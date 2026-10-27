import contextlib
import json
import sys
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from subprocess import CompletedProcess
from threading import Barrier, Event, Thread, current_thread
from typing import Literal, assert_never
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

from pywhales import DockerClient, Image, docker
from pywhales.client_config import Command
from pywhales.components.image.models import ImageInspectResult
from pywhales.components.image.streams import MultiImageStream
from pywhales.exceptions import (
    DockerException,
    NoSuchContainer,
    NoSuchImage,
    NoSuchNetwork,
    NoSuchVolume,
)
from pywhales.test_utils import get_all_jsons, random_name
from pywhales.utils import MappedProcessStream, ProcessStream, stream_stdout_and_stderr


def _assert_image_process_released(stream: ProcessStream) -> None:
    assert stream._process.poll() is not None
    assert all(not thread.is_alive() for thread in stream._threads)
    assert all(
        pipe.closed
        for pipe in (stream.stdin, stream._process.stdout, stream._process.stderr)
        if pipe is not None
    )


class _ImageCommands:
    def __init__(self, stack: contextlib.ExitStack) -> None:
        self.stack = stack
        self.client = DockerClient()
        self.client.client_config._client_call_with_path = ["docker-test"]
        self.children: dict[str, ProcessStream] = {}
        self.closed: dict[str, Event] = {}
        self.started: Queue[str] = Queue()
        self.output = True
        self.stdout = b"out0\nout1\ntail"
        self.stderr = b"err0\nerr1\n"

    def start(
        self, command: Command, *, buffer_bytes: int | None = None
    ) -> ProcessStream:
        name = str(command[-1])
        code = "import os, sys; "
        if self.output:
            code += f"os.write(1, {self.stdout!r}); os.write(2, {self.stderr!r}); "
        code += "status = int(input()); sys.exit(status)"
        child = self.stack.enter_context(
            stream_stdout_and_stderr(
                [sys.executable, "-c", code, name],
                pipe_stdin=True,
                buffer_bytes=buffer_bytes,
            )
        )
        self.children[name] = child
        closed = self.closed[name] = Event()
        close = child.close

        def close_and_signal() -> None:
            close()
            if all(not thread.is_alive() for thread in child._threads):
                closed.set()

        self.stack.enter_context(
            patch.object(child, "close", side_effect=close_and_signal)
        )
        self.started.put(name)
        return child

    def stream(
        self, operation: Literal["pull", "push"], names: list[str]
    ) -> MultiImageStream:
        method = (
            self.client.image.pull if operation == "pull" else self.client.image.push
        )
        result = method(names, stream_logs=True)
        assert isinstance(result, MultiImageStream)
        return result

    def finish(self, name: str, status: int = 0) -> None:
        stdin = self.children[name].stdin
        assert stdin is not None
        stdin.write(f"{status}\n".encode())
        stdin.flush()

    def release_children(self) -> None:
        for child in tuple(self.children.values()):
            child.close()


@pytest.fixture
def image_commands() -> Generator[_ImageCommands]:
    with contextlib.ExitStack() as stack:
        commands = _ImageCommands(stack)
        stack.enter_context(
            patch.object(commands.client.image, "inspect", return_value=[])
        )
        stack.enter_context(
            patch(
                "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr",
                side_effect=commands.start,
            )
        )
        yield commands


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("override", [None, 16])
def test_image_stream_new_operations_use_live_fallback_without_normalizing_override(
    operation: Literal["pull", "push"],
    multiple: bool,
    override: int | None,
    image_commands: _ImageCommands,
) -> None:
    config = image_commands.client.client_config
    config.stream_buffer_bytes = 32
    config.image_stream_buffer_bytes = override
    method = (
        image_commands.client.pull
        if operation == "pull"
        else image_commands.client.push
    )
    old_names = ["old-one", "old-two"] if multiple else ["old-one"]
    new_names = ["new-one", "new-two"] if multiple else ["new-one"]
    with contextlib.ExitStack() as stack:
        old = method(old_names, stream_logs=True)
        assert isinstance(old, (MappedProcessStream, MultiImageStream))
        stack.callback(old.close)
        config.stream_buffer_bytes = 64

        new = method(new_names, stream_logs=True)
        assert isinstance(new, (MappedProcessStream, MultiImageStream))
        stack.callback(new.close)
        next(old)
        next(new)
        for _ in [*old_names, *new_names]:
            image_commands.started.get(timeout=3)

        assert config.image_stream_buffer_bytes == override
        for names, expected in (
            (old_names, override or 32),
            (new_names, override or 64),
        ):
            assert all(
                image_commands.children[name]._buffer_bytes == expected
                for name in names
            )
        if isinstance(old, MultiImageStream) and isinstance(new, MultiImageStream):
            assert old._queue._limit == (override or 32)
            assert new._queue._limit == (override or 64)
    for child in image_commands.children.values():
        _assert_image_process_released(child)


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize(("budget", "override"), [(None, None), (16, None), (1, 32)])
def test_image_stream_lazy_queued_children_keep_public_operation_snapshot(
    operation: Literal["pull", "push"],
    budget: int | None,
    override: int | None,
    image_commands: _ImageCommands,
) -> None:
    config = image_commands.client.client_config
    config.stream_buffer_bytes = budget
    config.image_stream_buffer_bytes = override
    expected_budget = override if override is not None else budget
    image_commands.stdout = b"ok\n"
    image_commands.stderr = b""
    names = [f"image:{number}" for number in range(6)]
    stream = image_commands.stream(operation, names)
    try:
        assert image_commands.children == {}
        config.stream_buffer_bytes = 1
        config.image_stream_buffer_bytes = 1

        next(stream)
        for _ in range(4):
            image_commands.started.get(timeout=3)
        config.stream_buffer_bytes = 2
        config.image_stream_buffer_bytes = 2
        for name in names[:2]:
            image_commands.finish(name)
        for _ in range(2):
            image_commands.started.get(timeout=3)
        for name in names[2:]:
            image_commands.finish(name)

        assert len(list(stream)) == len(names) - 1
        assert stream._queue._limit == expected_budget
        assert all(
            child._buffer_bytes == expected_budget
            for child in image_commands.children.values()
        )
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        stream.close()


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("budget", [None, 16])
def test_push_stream_captures_budget_before_inspect_preflight(
    multiple: bool, budget: int | None, image_commands: _ImageCommands
) -> None:
    config = image_commands.client.client_config
    config.stream_buffer_bytes = budget

    def inspect_and_change_policy(names: list[str]) -> list[Image]:
        config.stream_buffer_bytes = 1
        config.image_stream_buffer_bytes = 1
        return []

    names = ["one", "two"] if multiple else ["one"]
    with patch.object(
        image_commands.client.image, "inspect", side_effect=inspect_and_change_policy
    ):
        stream = image_commands.client.push(names, stream_logs=True)
    assert isinstance(stream, (MappedProcessStream, MultiImageStream))
    try:
        next(stream)
        for _ in names:
            image_commands.started.get(timeout=3)
        assert all(
            child._buffer_bytes == budget for child in image_commands.children.values()
        )
        if isinstance(stream, MultiImageStream):
            assert stream._queue._limit == budget
    finally:
        stream.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("cancel", [False, True], ids=["drain", "cancel"])
