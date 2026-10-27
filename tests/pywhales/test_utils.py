"""Conversion and process-stream lifecycle regressions using real local children.

These tests launch Python and shell commands to exercise pipes, threads, buffering,
and deadlines. Docker wrapper paths are patched where used; daemon availability
does not replace the local process requirements.
"""

import os
import select
import signal
import sys
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest

import pywhales.utils
from pywhales.client_config import ClientConfig
from pywhales.components.buildx.cli_wrapper import stream_buildx_logs
from pywhales.exceptions import DockerException, DockerTimeout, NoSuchContainer
from pywhales.utils import (
    MappedProcessStream,
    ProcessStream,
    format_cpuset,
    stream_stdout_and_stderr,
)


def test_mapped_process_stream_owns_unstarted_mapping() -> None:
    child = stream_stdout_and_stderr([sys.executable, "-c", "input()"], pipe_stdin=True)
    stream = MappedProcessStream(child, (line for _, line in child))

    with stream as entered:
        assert entered is stream

    stream.close()
    assert list(stream) == []
    _assert_process_released(child)


def test_format_cpuset_none() -> None:
    assert format_cpuset(None) is None


def test_format_cpuset_list_of_ints() -> None:
    assert format_cpuset([0, 2, 3]) == "0,2,3"


def test_format_cpuset_empty_list() -> None:
    assert format_cpuset([]) == ""


def test_format_cpuset_string_passthrough() -> None:
    # cpuset strings accepted by the CLI must be passed through unmodified so
    # ranges like "0-3" and mixes like "0-3,7" reach docker as-is.
    assert format_cpuset("0-3") == "0-3"
    assert format_cpuset("0,2") == "0,2"
    assert format_cpuset("0-3,7") == "0-3,7"


def test_format_cpuset_generator() -> None:
    assert format_cpuset(iter([1, 4])) == "1,4"


def test_environment_variables_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_VARIABLE", "dododada")

    stdout = pywhales.utils.run(
        ["bash", "-c", "echo $SOME_VARIABLE && echo $OTHER_VARIABLE"],
        capture_stdout=True,
        env={"OTHER_VARIABLE": "dudu"},
    )
    assert stdout == "dododada\ndudu"


def test_stream_stdout_and_stderr_interactive() -> None:
    proc = stream_stdout_and_stderr(["cat"], pipe_stdin=True)
    assert isinstance(proc, ProcessStream)
    assert proc.stdin is not None
    proc.stdin.write(b"hello world\n")
    proc.stdin.close()
    output = list(proc)
    assert ("stdout", b"hello world\n") in output


def test_stream_stdout_and_stderr_interactive_multiple_lines() -> None:
    proc = stream_stdout_and_stderr(["cat"], pipe_stdin=True)
    assert isinstance(proc, ProcessStream)
    assert proc.stdin is not None
    proc.stdin.write(b"line1\n")
    proc.stdin.write(b"line2\n")
    proc.stdin.close()
    output = list(proc)
    stdout_lines = [line for source, line in output if source == "stdout"]
    assert b"line1\n" in stdout_lines
    assert b"line2\n" in stdout_lines


def test_stream_stdout_and_stderr_non_interactive() -> None:
    """Leave stdin unavailable when streaming without an interactive input pipe."""
    result = stream_stdout_and_stderr(["echo", "hi"])
    assert isinstance(result, ProcessStream)
    assert result.stdin is None
    output = list(result)
    assert ("stdout", b"hi\n") in output


def test_process_stream_close_after_eof() -> None:
    stream = stream_stdout_and_stderr([sys.executable, "-c", "print('done')"])
    assert list(stream) == [("stdout", b"done\n")]

    stream.close()
    stream.close()

    assert list(stream) == []


def _assert_process_released(stream: ProcessStream) -> None:
    """Check child reaping, reader and watchdog shutdown, and pipe closure."""
    assert stream._process.poll() is not None
    assert all(not thread.is_alive() for thread in stream._threads)
    assert not stream._watchdog.is_alive()
    assert all(
        pipe.closed
        for pipe in (stream.stdin, stream._process.stdout, stream._process.stderr)
        if pipe is not None
    )


def test_client_stream_budget_is_live_for_new_launches_but_frozen_after_start() -> None:
    config = ClientConfig(client_call=[sys.executable], stream_buffer_bytes=3)
    command = [
        *config.docker_cmd,
        "-c",
        "input(); print('long-record', flush=True); input()",
    ]
    stream = stream_stdout_and_stderr(command, pipe_stdin=True)
    config.stream_buffer_bytes = None
    try:
        assert stream.stdin is not None
        stream.stdin.write(b"go\n")
        stream.stdin.flush()
        with pytest.raises(BufferError):
            next(stream)
        _assert_process_released(stream)
        with stream_stdout_and_stderr(command, pipe_stdin=True) as fresh:
            assert fresh.stdin is not None
            fresh.stdin.write(b"go\n")
            fresh.stdin.flush()
            assert next(fresh) == ("stdout", b"long-record\n")
        _assert_process_released(fresh)
    finally:
        stream.close()


def test_explicit_unbounded_buffer_does_not_fall_back_to_client_budget() -> None:
    config = ClientConfig(client_call=[sys.executable], stream_buffer_bytes=3)

    stream = stream_stdout_and_stderr(
        [*config.docker_cmd, "-c", "print('long-record')"], buffer_bytes=None
    )

    assert list(stream) == [("stdout", b"long-record\n")]
    _assert_process_released(stream)


def test_buildx_mapped_stream_applies_client_budget_without_wrapper_override() -> None:
    config = ClientConfig(client_call=[sys.executable], stream_buffer_bytes=3)

    stream = stream_buildx_logs([*config.docker_cmd, "-c", "print('oversized')"])

    with pytest.raises(BufferError):
        list(stream)
    _assert_process_released(stream._stream)


@pytest.mark.parametrize("raw", [False, True])
def test_run_timeout_keeps_client_diagnostics_and_raw_output(raw: bool) -> None:
    secret = "timeout-secret-sentinel"
    config = ClientConfig(client_call=[sys.executable], raw_diagnostics=raw)

    with pytest.raises(DockerTimeout) as raised:
        pywhales.utils.run(
            [
                *config.docker_cmd,
                "-c",
                f"print({secret!r}, flush=True); __import__('threading').Event().wait()",
            ],
            input=b"",
            timeout=0.5,
        )

    assert (secret in str(raised.value)) is raw
    assert raised.value.stdout == secret + "\n"


def test_mapped_client_stream_applies_backpressure_and_cancels_saturated_child() -> (
    None
):
    config = ClientConfig(client_call=[sys.executable], stream_buffer_bytes=3)
    child = stream_stdout_and_stderr(
        [
            *config.docker_cmd,
            "-c",
            "import os; os.write(1, b'ab\\n' * 100000); input()",
        ],
        pipe_stdin=True,
    )
    stream = MappedProcessStream(child, (line for _, line in child))
    try:
        with child._queue.condition:
            assert child._queue.condition.wait_for(
                lambda: child._queue._bytes == 3 and bool(child._inflight["stdout"]),
                timeout=3,
            )

        assert next(stream) == b"ab\n"
        stream.close()

        _assert_process_released(child)
    finally:
        stream.close()


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("failure", ["natural", "owner", "timeout"])
def test_stream_diagnostics_snapshot_client_policy_at_launch(
    raw: bool, failure: str
) -> None:
    secret = "stream-secret-sentinel"
    config = ClientConfig(client_call=[sys.executable], raw_diagnostics=raw)
    action = "input()" if failure == "timeout" else "raise SystemExit(7)"
    command = [
        *config.docker_cmd,
        "-c",
        f"import sys; print({secret!r}, file=sys.stderr, flush=True); input(); {action}",
    ]
    stream = stream_stdout_and_stderr(
        command, pipe_stdin=True, timeout=1 if failure == "timeout" else None
    )
    errors: list[OSError | BufferError | DockerException] = []
    if failure == "owner":
        stream._on_failure(errors.append)
    config.raw_diagnostics = not raw
    try:
        assert next(stream) == ("stderr", (secret + "\n").encode())
        assert stream.stdin is not None
        stream.stdin.write(b"go\n")
        stream.stdin.flush()
        expected = DockerTimeout if failure == "timeout" else DockerException
        with pytest.raises(expected) as raised:
            list(stream)

        assert (secret in str(raised.value)) is raw
        assert raised.value.stderr == secret + "\n"
        if failure == "owner":
            assert errors == [raised.value]
        _assert_process_released(stream)
    finally:
        stream.close()