def test_image_fallback_budget_backpressures_slow_consumer_and_closes_children(
    operation: Literal["pull", "push"],
    cancel: bool,
    image_commands: _ImageCommands,
) -> None:
    image_commands.client.client_config.stream_buffer_bytes = 8
    image_commands.stdout = b"abc\n" * 20
    image_commands.stderr = b""
    stream = image_commands.stream(operation, ["one", "two"])
    saturated = Event()
    wait = stream._queue.condition.wait

    def wait_and_signal(timeout: float | None = None) -> bool:
        saturated.set()
        return wait(timeout)

    try:
        with patch.object(stream._queue.condition, "wait", side_effect=wait_and_signal):
            stream._start()
            assert saturated.wait(timeout=3)
            for _ in range(2):
                image_commands.started.get(timeout=3)
            with stream._queue.condition:
                assert stream._queue._bytes == 8
        if cancel:
            stream.close()
            assert list(stream) == []
        else:
            for name in ("one", "two"):
                image_commands.finish(name)
            output = list(stream)
            for name in ("one", "two"):
                assert [line for tag, line in output if tag == name] == [b"abc\n"] * 20
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        stream.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("overflow", ["record", "stderr-history"])
def test_image_fallback_budget_rejects_mapped_stream_overflow(
    operation: Literal["pull", "push"],
    multiple: bool,
    overflow: Literal["record", "stderr-history"],
    image_commands: _ImageCommands,
) -> None:
    image_commands.client.client_config.stream_buffer_bytes = 4
    image_commands.stdout = b"oversized\n" if overflow == "record" else b""
    image_commands.stderr = b"a\nb\nc\n" if overflow == "stderr-history" else b""
    method = (
        image_commands.client.pull
        if operation == "pull"
        else image_commands.client.push
    )
    stream = method(["one", "two"] if multiple else "one", stream_logs=True)
    assert isinstance(stream, (MappedProcessStream, MultiImageStream))
    with stream, pytest.raises(BufferError):
        list(stream)
    for child in image_commands.children.values():
        _assert_image_process_released(child)


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("budget", [None, 16])
def test_image_snapshot_passes_exact_budget_to_real_shared_stream_boundary(
    operation: Literal["pull", "push"], budget: int | None
) -> None:
    client = DockerClient()
    config = client.client_config
    config.stream_buffer_bytes = budget
    config._client_call_with_path = [
        sys.executable,
        "-c",
        "import os; os.write(1, b'progress\\n')",
    ]
    method = client.pull if operation == "pull" else client.push
    names = [f"image:{number}" for number in range(6)]
    with patch.object(client.image, "inspect", return_value=[]):
        stream = method(names, stream_logs=True)
    assert isinstance(stream, MultiImageStream)
    config.stream_buffer_bytes = 1
    config.image_stream_buffer_bytes = 1

    with stream:
        assert sorted(stream) == [(name, b"progress\n") for name in names]


@pytest.mark.parametrize("budget", [4, 64])
def test_path_image_load_uses_general_budget_instead_of_image_override(
    budget: int,
) -> None:
    client = DockerClient()
    client.client_config.stream_buffer_bytes = budget
    client.client_config.image_stream_buffer_bytes = 1
    client.client_config._client_call_with_path = [
        sys.executable,
        "-c",
        "print('Loaded image: sample', flush=True)",
    ]

    if budget == 4:
        with pytest.raises(BufferError):
            client.image.load("unused-archive.tar", quiet=True)
    else:
        assert client.image.load("unused-archive.tar", quiet=True) == ["sample"]


@pytest.mark.parametrize("cancel", [False, True], ids=["drain", "saturated-close"])
def test_multiple_image_budget_accounts_payload_while_producers_are_blocked(
    cancel: bool, image_commands: _ImageCommands
) -> None:
    image_commands.stdout = b"a\nbbb\nccc\n" * 10
    image_commands.stderr = b""
    names = ["long-image-tag:" + "a" * 100, "long-image-tag:" + "b" * 100]
    advanced: dict[str, list[int]] = {name: [] for name in names}

    def start(name: str) -> MappedProcessStream[tuple[str, bytes]]:
        child = image_commands.start(Command([name]))

        def mapped() -> Generator[tuple[str, bytes]]:
            for _, line in child:
                with stream._queue.condition:
                    advanced[name].append(len(line))
                yield name, line

        return MappedProcessStream(child, mapped())

    stream = MultiImageStream(names, start, buffer_bytes=10)
    blocked: set[str] = set()
    saturated = Event()
    wait = stream._queue.condition.wait

    def wait_and_signal(timeout: float | None = None) -> bool:
        blocked.add(current_thread().name)
        if len(blocked) == 2:
            saturated.set()
        return wait(timeout)

    try:
        with patch.object(stream._queue.condition, "wait", side_effect=wait_and_signal):
            stream._start()
            assert saturated.wait(timeout=3)
            with stream._queue.condition:
                queued = tuple(stream._queue._items)
                retained_bytes = sum(
                    len(item[1]) for item in queued if item is not None
                )
                assert stream._queue._bytes == retained_bytes
                assert 0 < retained_bytes <= 10
                assert all(item is not None and len(item[1]) <= 10 for item in queued)
                assert sum(map(len, advanced.values())) == len(queued) + 2
                in_flight = sum(sizes[-1] for sizes in advanced.values())
                assert sum(map(sum, advanced.values())) == retained_bytes + in_flight
                assert all(sizes[-1] <= 10 for sizes in advanced.values())
        for _ in names:
            image_commands.started.get(timeout=3)
        if cancel:
            closers = [Thread(target=stream.close, daemon=True) for _ in range(2)]
            for closer in closers:
                closer.start()
            for closer in closers:
                closer.join(timeout=3)
                assert not closer.is_alive()
            assert list(stream) == []
            assert stream._queue._bytes == 0
        else:
            for name in names:
                image_commands.finish(name)
            output = list(stream)
            for name in names:
                assert [line for tag, line in output if tag == name] == [
                    b"a\n",
                    b"bbb\n",
                    b"ccc\n",
                ] * 10
        executor = stream._executor
        assert executor is not None
        assert all(not worker.is_alive() for worker in executor._threads)
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        stream.close()
        image_commands.release_children()