@pytest.mark.parametrize("consume", [False, True])
def test_process_stream_closes_idle_child(consume: bool) -> None:
    with stream_stdout_and_stderr(
        [sys.executable, "-c", "print('ready', flush=True); input()"], pipe_stdin=True
    ) as stream:
        if consume:
            assert next(stream) == ("stdout", b"ready\n")

        started = monotonic()
        stream.close()
        stream.close()

        assert monotonic() - started < 3
        assert list(stream) == []
        _assert_process_released(stream)


def test_process_stream_closes_with_pending_output() -> None:
    with stream_stdout_and_stderr(
        [sys.executable, "-c", "print('queued\\n' * 100000, flush=True); input()"],
        pipe_stdin=True,
    ) as stream:
        assert stream._queue.get(timeout=3) == ("stdout", b"queued\n")

        started = monotonic()
        stream.close()

        assert monotonic() - started < 3
        _assert_process_released(stream)


def test_process_stream_context_closes_before_iteration() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True
    )

    with stream as entered:
        assert entered is stream

    _assert_process_released(stream)


def test_process_stream_context_preserves_body_error() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "print('ready', flush=True); input()"], pipe_stdin=True
    )
    error = RuntimeError("consumer failed")

    with pytest.raises(RuntimeError) as raised, stream:
        assert next(stream) == ("stdout", b"ready\n")
        raise error

    assert raised.value is error
    _assert_process_released(stream)


def test_process_stream_close_interrupts_pending_next() -> None:
    with stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True
    ) as stream:
        entered = Event()
        finished = Event()
        output: list[tuple[str, bytes]] = []

        def consume() -> None:
            entered.set()
            output.extend(stream)
            finished.set()

        consumer = Thread(target=consume, daemon=True)
        consumer.start()
        assert entered.wait(timeout=3)

        stream.close()

        assert finished.wait(timeout=3)
        consumer.join(timeout=3)
        assert output == []
        _assert_process_released(stream)


@pytest.mark.skipif(
    sys.platform == "win32", reason="SIGTERM can only be ignored on POSIX"
)
def test_process_stream_kills_child_that_ignores_termination() -> None:
    with stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); input()",
        ],
        pipe_stdin=True,
    ) as stream:
        assert next(stream) == ("stdout", b"ready\n")

        started = monotonic()
        stream.close()

        assert monotonic() - started < 3
        _assert_process_released(stream)


def test_process_stream_eof_preserves_pipe_order_and_unterminated_lines() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; "
            "[(os.write(1, f'out{n}\\n'.encode()), os.write(2, f'err{n}\\n'.encode())) "
            "for n in range(1000)]; os.write(1, b'tail'); os.write(2, b'error tail')",
        ]
    )

    output = list(stream)

    assert [line for origin, line in output if origin == "stdout"] == [
        *(f"out{number}\n".encode() for number in range(1000)),
        b"tail",
    ]
    assert [line for origin, line in output if origin == "stderr"] == [
        *(f"err{number}\n".encode() for number in range(1000)),
        b"error tail",
    ]
    _assert_process_released(stream)


def test_process_stream_eof_preserves_command_failure() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import sys; print('output'); "
            "sys.stderr.write('No such container: missing\\n'); sys.exit(7)",
        ],
        pipe_stdin=True,
    )
    output: list[tuple[str, bytes]] = []

    with pytest.raises(NoSuchContainer) as raised:
        output.extend(stream)

    assert ("stdout", b"output\n") in output
    assert ("stderr", b"No such container: missing\n") in output
    assert raised.value.return_code == 7
    assert raised.value.stderr == "No such container: missing\n"
    _assert_process_released(stream)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal handlers")
def test_process_stream_close_never_runs_signal_handler(tmp_path: Path) -> None:
    forwarded = tmp_path / "forwarded"
    with stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import signal, sys; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, "
            "lambda *_: Path(sys.argv[1]).write_bytes(b'forwarded')); "
            "print('ready', flush=True)\n"
            "while True: signal.pause()",
            forwarded,
        ]
    ) as stream:
        assert stream._queue.get(timeout=3) == ("stdout", b"ready\n")

        stream.close()

        assert not forwarded.exists()
        assert stream._process.returncode == -signal.SIGKILL
        _assert_process_released(stream)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork and inherited fds")
def test_process_stream_close_discards_buffered_stdin_held_by_descendant() -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    with (
        os.fdopen(release_read, "rb", buffering=0) as release_reader,
        os.fdopen(release_write, "wb", buffering=0) as release_writer,
        os.fdopen(status_read, "rb", buffering=0) as status_reader,
        os.fdopen(status_write, "wb", buffering=0) as status_writer,
    ):
        stream = stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                "import os, signal, sys\n"
                "release, status = map(int, sys.argv[1:])\n"
                "if os.fork() == 0:\n"
                "    os.write(status, b'R')\n"
                "    os.read(release, 1)\n"
                "    os._exit(0)\n"
                "os.close(release)\n"
                "os.close(status)\n"
                "signal.pause()\n",
                release_reader.fileno(),
                status_writer.fileno(),
            ],
            pass_fds=(release_reader.fileno(), status_writer.fileno()),
            pipe_stdin=True,
        )
        closer = Thread(target=stream.close, daemon=True)
        try:
            release_reader.close()
            status_writer.close()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b"R"
            assert stream.stdin is not None
            stdin_fd = stream.stdin.fileno()
            os.set_blocking(stdin_fd, False)
            with pytest.raises(BlockingIOError):
                while True:
                    os.write(stdin_fd, b"x" * 4096)
            assert stream.stdin.write(b"buffered") == len(b"buffered")
            os.set_blocking(stdin_fd, True)

            closer.start()
            closer.join(timeout=3)

            assert not closer.is_alive(), "close blocked flushing buffered stdin"
            _assert_process_released(stream)
            assert not select.select([status_reader], [], [], 0)[0]
        finally:
            release_writer.close()
            if closer.ident is None:
                closer.start()
            closer.join(timeout=3)
            assert not closer.is_alive()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b""
            _assert_process_released(stream)


def test_run_timeout_preserves_captured_output() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; print('ready', flush=True); "
        "sys.stderr.write('error\\n'); sys.stderr.flush(); "
        "__import__('threading').Event().wait()",
    ]

    with pytest.raises(DockerTimeout) as raised:
        pywhales.utils.run(command, input=b"", timeout=1)

    assert type(raised.value).__name__ == "DockerTimeout"
    assert raised.value.docker_command == command
    assert raised.value.stdout == "ready\n"
    assert raised.value.stderr == "error\n"


def test_process_stream_times_out_without_consumption() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=0.1
    )

    assert stream._released.wait(timeout=3)

    with pytest.raises(DockerTimeout) as raised:
        next(stream)
    assert raised.value.docker_command[:2] == [sys.executable, "-c"]
    assert list(stream) == []
    _assert_process_released(stream)


@pytest.mark.parametrize("mapped", [False, True])
def test_process_stream_context_reports_unconsumed_timeout(mapped: bool) -> None:
    child = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=0.1
    )
    stream = (
        MappedProcessStream(child, (line for _, line in child)) if mapped else child
    )

    with pytest.raises(DockerTimeout), stream:
        assert child._released.wait(timeout=3)

    assert list(stream) == []
    _assert_process_released(child)


@pytest.mark.parametrize(
    "timeout", [0, -1, float("inf"), float("-inf"), float("nan"), True]
)
def test_invalid_timeout_is_rejected_before_launch(timeout: float) -> None:
    with pytest.raises(ValueError):
        pywhales.utils.run(["does-not-exist"], timeout=timeout)
    with pytest.raises(ValueError):
        stream_stdout_and_stderr(["does-not-exist"], timeout=timeout)


@pytest.mark.parametrize("timeout", [None, 1.0, 1e308])
def test_run_deadline_preserves_return_shapes(timeout: float | None) -> None:
    assert (
        pywhales.utils.run([sys.executable, "-c", "print('output')"], timeout=timeout)
        == "output"
    )
    assert pywhales.utils.run(
        [
            sys.executable,
            "-c",
            "import sys; print('output'); sys.stderr.write('error\\n')",
        ],
        return_stderr=True,
        timeout=timeout,
    ) == ("output", "error")