@pytest.mark.parametrize("failure", ["mapper", "oversize", "child"])
def test_multiple_image_failure_wakes_saturated_sibling_producer(
    failure: Literal["mapper", "oversize", "child"], image_commands: _ImageCommands
) -> None:
    image_commands.stdout = b"abc\n" * 10
    image_commands.stderr = b""
    saturated = Event()
    error = RuntimeError("mapper failed")

    def start(name: str) -> MappedProcessStream[tuple[str, bytes]]:
        if name == "sibling":
            child = image_commands.start(Command([name]))
            return MappedProcessStream(child, ((name, line) for _, line in child))
        assert saturated.wait(timeout=3)
        with patch.object(image_commands, "output", False):
            child = image_commands.start(Command([name]))

        def mapped() -> Generator[tuple[str, bytes]]:
            match failure:
                case "mapper":
                    raise error
                case "oversize":
                    yield name, b"oversized"
                case "child":
                    image_commands.finish(name, status=7)
                    yield from ((name, line) for _, line in child)
                case unreachable:
                    assert_never(unreachable)

        return MappedProcessStream(child, mapped())

    stream = MultiImageStream(["sibling", "failure"], start, buffer_bytes=4)
    wait = stream._queue.condition.wait

    def wait_and_signal(timeout: float | None = None) -> bool:
        saturated.set()
        return wait(timeout)

    try:
        with patch.object(stream._queue.condition, "wait", side_effect=wait_and_signal):
            stream._start()
            assert saturated.wait(timeout=3)
            for _ in range(2):
                image_commands.started.get(timeout=3)
            for name in ("sibling", "failure"):
                assert image_commands.closed[name].wait(timeout=3)
        executor = stream._executor
        assert executor is not None
        for worker in executor._threads:
            worker.join(timeout=3)
            assert not worker.is_alive()
        expected = {
            "mapper": RuntimeError,
            "oversize": BufferError,
            "child": DockerException,
        }[failure]
        with pytest.raises(expected) as raised:
            next(stream)
        if failure == "mapper":
            assert raised.value is error
        assert list(stream) == []
        assert stream._queue._bytes == 0
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        stream.close()
        image_commands.release_children()


@pytest.mark.parametrize("failure_before_registration", [False, True])
@pytest.mark.parametrize("failure", ["buffer", "exit"])
def test_background_child_failure_cancels_group_without_consumer_drain(
    failure_before_registration: bool,
    failure: Literal["buffer", "exit"],
) -> None:
    children: dict[str, ProcessStream] = {}
    blocked: set[str] = set()
    ready = Event()
    sibling_saturated = Event()
    saturated = Event()
    terminal_code = (
        "os._exit(7)" if failure == "exit" else "os.write(2, b'x' * 17); input()"
    )

    def start(name: str) -> MappedProcessStream[tuple[str, bytes]]:
        child = stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                f"import os; os.write(1, b'a\\n' * 32); input(); {terminal_code}",
            ],
            pipe_stdin=True,
            buffer_bytes=16,
        )
        children[name] = child
        if len(children) == 2:
            ready.set()
        if failure_before_registration and name == "bad":
            assert sibling_saturated.wait(timeout=3)
            assert child.stdin is not None
            child.stdin.write(b"fail\n")
            child.stdin.flush()
            if failure == "exit":
                assert child._process.wait(timeout=3) == 7
            else:
                assert child._released.wait(timeout=3)

        def mapped() -> Generator[tuple[str, bytes]]:
            if failure_before_registration and name == "bad":
                yield name, b"a\n"
            yield from ((name, line) for _, line in child)

        return MappedProcessStream(child, mapped())

    stream = MultiImageStream(["sibling", "bad"], start, buffer_bytes=16)
    wait = stream._queue.condition.wait

    def wait_and_signal(timeout: float | None = None) -> bool:
        blocked.add(current_thread().name)
        sibling_saturated.set()
        if len(blocked) == 2:
            saturated.set()
        return wait(timeout)

    try:
        with patch.object(stream._queue.condition, "wait", side_effect=wait_and_signal):
            stream._start()
            assert ready.wait(timeout=3)
            if not failure_before_registration:
                assert saturated.wait(timeout=3)
                with stream._queue.condition:
                    assert stream._queue._bytes == 16
                bad = children["bad"]
                with bad._queue.condition:
                    assert bad._queue.condition.wait_for(
                        lambda: (
                            bad._queue._bytes == 16 and bool(bad._inflight["stdout"])
                        ),
                        timeout=3,
                    )
                assert bad.stdin is not None
                bad.stdin.write(b"fail\n")
                bad.stdin.flush()
            for child in tuple(children.values()):
                assert child._released.wait(timeout=3)
            executor = stream._executor
            assert executor is not None
            for worker in executor._threads:
                worker.join(timeout=3)
                assert not worker.is_alive()
        assert set(children) == {"bad", "sibling"}
        error = stream._error
        expected = DockerException if failure == "exit" else BufferError
        assert isinstance(error, expected)
        with pytest.raises(expected) as raised:
            next(stream)
        assert raised.value is error
        if failure == "exit":
            assert isinstance(error, DockerException)
            assert error.return_code == 7
        assert list(stream) == []
        assert stream._queue._bytes == 0
        for child in children.values():
            _assert_image_process_released(child)
            assert not child._watchdog.is_alive()
    finally:
        stream.close()
        for child in children.values():
            child.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("multiple", [False, True])