def test_run_natural_failure_is_not_timeout() -> None:
    with pytest.raises(DockerException) as raised:
        pywhales.utils.run([sys.executable, "-c", "raise SystemExit(7)"], timeout=1)
    assert type(raised.value) is DockerException
    assert raised.value.return_code == 7


def test_process_stream_deadline_continues_while_consumption_pauses() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "print('ready', flush=True); input()"],
        pipe_stdin=True,
        timeout=1,
    )
    assert next(stream) == ("stdout", b"ready\n")

    assert stream._released.wait(timeout=3)

    with pytest.raises(DockerTimeout):
        next(stream)
    _assert_process_released(stream)


@pytest.mark.parametrize("mapped", [False, True])
def test_process_stream_timeout_preserves_active_body_exception(mapped: bool) -> None:
    child = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=0.1
    )
    stream = (
        MappedProcessStream(child, (line for _, line in child)) if mapped else child
    )
    error = RuntimeError("body failure")

    with pytest.raises(RuntimeError) as raised, stream:
        assert child._released.wait(timeout=3)
        raise error

    assert raised.value is error
    _assert_process_released(child)


def test_process_stream_explicit_close_cancels_deadline() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=60
    )

    stream.close()

    with stream:
        assert list(stream) == []
    _assert_process_released(stream)


def test_process_stream_completed_child_keeps_unread_output_past_deadline() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "import os; os.write(1, b'ab\\n' * 100)"],
        timeout=0.5,
        buffer_bytes=3,
    )
    try:
        assert stream._process.wait(timeout=3) == 0
        assert not stream._released.wait(timeout=0.6)

        assert list(stream) == [("stdout", b"ab\n")] * 100

        _assert_process_released(stream)
    finally:
        stream.close()


@pytest.mark.parametrize("payload", [b"1234567\n", b"12345678"])
@pytest.mark.parametrize("pipe", [1, 2])
def test_process_stream_record_at_budget_is_preserved(
    payload: bytes, pipe: int
) -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", f"import os; os.write({pipe}, {payload!r})"],
        buffer_bytes=8,
    )

    assert list(stream) == [("stdout" if pipe == 1 else "stderr", payload)]

    _assert_process_released(stream)


@pytest.mark.parametrize("payload", [b"12345678\n", b"123456789"])
@pytest.mark.parametrize("pipe", [1, 2])
def test_process_stream_oversize_record_is_observable_without_consumer(
    payload: bytes, pipe: int
) -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", f"import os; os.write({pipe}, {payload!r}); input()"],
        pipe_stdin=True,
        buffer_bytes=8,
    )
    try:
        assert stream._released.wait(timeout=3)

        with pytest.raises(BufferError):
            next(stream)

        assert list(stream) == []
        _assert_process_released(stream)
    finally:
        stream.close()


def test_process_stream_stderr_history_has_independent_budget() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; os.write(2, b'ab\\n'); input(); os.write(2, b'cd\\n'); input()",
        ],
        pipe_stdin=True,
        buffer_bytes=4,
    )
    try:
        assert next(stream) == ("stderr", b"ab\n")
        assert stream.stdin is not None
        stream.stdin.write(b"continue\n")
        stream.stdin.flush()

        with pytest.raises(BufferError):
            next(stream)

        assert len(stream._stderr) <= 4
        _assert_process_released(stream)
    finally:
        stream.close()


@pytest.mark.parametrize("timeout", [None, 0.3])
def test_process_stream_saturated_queue_wakes_on_close_or_expiry(
    timeout: float | None,
) -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "import os; os.write(1, b'ab\\n' * 100000); input()"],
        pipe_stdin=True,
        timeout=timeout,
        buffer_bytes=3,
    )
    try:
        with stream._queue.condition:
            assert stream._queue.condition.wait_for(
                lambda: stream._queue._bytes == 3, timeout=3
            )
            assert max(stream._pending_bytes.values()) <= 3
            assert len(stream._stderr) <= 3
        if timeout is None:
            closers = [Thread(target=stream.close, daemon=True) for _ in range(4)]
            for closer in closers:
                closer.start()
            for closer in closers:
                closer.join(timeout=3)
                assert not closer.is_alive()
            assert list(stream) == []
        else:
            assert stream._released.wait(timeout=3)
            with pytest.raises(DockerTimeout):
                next(stream)
        _assert_process_released(stream)
    finally:
        stream.close()


def test_process_stream_unfinished_records_have_separate_pipe_budgets() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'ab\\n1234'); os.write(2, b'5678'); input()",
        ],
        pipe_stdin=True,
        buffer_bytes=4,
    )
    try:
        with stream._queue.condition:
            assert stream._queue.condition.wait_for(
                lambda: stream._pending_bytes == {"stdout": 4, "stderr": 4}, timeout=3
            )
            assert stream._queue._bytes == 3
            assert len(stream._stderr) == 4

        assert next(stream) == ("stdout", b"ab\n")
    finally:
        stream.close()
    _assert_process_released(stream)


def test_process_stream_buffer_error_is_reported_on_context_exit() -> None:
    child = stream_stdout_and_stderr(
        [sys.executable, "-c", "print('oversize'); input()"],
        pipe_stdin=True,
        buffer_bytes=3,
    )
    stream = MappedProcessStream(child, (line for _, line in child))

    with pytest.raises(BufferError), stream:
        assert child._released.wait(timeout=3)

    _assert_process_released(child)


def test_process_stream_timeout_preserves_partial_output() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'partial\\xff'); os.write(2, b'error'); input()",
        ],
        pipe_stdin=True,
        timeout=1,
        buffer_bytes=16,
    )
    try:
        assert stream._released.wait(timeout=3)

        with pytest.raises(DockerTimeout) as raised:
            next(stream)

        assert raised.value.stdout == "partial\ufffd"
        assert raised.value.stderr == "error"
        _assert_process_released(stream)
    finally:
        stream.close()


def test_process_stream_failure_wakes_sibling_blocked_on_full_queue() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'ab\\n' * 10); input(); os.write(2, b'oversize'); input()",
        ],
        pipe_stdin=True,
        buffer_bytes=3,
    )
    try:
        with stream._queue.condition:
            assert stream._queue.condition.wait_for(
                lambda: stream._queue._bytes == 3, timeout=3
            )
        assert stream.stdin is not None
        stream.stdin.write(b"fail\n")
        stream.stdin.flush()

        assert stream._released.wait(timeout=3)

        with pytest.raises(BufferError):
            next(stream)
        _assert_process_released(stream)
    finally:
        stream.close()


@pytest.mark.parametrize("budget", [0, -1, True, False, 1.5, "3"])
def test_process_stream_invalid_budget_is_rejected_before_launch(budget: int) -> None:
    with pytest.raises(ValueError):
        stream_stdout_and_stderr(["does-not-exist"], buffer_bytes=budget)


def test_process_stream_close_after_timeout_does_not_erase_error() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=0.1
    )
    assert stream._released.wait(timeout=3)

    stream.close()

    with pytest.raises(DockerTimeout):
        next(stream)
    with stream:
        assert list(stream) == []
    _assert_process_released(stream)


def test_process_stream_timeout_interrupts_blocked_next() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=0.1
    )
    errors: list[DockerTimeout] = []

    def consume() -> None:
        try:
            next(stream)
        except DockerTimeout as error:
            errors.append(error)

    consumer = Thread(target=consume, daemon=True)
    consumer.start()
    consumer.join(timeout=3)

    assert not consumer.is_alive()
    assert len(errors) == 1
    _assert_process_released(stream)


def test_mapped_process_stream_mapper_failure_closes_saturated_transport() -> None:
    child = stream_stdout_and_stderr(
        [sys.executable, "-c", "import os; os.write(1, b'ab\\n' * 100000); input()"],
        pipe_stdin=True,
        buffer_bytes=3,
    )
    error = ValueError("mapper failed")

    def map_record(record: tuple[str, bytes]) -> bytes:
        raise error

    stream = MappedProcessStream(child, map(map_record, child))

    with pytest.raises(ValueError) as raised:
        next(stream)

    assert raised.value is error
    _assert_process_released(child)