def test_image_stream_factories_use_selected_client_budget_and_root_alias(
    operation: Literal["pull", "push"], multiple: bool, image_commands: _ImageCommands
) -> None:
    image_commands.client.client_config.image_stream_buffer_bytes = 16
    method = (
        image_commands.client.pull
        if operation == "pull"
        else image_commands.client.push
    )
    names = ["one", "two", "one"] if multiple else ["one", "one"]
    start = image_commands.start
    selected: list[int | None] = []

    def record_budget(
        command: Command, *, buffer_bytes: int | None = None
    ) -> ProcessStream:
        selected.append(buffer_bytes)
        return start(command, buffer_bytes=buffer_bytes)

    with patch(
        "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr",
        side_effect=record_budget,
    ):
        stream = method(names, stream_logs=True)
        assert isinstance(stream, (MappedProcessStream, MultiImageStream))
        try:
            next(stream)
            for _ in range(2 if multiple else 1):
                image_commands.started.get(timeout=3)
            assert selected == ([16, 16] if multiple else [16])
            if isinstance(stream, MultiImageStream):
                assert stream._queue._limit == 16
        finally:
            stream.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_image_stream_budgets_do_not_leak_to_unbounded_client(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    image_commands.stdout = b"abcdef\n"
    image_commands.stderr = b""
    image_commands.client.client_config.image_stream_buffer_bytes = 4
    unbounded_client = DockerClient()
    unbounded_client.client_config._client_call_with_path = ["docker-test"]
    limited_method = (
        image_commands.client.pull
        if operation == "pull"
        else image_commands.client.push
    )
    unbounded_method = (
        unbounded_client.pull if operation == "pull" else unbounded_client.push
    )

    with patch.object(unbounded_client.image, "inspect", return_value=[]):
        limited = limited_method("limited", stream_logs=True)
        unbounded = unbounded_method("unbounded", stream_logs=True)
        assert isinstance(limited, MappedProcessStream)
        assert isinstance(unbounded, MappedProcessStream)
        try:
            image_commands.finish("unbounded")
            with pytest.raises(BufferError):
                list(limited)
            assert list(unbounded) == [("unbounded", b"abcdef\n")]
        finally:
            limited.close()
            unbounded.close()
    for child in image_commands.children.values():
        _assert_image_process_released(child)


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("budget", [None, 16])
def test_multiple_image_stream_cancels_active_and_queued_commands(
    operation: Literal["pull", "push"],
    image_commands: _ImageCommands,
    budget: int | None,
) -> None:
    image_commands.client.client_config.image_stream_buffer_bytes = budget
    names = [f"image:{number}" for number in range(8)]
    stream = image_commands.stream(operation, names)
    closer = Thread(target=stream.close, daemon=True)
    try:
        next(stream)
        started = [image_commands.started.get(timeout=3) for _ in range(4)]
        assert set(started) == set(names[:4])
        executor = stream._executor
        assert executor is not None
        assert len(executor._threads) == 4

        closer.start()
        closer.join(timeout=3)

        assert not closer.is_alive()
        assert all(not worker.is_alive() for worker in executor._threads)
        assert set(image_commands.children) == set(names[:4])
        assert list(stream) == []
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        image_commands.release_children()
        stream.close()
        if closer.ident is not None:
            closer.join(timeout=3)


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_multiple_image_stream_rejects_running_but_unadmitted_worker_after_close(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    names = [f"image:{number}" for number in range(6)]
    stream = image_commands.stream(operation, names)
    running = Event()
    admit = Event()
    consume = stream._consume

    def gated_consume(name: str) -> None:
        if name == names[4]:
            running.set()
            assert admit.wait(timeout=3)
        consume(name)

    closer = Thread(target=stream.close, daemon=True)
    with patch.object(stream, "_consume", side_effect=gated_consume):
        try:
            next(stream)
            for _ in range(4):
                image_commands.started.get(timeout=3)
            image_commands.finish(names[0])
            assert running.wait(timeout=3)

            closer.start()
            assert image_commands.closed[names[1]].wait(timeout=3)
            admit.set()
            closer.join(timeout=3)

            assert not closer.is_alive()
            assert set(image_commands.children) == set(names[:4])
            for child in image_commands.children.values():
                _assert_image_process_released(child)
        finally:
            admit.set()
            image_commands.release_children()
            stream.close()
            if closer.ident is not None:
                closer.join(timeout=3)


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("budget", [None, 16])
def test_multiple_image_stream_close_wakes_blocked_consumer(
    operation: Literal["pull", "push"],
    image_commands: _ImageCommands,
    budget: int | None,
) -> None:
    image_commands.client.client_config.image_stream_buffer_bytes = budget
    image_commands.output = False
    stream = image_commands.stream(operation, ["one", "two"])
    waiting = Event()
    finished = Event()
    output: list[tuple[str, bytes]] = []
    get = stream._queue.get

    def get_and_signal() -> tuple[str, bytes] | None:
        waiting.set()
        return get()

    def consume() -> None:
        try:
            output.extend(stream)
        finally:
            finished.set()

    consumer = Thread(target=consume, daemon=True)
    with patch.object(stream._queue, "get", side_effect=get_and_signal):
        try:
            consumer.start()
            assert waiting.wait(timeout=3)
            for _ in range(2):
                image_commands.started.get(timeout=3)

            stream.close()

            assert finished.wait(timeout=3)
            consumer.join(timeout=3)
            assert output == []
            for child in image_commands.children.values():
                _assert_image_process_released(child)
        finally:
            image_commands.release_children()
            stream.close()
            consumer.join(timeout=3)


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("explicit_close", [False, True])
def test_multiple_image_failure_cancels_siblings_while_consumer_is_paused(
    operation: Literal["pull", "push"],
    explicit_close: bool,
    image_commands: _ImageCommands,
) -> None:
    names = [f"image:{number}" for number in range(6)]
    stream = image_commands.stream(operation, names)
    try:
        next(stream)
        for _ in range(4):
            image_commands.started.get(timeout=3)

        image_commands.finish(names[0], status=7)

        for name in names[:4]:
            assert image_commands.closed[name].wait(timeout=3)
        executor = stream._executor
        assert executor is not None
        for worker in executor._threads:
            worker.join(timeout=3)
            assert not worker.is_alive()
        assert stream._remaining == 0
        error = stream._error
        assert isinstance(error, DockerException)
        assert error.return_code == 7
        assert set(image_commands.children) == set(names[:4])
        if explicit_close:
            stream.close()
        with pytest.raises(DockerException) as raised:
            next(stream)
        assert raised.value is error
        assert list(stream) == []
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        image_commands.release_children()
        stream.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_multiple_image_stream_keeps_first_original_error(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    stream = image_commands.stream(operation, ["one", "two"])
    failures = Barrier(2)
    first_recorded = Event()
    second_recorded = Event()
    errors: dict[str, DockerException] = {}
    record_error = stream._record_error

    def ordered_failures(error: BaseException) -> None:
        assert isinstance(error, DockerException)
        name = error.docker_command[-1]
        if name in errors:
            record_error(error)
            return
        errors[name] = error
        failures.wait(timeout=3)
        if name == "two":
            assert first_recorded.wait(timeout=3)
        record_error(error)
        if name == "one":
            first_recorded.set()
        else:
            second_recorded.set()

    with patch.object(stream, "_record_error", side_effect=ordered_failures):
        try:
            next(stream)
            for _ in range(2):
                image_commands.started.get(timeout=3)
            image_commands.finish("one", status=7)
            image_commands.finish("two", status=9)
            assert second_recorded.wait(timeout=3)

            stream.close()

            with pytest.raises(DockerException) as raised:
                next(stream)
            assert set(errors) == {"one", "two"}
            assert raised.value is errors["one"]
            assert raised.value.return_code == 7
            assert list(stream) == []
            for child in image_commands.children.values():
                _assert_image_process_released(child)
        finally:
            failures.abort()
            first_recorded.set()
            image_commands.release_children()
            stream.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_multiple_image_stream_exhaustion_preserves_tags_bytes_and_pipe_order(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    names = [f"image:{number}" for number in range(6)]
    stream = image_commands.stream(operation, [*names, names[0], names[-1]])
    try:
        output = [next(stream)]
        for _ in range(4):
            image_commands.finish(image_commands.started.get(timeout=3))
        for _ in range(2):
            image_commands.finish(image_commands.started.get(timeout=3))

        output.extend(stream)

        assert set(image_commands.children) == set(names)
        for name in names:
            lines = [line for tag, line in output if tag == name]
            assert [line for line in lines if not line.startswith(b"err")] == [
                b"out0\n",
                b"out1\n",
                b"tail",
            ]
            assert [line for line in lines if line.startswith(b"err")] == [
                b"err0\n",
                b"err1\n",
            ]
        assert len(output) == 5 * len(names)
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        image_commands.release_children()
        stream.close()


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("budget", [None, 16])
def test_multiple_image_stream_concurrent_close_waits_for_cleanup(
    operation: Literal["pull", "push"],
    image_commands: _ImageCommands,
    budget: int | None,
) -> None:
    image_commands.client.client_config.image_stream_buffer_bytes = budget
    stream = image_commands.stream(operation, ["one", "two"])
    entered = Event()
    release = Event()
    waiting = Event()
    finished = Event()
    first = Thread(target=stream.close, daemon=True)

    def close_again() -> None:
        stream.close()
        finished.set()

    second = Thread(target=close_again, daemon=True)
    try:
        next(stream)
        for _ in range(2):
            image_commands.started.get(timeout=3)
        child = image_commands.children["one"]
        close = child.close
        wait = stream._cleanup_done.wait

        def gated_close() -> None:
            entered.set()
            assert release.wait(timeout=3)
            close()

        def wait_and_signal() -> bool:
            waiting.set()
            return wait()

        with (
            patch.object(child, "close", side_effect=gated_close),
            patch.object(stream._cleanup_done, "wait", side_effect=wait_and_signal),
        ):
            first.start()
            assert entered.wait(timeout=3)
            second.start()
            assert waiting.wait(timeout=3)
            assert not finished.is_set()

            release.set()
            first.join(timeout=3)
            second.join(timeout=3)

            assert not first.is_alive()
            assert not second.is_alive()
            assert finished.is_set()
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        release.set()
        image_commands.release_children()
        stream.close()
        for closer in (first, second):
            if closer.ident is not None:
                closer.join(timeout=3)


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_multiple_image_stream_child_startup_failure_closes_sibling(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    error = OSError("cannot start image CLI")
    stream = image_commands.stream(operation, ["one", "two", "three"])
    start = image_commands.start

    def start_or_fail(
        command: Command, *, buffer_bytes: int | None = None
    ) -> ProcessStream:
        if command[-1] == "two":
            raise error
        return start(command, buffer_bytes=buffer_bytes)

    with patch(
        "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr",
        side_effect=start_or_fail,
    ):
        try:
            with pytest.raises(OSError) as raised:
                list(stream)

            assert raised.value is error
            assert "two" not in image_commands.children
            for child in image_commands.children.values():
                _assert_image_process_released(child)
        finally:
            image_commands.release_children()
            stream.close()


@pytest.mark.parametrize("phase", ["constructor", "submission"])
def test_multiple_image_stream_executor_startup_failure_releases_resources(
    phase: Literal["constructor", "submission"], image_commands: _ImageCommands
) -> None:
    stream = image_commands.stream("pull", ["one", "two"])
    error = RuntimeError("executor startup failed")
    submit = ThreadPoolExecutor.submit
    submissions = 0

    def submit_or_fail(
        executor: ThreadPoolExecutor, callback: Callable[[str], None], name: str
    ) -> Future[None]:
        nonlocal submissions
        submissions += 1
        if submissions == 2:
            raise error
        return submit(executor, callback, name)

    target = (
        patch("pywhales.components.image.streams.ThreadPoolExecutor", side_effect=error)
        if phase == "constructor"
        else patch.object(
            ThreadPoolExecutor, "submit", autospec=True, side_effect=submit_or_fail
        )
    )
    with target, pytest.raises(RuntimeError) as raised:
        next(stream)

    assert raised.value is error
    stream.close()
    assert list(stream) == []
    for child in image_commands.children.values():
        _assert_image_process_released(child)


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_multiple_image_context_preserves_body_exception_after_worker_failure(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    stream = image_commands.stream(operation, ["one", "two"])
    body_error = RuntimeError("consumer body failed")
    try:
        next(stream)
        for _ in range(2):
            image_commands.started.get(timeout=3)
        image_commands.finish("one", status=7)
        assert image_commands.closed["two"].wait(timeout=3)
        worker_error = stream._error

        with pytest.raises(RuntimeError) as raised, stream:
            raise body_error

        assert raised.value is body_error
        with pytest.raises(DockerException) as failure:
            next(stream)
        assert failure.value is worker_error
        for child in image_commands.children.values():
            _assert_image_process_released(child)
    finally:
        image_commands.release_children()
        stream.close()


@pytest.mark.parametrize("stream_logs", [False, True])
def test_empty_image_operations_keep_existing_result_shapes(
    stream_logs: bool, image_commands: _ImageCommands
) -> None:
    result = image_commands.client.image.pull([], stream_logs=stream_logs)

    assert isinstance(result, Generator) if stream_logs else isinstance(result, list)
    assert list(result) == []
    assert image_commands.client.image.push([], stream_logs=stream_logs) is None
    assert image_commands.children == {}


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_duplicate_single_image_stream_keeps_eager_owned_child(
    operation: Literal["pull", "push"], image_commands: _ImageCommands
) -> None:
    method = (
        image_commands.client.image.pull
        if operation == "pull"
        else image_commands.client.image.push
    )
    stream = method(["one", "one"], stream_logs=True)
    assert isinstance(stream, MappedProcessStream)

    stream.close()

    assert list(image_commands.children) == ["one"]
    _assert_image_process_released(image_commands.children["one"])


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("multiple", [False, True])
def test_nonstream_image_operations_keep_eager_results_and_deduplication(
    operation: Literal["pull", "push"], multiple: bool, image_commands: _ImageCommands
) -> None:
    names = ["one", "two", "one"] if multiple else "one"
    method = (
        image_commands.client.image.pull
        if operation == "pull"
        else image_commands.client.image.push
    )
    with patch(
        "pywhales.components.image.cli_wrapper.run",
        return_value=b'[{"Id": "sha256:test"}]',
    ) as run:
        result = method(names)

    assert image_commands.children == {}
    assert sorted(
        call.args[0][-1] for call in run.call_args_list if call.args[0][2] == operation
    ) == (["one", "two"] if multiple else ["one"])
    if operation == "push":
        assert result is None
    elif multiple:
        assert isinstance(result, list)
        assert all(isinstance(image, Image) for image in result)
        assert len(result) == 2
    else:
        assert isinstance(result, Image)


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_multiple_image_stream_has_lazy_owned_lifecycle(
    operation: Literal["pull", "push"],
) -> None:
    client = DockerClient()
    with (
        patch.object(client.image, "inspect", return_value=[]),
        patch(
            "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr"
        ) as start,
    ):
        method = client.image.pull if operation == "pull" else client.image.push
        result = method(["one", "two"], stream_logs=True)
        assert result is not None
        assert isinstance(result, MultiImageStream)
        with result as entered:
            assert entered is result
        result.close()
        assert list(result) == []
        start.assert_not_called()


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("consume", [False, True])
def test_single_image_stream_owns_idle_child(
    operation: Literal["pull", "push"], consume: bool
) -> None:
    client = DockerClient()
    child = stream_stdout_and_stderr(
        [sys.executable, "-c", "print('ready', flush=True); input()"], pipe_stdin=True
    )
    with (
        child,
        patch.object(client.image, "inspect", return_value=[]),
        patch(
            "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr",
            return_value=child,
        ),
    ):
        method = client.image.pull if operation == "pull" else client.image.push
        result = method("sample:v1", stream_logs=True)
        assert isinstance(result, MappedProcessStream)
        if consume:
            assert next(result) == ("sample:v1", b"ready\n")

        result.close()
        result.close()

        assert list(result) == []
        _assert_image_process_released(child)


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_single_image_stream_context_preserves_body_error(
    operation: Literal["pull", "push"],
) -> None:
    client = DockerClient()
    error = RuntimeError("consumer failed")
    with (
        stream_stdout_and_stderr(
            [sys.executable, "-c", "input()"], pipe_stdin=True
        ) as child,
        patch.object(client.image, "inspect", return_value=[]),
        patch(
            "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr",
            return_value=child,
        ),
    ):
        method = client.image.pull if operation == "pull" else client.image.push
        result = method("sample:v1", stream_logs=True)
        assert isinstance(result, MappedProcessStream)

        with pytest.raises(RuntimeError) as raised, result as entered:
            assert entered is result
            raise error

        assert raised.value is error
        _assert_image_process_released(child)


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_single_image_stream_exhaustion_keeps_bytes_and_command_failure(
    operation: Literal["pull", "push"], exit_code: int
) -> None:
    client = DockerClient()
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                "import os, sys; os.write(1, b'first\\nsecond\\ntail'); "
                "os.write(2, b'No such image: missing\\n'); sys.exit(int(sys.argv[1]))",
                str(exit_code),
            ]
        ) as child,
        patch.object(client.image, "inspect", return_value=[]),
        patch(
            "pywhales.components.image.cli_wrapper.stream_stdout_and_stderr",
            return_value=child,
        ),
    ):
        method = client.image.pull if operation == "pull" else client.image.push
        result = method("sample:v1", stream_logs=True)
        assert isinstance(result, MappedProcessStream)
        output: list[tuple[str, bytes]] = []

        if exit_code:
            with pytest.raises(NoSuchImage) as raised:
                output.extend(result)
            assert raised.value.return_code == exit_code
        else:
            output.extend(result)

        assert all(tag == "sample:v1" for tag, _ in output)
        assert [
            line for _, line in output if not line.startswith(b"No such image")
        ] == [
            b"first\n",
            b"second\n",
            b"tail",
        ]
        assert ("sample:v1", b"No such image: missing\n") in output
        _assert_image_process_released(child)


def test_batch_inspect_image_aliases_and_typed_snapshot() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    identity = "sha256:" + "a" * 64
    snapshot = {"Id": identity, "RepoTags": ["sample:latest", "other:v1"], "Size": 42}
    references = [
        "sample",
        "other:v1",
        "a" * 12,
        identity,
        "docker.io/library/sample:latest",
    ]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess(
                [], 0, json.dumps([snapshot] * len(references)).encode(), b""
            ),
        ) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        images = client.image.inspect(iter(references))
        assert [image.id for image in images] == [identity] * len(references)
        assert all(image.size == 42 for image in images)
        assert all(
            isinstance(image._inspect_result, ImageInspectResult) for image in images
        )
        assert command.call_count == 1


@pytest.mark.parametrize(
    "reference",
    [
        "sample@sha256:" + "b" * 64,
        "sample:latest@sha256:" + "b" * 64,
    ],
    ids=["repo-digest", "tagged-digest"],
)
def test_batch_inspect_image_digest_and_local_aliases(reference: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    identity = "sha256:" + "a" * 64
    snapshot = {
        "Id": identity,
        "RepoDigests": ["docker.io/library/sample@sha256:" + "b" * 64],
    }
    with patch(
        "subprocess.run",
        return_value=CompletedProcess([], 0, json.dumps([snapshot]).encode(), b""),
    ) as command:
        images = client.image.inspect([reference])
    assert [image.id for image in images] == [identity]
    assert command.call_count == 1


def _repo_tags(image: Image) -> list[str]:
    tags = image.repo_tags
    assert tags is not None
    return tags


@pytest.mark.parametrize("missing", [False, True], ids=["removed", "already-missing"])
def test_image_cleanup_forces_removal(missing: bool) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    image = Image(client.client_config, "resource", is_immutable_id=True)
    response = CompletedProcess(
        [], 1 if missing else 0, b"", b"No such image: resource" if missing else b""
    )
    with patch("subprocess.run", return_value=response) as command, image:
        pass
    assert command.call_args.args[0] == [
        "docker-test",
        "image",
        "rm",
        "--force",
        "resource",
    ]
    assert command.call_count == 1
    if missing:
        with patch("subprocess.run", return_value=response), pytest.raises(NoSuchImage):
            image.remove(force=True)


@pytest.mark.parametrize(
    ("stderr", "error_type"),
    [
        pytest.param(b"permission denied", DockerException, id="command-failure"),
        pytest.param(b"No such container: resource", NoSuchContainer, id="container"),
        pytest.param(b"network resource not found", NoSuchNetwork, id="network"),
        pytest.param(b"No such volume: resource", NoSuchVolume, id="volume"),
    ],
)
def test_image_cleanup_propagates_unrelated_cli_failures(
    stderr: bytes, error_type: type[DockerException]
) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    image = Image(client.client_config, "resource", is_immutable_id=True)
    with (
        patch(
            "subprocess.run", return_value=CompletedProcess([], 17, b"output", stderr)
        ) as command,
        pytest.raises(DockerException) as raised,
        image,
    ):
        pass

    expected = ["docker-test", "image", "rm", "--force", "resource"]
    command.assert_called_once()
    assert command.call_args.args[0] == expected
    assert type(raised.value) is error_type
    assert raised.value.docker_command == expected
    assert raised.value.return_code == 17
    assert raised.value.stdout == "output"
    assert raised.value.stderr == stderr.decode()


@pytest.mark.parametrize("json_file", get_all_jsons("images"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    ImageInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


def test_image_repr(ctr_client: DockerClient) -> None:
    ctr_client.image.pull("busybox:1", quiet=True)
    ctr_client.image.pull("busybox:1.32", quiet=True)
    assert "busybox:1" in repr(ctr_client.image.list())
    assert "busybox:1.32" in repr(ctr_client.image.list())
    ctr_client.image.remove(["busybox:1", "busybox:1.32"])


def test_image_remove(ctr_client: DockerClient) -> None:
    ctr_client.image.pull("busybox:1", quiet=True)
    ctr_client.image.pull("busybox:1.32", quiet=True)
    ctr_client.image.remove(["busybox:1", "busybox:1.32"])


def test_save_load(ctr_client: DockerClient, tmp_path: Path) -> None:
    tar_file = tmp_path / "dodo.tar"
    image_name = "busybox:1"
    image = ctr_client.image.pull(image_name, quiet=True)
    image_tag = next(tag for tag in _repo_tags(image) if tag.endswith(image_name))
    ctr_client.image.save(image_name, output=tar_file)
    ctr_client.image.remove(image_name, force=True)
    assert not ctr_client.image.exists(image_name)
    loaded_tags = ctr_client.image.load(input=tar_file)
    assert loaded_tags == [image_tag]
    assert ctr_client.image.exists(image_name)


def test_save_iterator_bytes(ctr_client: DockerClient) -> None:
    ctr_client.image.pull("busybox:1", quiet=True)
    iterator = ctr_client.image.save("busybox:1")

    chunk_count = 0
    for chunk_count, my_bytes in enumerate(iterator, start=1):
        if chunk_count == 1:
            assert len(my_bytes) != 0

    assert chunk_count > 1


def test_save_iterator_bytes_and_load(ctr_client: DockerClient) -> None:
    image_name = "busybox:1"
    image = ctr_client.image.pull(image_name, quiet=True)
    image_tag = next(tag for tag in _repo_tags(image) if tag.endswith(image_name))
    iterator = ctr_client.image.save(image_name)
    my_tar_as_bytes = b"".join(iterator)
    image.remove(force=True)
    assert ctr_client.image.load(my_tar_as_bytes) == [image_tag]
    assert ctr_client.image.exists(image_name)


def test_save_iterator_bytes_and_load_from_iterator(ctr_client: DockerClient) -> None:
    image_name = "busybox:1"
    image = ctr_client.image.pull(image_name, quiet=True)
    image_tag = next(tag for tag in _repo_tags(image) if tag.endswith(image_name))
    iterator = ctr_client.image.save(image_name)
    # Cannot remove the image here because the save may still be in progress!
    assert ctr_client.image.load(iterator) == [image_tag]


def test_save_iterator_bytes_and_load_from_iterator_list_of_images(
    ctr_client: DockerClient,
) -> None:
    image_names = ["busybox:1", "hello-world:latest"]
    images = ctr_client.image.pull(image_names, quiet=True)
    image_tags = [
        tag
        for image in images
        for tag in _repo_tags(image)
        if any(tag.endswith(n) for n in image_names)
    ]
    iterator = ctr_client.image.save(image_names)
    # Cannot remove the image here because the save may still be in progress!
    assert set(ctr_client.image.load(iterator)) == set(image_tags)


def test_list_filters(ctr_client: DockerClient) -> None:
    ctr_client.pull(["hello-world", "busybox"])
    images_listed = ctr_client.image.list(filters=[("reference", "hello-world")])
    tags = {tag.split("/")[-1] for image in images_listed for tag in _repo_tags(image)}
    assert tags == {"hello-world:latest"}


def test_list_filters_old_signature(docker_client: DockerClient) -> None:
    """Check backward compatibility of the DockerClient.image.list() API."""
    docker_client.pull(["hello-world", "busybox"])
    expected_warning = (
        r"Passing filters as a mapping is deprecated, replace with an iterable "
        r"of tuples instead, as so:\n"
        r"filters=\[\(.*\)\]"
    )
    with pytest.warns(DeprecationWarning, match=expected_warning):
        images_listed = docker_client.image.list(filters={"reference": "hello-world"})
    tags = {tag.split("/")[-1] for image in images_listed for tag in _repo_tags(image)}
    assert tags == {"hello-world:latest"}


def test_list_filter_by_name(ctr_client: DockerClient) -> None:
    ctr_client.pull(["hello-world", "busybox"])
    images_listed = ctr_client.image.list("hello-world")
    tags = {tag.split("/")[-1] for image in images_listed for tag in _repo_tags(image)}
    assert tags == {"hello-world:latest"}


def test_image_list(ctr_client: DockerClient) -> None:
    image_list = ctr_client.image.list()
    all_ids = [x.id for x in image_list]
    all_ids_uniquified = set(all_ids)
    assert len(set(all_ids_uniquified)) == len(image_list)


def test_image_list_tags(ctr_client: DockerClient) -> None:
    ctr_client.image.pull("busybox:1", quiet=True)
    all_images = ctr_client.image.list()
    assert "busybox:1" in {
        tag.split("/")[-1] for image in all_images for tag in _repo_tags(image)
    }


def test_pull_validation_error(ctr_client: DockerClient) -> None:
    with pytest.raises(ValueError) as err:
        ctr_client.image.pull(ANY, quiet=True, stream_logs=True)
    assert (
        "It's not possible to have stream_logs=True and quiet=True at the same time"
        in str(err.value)
    )


def test_pull_nothing(ctr_client: DockerClient) -> None:
    result = ctr_client.image.pull([], quiet=True)
    assert isinstance(result, list)
    assert len(result) == 0


def test_pull_nothing_stream_logs(ctr_client: DockerClient) -> None:
    result = ctr_client.image.pull([], stream_logs=True)
    assert isinstance(result, Generator)
    assert len(list(result)) == 0


def test_pull_duplicate_images(ctr_client: DockerClient) -> None:
    with contextlib.suppress(DockerException):
        ctr_client.image.remove("busybox:1", force=True)
    images = ["busybox:1", "busybox:1", "busybox:1"]
    with (
        patch("pywhales.components.image.cli_wrapper.Image") as image,
        patch.object(ctr_client.image, "_pull_single_tag") as pull_single_tag,
        patch.object(ctr_client.image, "_pull_multiple_tags") as pull_multiple_tags,
    ):
        image.return_value = MagicMock()
        pull_single_tag.return_value = None
        ctr_client.image.pull(images, quiet=True)
        pull_multiple_tags.assert_not_called()
        pull_single_tag.assert_called_once()


def test_pull_not_quiet(ctr_client: DockerClient) -> None:
    with contextlib.suppress(DockerException):
        ctr_client.image.remove("busybox:1", force=True)
    image = ctr_client.image.pull("busybox:1")
    assert "busybox:1" in {tag.split("/")[-1] for tag in _repo_tags(image)}


def test_pull_stream_logs(ctr_client: DockerClient) -> None:
    with contextlib.suppress(DockerException):
        ctr_client.image.remove("busybox:1", force=True)
    logs = ctr_client.image.pull("busybox:1", stream_logs=True)
    lines = [line.decode().strip() for _, line in logs]
    assert len(lines) > 0
    assert any("busybox:1" in line for line in lines)


def test_pull_not_quiet_multiple_images(ctr_client: DockerClient) -> None:
    images_names = ["busybox:1", "hello-world:latest"]
    with contextlib.suppress(DockerException):
        ctr_client.image.remove(images_names, force=True)
    images = ctr_client.pull(images_names)
    for image_name, image in zip(images_names, images, strict=True):
        assert image_name in _repo_tags(image)


def test_pull_not_quiet_multiple_images_break(ctr_client: DockerClient) -> None:
    images_names = ["busybox:1", "hellstuff"]
    with contextlib.suppress(DockerException):
        ctr_client.image.remove(images_names, force=True)

    with pytest.raises(DockerException) as err:
        ctr_client.pull(images_names)
    assert f"{ctr_client.client_config.client_call[0]} image pull hellstuff" in str(
        err.value
    )


def test_pull_stream_logs_multiple_images_break(ctr_client: DockerClient) -> None:
    images_names = ["busybox:1", "hellstuff"]
    with contextlib.suppress(DockerException):
        ctr_client.image.remove(images_names, force=True)

    with pytest.raises(DockerException) as err:
        logs = ctr_client.pull(images_names, stream_logs=True)
        for _ in logs:
            pass
    assert f"{ctr_client.client_config.client_call[0]} image pull hellstuff" in str(
        err.value
    )


def test_push_validation_error(ctr_client: DockerClient) -> None:
    with pytest.raises(ValueError) as err:
        ctr_client.push(ANY, quiet=True, stream_logs=True)
    assert (
        "It's not possible to have stream_logs=True and quiet=True at the same time"
        in str(err.value)
    )


def test_push_nothing(ctr_client: DockerClient) -> None:
    result = ctr_client.push([], quiet=True)
    assert result is None


def test_push_nothing_stream_logs(ctr_client: DockerClient) -> None:
    result = ctr_client.push([], stream_logs=True)
    assert result is None


def test_push_duplicate_images(ctr_client: DockerClient) -> None:
    with contextlib.suppress(DockerException):
        ctr_client.image.remove("busybox:1", force=True)
    images = ["busybox:1", "busybox:1", "busybox:1"]
    with (
        patch.object(ctr_client.image, "inspect") as inspect_images,
        patch.object(ctr_client.image, "_push_single_tag") as push_single_tag,
        patch.object(ctr_client.image, "_push_multiple_tags") as push_multiple_tags,
    ):
        push_single_tag.return_value = None
        ctr_client.image.push(images, quiet=True)
        inspect_images.assert_called_once_with(["busybox:1"])
        push_multiple_tags.assert_not_called()
        push_single_tag.assert_called_once_with(
            tag_or_repo="busybox:1", quiet=True, stream_logs=False, buffer_bytes=None
        )


def test_push_not_quiet_multiple_images_break(ctr_client: DockerClient) -> None:
    with contextlib.suppress(DockerException):
        ctr_client.image.remove("busybox:1", force=True)
    ctr_client.image.pull("busybox:1", quiet=True)
    with pytest.raises(NoSuchImage):
        ctr_client.push(["busybox:1", "hellstuff"])


def test_push_stream_logs_multiple_images_break(ctr_client: DockerClient) -> None:
    with contextlib.suppress(DockerException):
        ctr_client.image.remove("busybox:1", force=True)
    ctr_client.image.pull("busybox:1", quiet=True)
    with pytest.raises(NoSuchImage):
        logs = ctr_client.push(["busybox:1", "hellstuff"], stream_logs=True)
        assert logs is not None
        for _ in logs:
            pass


def test_copy_from_and_to(ctr_client: DockerClient, tmp_path: Path) -> None:
    my_image = ctr_client.pull("busybox:1")
    (tmp_path / "dodo.txt").write_text("Hello world!")

    new_image_name = random_name()
    my_image.copy_to(tmp_path / "dodo.txt", "/dada.txt", new_tag=new_image_name)

    new_image_name = ctr_client.image.inspect(new_image_name)
    new_image_name.copy_from("/dada.txt", tmp_path / "dudu.txt")
    assert (tmp_path / "dodo.txt").read_text() == (tmp_path / "dudu.txt").read_text()


def test_copy_from_and_to_directory(ctr_client: DockerClient, tmp_path: Path) -> None:
    my_image = ctr_client.pull("busybox:1")
    (tmp_path / "dodo.txt").write_text("Hello world!")

    new_image_name = random_name()
    my_image.copy_to(tmp_path, "/some_path", new_tag=new_image_name)

    new_image_name = ctr_client.image.inspect(new_image_name)
    new_image_name.copy_from("/some_path", tmp_path / "some_path")
    assert (tmp_path / "some_path" / "dodo.txt").read_text() == "Hello world!"


def test_prune(ctr_client: DockerClient) -> None:
    ctr_client.pull("busybox")
    ctr_client.image.prune(all=True)
    with pytest.raises(DockerException):
        ctr_client.image.inspect("busybox")


def test_remove_nothing(ctr_client: DockerClient) -> None:
    with ctr_client.pull("hello-world"):
        all_images = set(ctr_client.image.list())
        ctr_client.image.remove([])
        assert all_images == set(ctr_client.image.list())


def test_no_such_image_inspect(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        ctr_client.image.inspect(image_name_that_does_not_exists)

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_no_such_image_remove(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        ctr_client.image.remove(image_name_that_does_not_exists)

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_no_such_image_push(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        ctr_client.push(image_name_that_does_not_exists)

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_no_such_image_push_stream_logs(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        ctr_client.push(image_name_that_does_not_exists, stream_logs=True)

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_no_such_image_save(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        ctr_client.image.save(image_name_that_does_not_exists, output="/tmp/dada")

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_no_such_image_save_generator(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        for _ in ctr_client.image.save(image_name_that_does_not_exists):
            pass

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_no_such_image_tag(ctr_client: DockerClient) -> None:
    image_name_that_does_not_exists = "dueizhguizhfezaezagrthyh"
    with pytest.raises(NoSuchImage) as err:
        ctr_client.image.tag(image_name_that_does_not_exists, "something")

    assert f"No such image: {image_name_that_does_not_exists}" in str(err.value)


def test_exists(ctr_client: DockerClient) -> None:
    my_image = ctr_client.pull("busybox")
    assert my_image.exists()
    assert ctr_client.image.exists("busybox")

    assert not ctr_client.image.exists("dudurghurozgiozpfezjigfoeioengizeonig")


@patch("pywhales.components.image.cli_wrapper.ContainerCLI")
def test_copy_from_default_pull(container_mock: Mock) -> None:
    container_cli_mock = MagicMock()
    container_mock.return_value = container_cli_mock

    test_image_name = "test_dummy_image"

    docker.image.copy_from(
        test_image_name, "test_path_in_image", "test_local_destination"
    )

    container_cli_mock.create.assert_called_with(test_image_name, pull="missing")


@patch("pywhales.components.image.cli_wrapper.ContainerCLI")
def test_copy_from_pull(container_mock: Mock) -> None:
    container_cli_mock = MagicMock()
    container_mock.return_value = container_cli_mock

    test_image_name = "test_dummy_image"
    test_pull_flag = "test_pull_flag_value"

    docker.image.copy_from(
        test_image_name,
        "test_path_in_image",
        "test_local_destination",
        pull=test_pull_flag,
    )

    container_cli_mock.create.assert_called_with(test_image_name, pull=test_pull_flag)


@patch("pywhales.components.image.cli_wrapper.ContainerCLI")
def test_copy_to_default_pull(container_mock: Mock) -> None:
    container_cli_mock = MagicMock()
    container_mock.return_value = container_cli_mock

    test_image_name = "test_dummy_image"

    docker.image.copy_to(
        test_image_name, "test_local_destination", "test_path_in_image"
    )

    container_cli_mock.create.assert_called_with(test_image_name, pull="missing")


@patch("pywhales.components.image.cli_wrapper.ContainerCLI")
def test_copy_to_pull(container_mock: Mock) -> None:
    container_cli_mock = MagicMock()
    container_mock.return_value = container_cli_mock

    test_image_name = "test_dummy_image"
    test_pull_flag = "test_pull_flag_value"

    docker.image.copy_to(
        test_image_name,
        "test_local_destination",
        "test_path_in_image",
        pull=test_pull_flag,
    )

    container_cli_mock.create.assert_called_with(test_image_name, pull=test_pull_flag)