def test_mapped_process_stream_close_after_expiry_keeps_pending_timeout() -> None:
    child = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=0.1
    )
    stream = MappedProcessStream(child, (line for _, line in child))
    assert child._released.wait(timeout=3)

    stream.close()

    with pytest.raises(DockerTimeout):
        next(stream)
    assert list(stream) == []
    _assert_process_released(child)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork and inherited fds")
def test_process_stream_close_releases_pipes_inherited_after_natural_exit() -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    with (
        os.fdopen(release_read, "rb", buffering=0) as release_reader,
        os.fdopen(release_write, "wb", buffering=0) as release_writer,
        os.fdopen(status_read, "rb", buffering=0) as status_reader,
        os.fdopen(status_write, "wb", buffering=0) as status_writer,
    ):
        stream = stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                "import os, sys\n"
                "release, status = map(int, sys.argv[1:])\n"
                "if os.fork() == 0:\n"
                "    os.write(status, b'R')\n"
                "    os.read(release, 1)\n"
                "    os._exit(0)\n"
                "os._exit(0)\n",
                release_reader.fileno(),
                status_writer.fileno(),
            ],
            pass_fds=(release_reader.fileno(), status_writer.fileno()),
            timeout=0.5,
        )
        try:
            release_reader.close()
            status_writer.close()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b"R"
            assert stream._process.wait(timeout=3) == 0
            assert not stream._released.wait(timeout=0.6)

            started = monotonic()
            stream.close()

            assert monotonic() - started < 3
            assert list(stream) == []
            _assert_process_released(stream)
        finally:
            release_writer.close()
            stream.close()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b""


def test_process_stream_deadline_uses_launch_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pywhales.utils, "monotonic", lambda: monotonic() - 60)

    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=30
    )

    assert stream._released.wait(timeout=3)
    with pytest.raises(DockerTimeout):
        next(stream)
    _assert_process_released(stream)


def test_process_stream_close_wins_pending_natural_failure_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = stream_stdout_and_stderr([sys.executable, "-c", "raise SystemExit(7)"])
    reaped = Event()
    resume = Event()
    original_raise = stream._raise_error
    errors: list[DockerException] = []

    def pause_after_reap() -> None:
        original_raise()
        reaped.set()
        assert resume.wait(timeout=3)

    def consume() -> None:
        try:
            list(stream)
        except DockerException as error:
            errors.append(error)

    monkeypatch.setattr(stream, "_raise_error", pause_after_reap)
    consumer = Thread(target=consume, daemon=True)
    consumer.start()
    try:
        assert reaped.wait(timeout=3)

        stream.close()
        resume.set()
        consumer.join(timeout=3)

        assert not consumer.is_alive()
        assert errors == []
        _assert_process_released(stream)
    finally:
        resume.set()
        consumer.join(timeout=3)
        stream.close()


def test_process_stream_close_after_deadline_records_expiry() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True, timeout=60
    )
    with stream._queue.condition:
        stream._deadline = monotonic() - 1

    stream.close()

    with pytest.raises(DockerTimeout):
        next(stream)
    _assert_process_released(stream)


def test_process_stream_close_wins_before_owner_failure_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "import os; input(); os._exit(7)"], pipe_stdin=True
    )
    observed = Event()
    resume = Event()
    errors: list[OSError | BufferError | DockerException] = []
    fail = stream._fail

    def pause_before_failure(error: OSError | BufferError | DockerException) -> None:
        observed.set()
        assert resume.wait(timeout=3)
        fail(error)

    monkeypatch.setattr(stream, "_fail", pause_before_failure)
    stream._on_failure(errors.append)
    closer = Thread(target=stream.close, daemon=True)
    try:
        assert stream.stdin is not None
        stream.stdin.write(b"exit\n")
        stream.stdin.flush()
        assert observed.wait(timeout=3)

        closer.start()
        assert stream._stop.wait(timeout=3)
        resume.set()
        closer.join(timeout=3)

        assert not closer.is_alive()
        assert errors == []
        assert list(stream) == []
        _assert_process_released(stream)
    finally:
        resume.set()
        if closer.ident is not None:
            closer.join(timeout=3)
        stream.close()


def test_process_stream_owner_keeps_natural_failure_exception_type() -> None:
    message = b"No such container: missing\n"
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            f"import os; os.write(2, {message!r}); input(); os._exit(7)",
        ],
        pipe_stdin=True,
    )
    errors: list[OSError | BufferError | DockerException] = []
    stream._on_failure(errors.append)
    try:
        assert next(stream) == ("stderr", message)

        assert stream.stdin is not None
        stream.stdin.write(b"exit\n")
        stream.stdin.flush()

        assert stream._released.wait(timeout=3)
        assert len(errors) == 1
        with pytest.raises(NoSuchContainer) as raised:
            next(stream)
        assert raised.value is errors[0]
        assert raised.value.return_code == 7
        assert raised.value.stderr == message.decode()
        assert list(stream) == []
        _assert_process_released(stream)
    finally:
        stream.close()


def test_process_stream_timeout_preserves_read_scratch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input(); print('captured', flush=True); input()"],
        pipe_stdin=True,
        timeout=60,
    )
    read = Event()
    resume = Event()
    check_size = stream._check_size

    def pause_after_read(size: int, category: str) -> None:
        check_size(size, category)
        read.set()
        assert resume.wait(timeout=3)

    monkeypatch.setattr(stream, "_check_size", pause_after_read)
    try:
        assert stream.stdin is not None
        stream.stdin.write(b"read\n")
        stream.stdin.flush()
        assert read.wait(timeout=3)
        with stream._queue.condition:
            stream._deadline = monotonic() - 1
            stream._expire()

        resume.set()
        assert stream._released.wait(timeout=3)

        with pytest.raises(DockerTimeout) as raised:
            next(stream)
        assert raised.value.stdout == "captured\n"
        _assert_process_released(stream)
    finally:
        resume.set()
        stream.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork and inherited fds")
def test_run_completed_child_with_inherited_pipes_is_not_timeout() -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    with (
        os.fdopen(release_read, "rb", buffering=0) as release_reader,
        os.fdopen(release_write, "wb", buffering=0) as release_writer,
        os.fdopen(status_read, "rb", buffering=0) as status_reader,
        os.fdopen(status_write, "wb", buffering=0) as status_writer,
    ):
        try:
            output = pywhales.utils.run(
                [
                    sys.executable,
                    "-c",
                    "import os, sys\n"
                    "release, status = map(int, sys.argv[1:])\n"
                    "if os.fork() == 0:\n"
                    "    os.write(status, b'R')\n"
                    "    os.read(release, 1)\n"
                    "    os._exit(0)\n"
                    "os.write(1, b'completed\\n')\n"
                    "os._exit(0)\n",
                    release_reader.fileno(),
                    status_writer.fileno(),
                ],
                pass_fds=(release_reader.fileno(), status_writer.fileno()),
                timeout=0.5,
            )

            assert output == "completed"
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b"R"
        finally:
            release_reader.close()
            status_writer.close()
            release_writer.close()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b""


def test_process_stream_queue_budget_is_shared_between_both_producers() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'aa\\n' * 10); input(); os.write(2, b'bb\\n'); input()",
        ],
        pipe_stdin=True,
        buffer_bytes=3,
    )
    try:
        with stream._queue.condition:
            assert stream._queue.condition.wait_for(
                lambda: stream._queue._bytes == 3, timeout=3
            )
        assert stream.stdin is not None
        stream.stdin.write(b"stderr\n")
        stream.stdin.flush()
        with stream._queue.condition:
            assert stream._queue.condition.wait_for(
                lambda: all(stream._inflight.values()), timeout=3
            )
            assert stream._queue._bytes == 3
            assert len(stream._stderr) == 3
    finally:
        stream.close()
    _assert_process_released(stream)


@pytest.mark.parametrize("budget", [65536, 65537])
def test_process_stream_record_bound_includes_newline_across_chunks(
    budget: int,
) -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "import os; os.write(1, b'x' * 65536 + b'\\n')"],
        buffer_bytes=budget,
    )
    try:
        if budget == 65536:
            with pytest.raises(BufferError):
                list(stream)
        else:
            assert list(stream) == [("stdout", b"x" * 65536 + b"\n")]
        _assert_process_released(stream)
    finally:
        stream.close()
