"""Deadline execution contracts against a temporary Python CLI driver.

Commands launch real local Python children rather than Docker. The driver records
argv and PIDs, supplies inspection fixtures, and optionally blocks or fails the
launches selected by argv prefix so tests can observe timeout delivery, captured
output, and process cleanup.
"""

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from time import perf_counter, sleep
from typing import Literal, assert_never

import pytest
from pytest_mock import MockerFixture

from pywhales import Container, DockerClient, Image, utils
from pywhales.components.buildx.cli_wrapper import BakeResult
from pywhales.components.compose.models import ComposeEvent
from pywhales.components.system.models import DockerEvent
from pywhales.exceptions import DockerException, DockerTimeout
from pywhales.utils import MappedProcessStream, ProcessStream

type Operation = Literal["run", "execute", "instance", "compose", "build", "bake"]
OPERATIONS: tuple[Operation, ...] = (
    "run",
    "execute",
    "instance",
    "compose",
    "build",
    "bake",
)


@pytest.fixture
def client(tmp_path: Path) -> DockerClient:
    """Return a client whose command prefix starts the temporary Python driver.

    Creating the fixture writes the driver but starts no child. Wrapper calls
    launch it and append their arguments to launches.jsonl under tmp_path.
    """
    driver = tmp_path / "driver.py"
    driver.write_text(
        """import json
import os
import sys
import threading
import time
from pathlib import Path

record = Path(sys.argv[1])
args = sys.argv[2:]
with record.open('a') as output:
    output.write(json.dumps(args) + '\\n')
with record.with_name('pids.jsonl').open('a') as output:
    output.write(json.dumps([os.getpid(), args]) + '\\n')


def selected(name):
    prefix = os.environ.get(name, '').split()
    return bool(prefix) and args[:len(prefix)] == prefix


for words, seconds in json.loads(os.environ.get('PYWHALES_TEST_DELAYS', '{}')).items():
    if set(words.split()) <= set(args):
        time.sleep(seconds)
if selected('PYWHALES_TEST_BLOCK_ON'):
    threading.Event().wait(60)
if selected('PYWHALES_TEST_FAIL_ON'):
    print('induced failure', file=sys.stderr)
    sys.exit(3)
if args[:2] == ['buildx', 'inspect']:
    print('Name: selected')
elif args[:2] == ['buildx', 'ls']:
    print(json.dumps({'Name': 'selected', 'Driver': 'docker'}))
elif args[:2] == ['image', 'inspect']:
    names = [name for name in args[2:] if not name.startswith('-')]
    print(json.dumps([
        {'Id': name if name.startswith('sha256:') else 'sha256:' + name}
        for name in names
    ]))
elif args[:2] == ['manifest', 'inspect']:
    print('{}')
elif args[:2] == ['container', 'inspect']:
    print('[{"Id":"container-id","HostConfig":{"AutoRemove":false},"State":{"Running":false}}]')
elif args[:2] == ['service', 'inspect']:
    print('[{"ID":"service-id","Spec":{"Name":"workload"}}]')
else:
    if '--iidfile' in args:
        Path(args[args.index('--iidfile') + 1]).write_text('sha256:built')
    if '--print' in args:
        print('{"target":{"app":{"context":"."}}}', flush=True)
    elif 'logs' in args:
        os.write(1, b'app | first\\nlast')
    elif 'events' in args:
        print('{"action":"start","Action":"start","service":"web"}', flush=True)
    else:
        print('container-id', flush=True)
    block = os.environ.get('PYWHALES_TEST_BLOCK')
    if block == '1' or (block == 'print' and '--print' in args):
        if {'logs', 'events', 'push', 'start', 'watch', 'prune'} & set(args):
            os.write(2, b'diagnostic\\xff')
        threading.Event().wait(60)
"""
    )
    result = DockerClient()
    result.client_config._client_call_with_path = [
        sys.executable,
        str(driver),
        str(tmp_path / "launches.jsonl"),
    ]
    return result


def invoke(
    client: DockerClient,
    operation: Operation,
    *,
    timeout: float | None,
    stream: bool = False,
) -> object:
    """Dispatch a launch scenario with its selected execution deadline.

    Args:
        client: Client configured to run the local Python driver.
        operation: Launch entry point whose return and timeout behavior is tested.
        timeout: Per-launch duration in seconds, or None for no deadline.
        stream: Return owned output streams instead of eager results when true.

    Returns:
        Captured text for run/execute/Compose, an image for build, or a config
        mapping for bake. Streaming returns a caller-owned process stream;
        build and bake map records to text instead of labeled bytes.

    Raises:
        ValueError: If timeout is not a finite positive duration or None.
        DockerTimeout: If an eager launch exceeds its deadline. A streaming
            deadline is reported when the returned stream is consumed or exited.
    """
    match operation:
        case "run":
            return client.run("app", stream=stream, timeout=timeout)
        case "execute":
            return client.execute(
                "container", ["command"], stream=stream, timeout=timeout
            )
        case "instance":
            return Container(
                client.client_config, "container", is_immutable_id=True
            ).execute(["command"], stream=stream, timeout=timeout)
        case "compose":
            return client.compose.run("app", tty=False, stream=stream, timeout=timeout)
        case "build":
            return client.build(".", stream_logs=stream, timeout=timeout)
        case "bake":
            return client.buildx.bake(stream_logs=stream, timeout=timeout)
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "timeout", [0.0, -1.0, float("inf"), float("-inf"), float("nan"), True, False]
)
def test_invalid_timeout_precedes_every_launch(
    client: DockerClient, tmp_path: Path, operation: Operation, timeout: float
) -> None:
    with pytest.raises(ValueError):
        invoke(client, operation, timeout=timeout)

    assert not (tmp_path / "launches.jsonl").exists()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("stream", [False, True])
def test_deadline_reaches_selected_process(
    client: DockerClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
    stream: bool,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")

    with pytest.raises(DockerTimeout):
        result = invoke(client, operation, timeout=0.4, stream=stream)
        if stream:
            assert isinstance(result, (ProcessStream, MappedProcessStream))
            with result:
                list(result)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("timeout", [None, 5.0])
def test_successful_deadlines_preserve_results(
    client: DockerClient, operation: Operation, timeout: float | None
) -> None:
    result = invoke(client, operation, timeout=timeout)

    match operation:
        case "build":
            assert isinstance(result, Image)
            assert result.id == "sha256:built"
        case "bake":
            assert result == {"target": {"app": {"context": "."}}}
        case "run" | "execute" | "instance" | "compose":
            assert result == "container-id"
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", ["run", "execute", "instance", "compose"])
def test_detached_deadlines_preserve_results(
    client: DockerClient,
    operation: Literal["run", "execute", "instance", "compose"],
) -> None:
    match operation:
        case "run":
            result = client.run("app", pull="never", detach=True, timeout=5)
        case "execute":
            result = client.execute("container", ["command"], detach=True, timeout=5)
        case "instance":
            result = Container(
                client.client_config, "container", is_immutable_id=True
            ).execute(["command"], detach=True, timeout=5)
        case "compose":
            result = client.compose.run("app", detach=True, tty=False, timeout=5)
        case unreachable:
            assert_never(unreachable)

    if operation in ("run", "compose"):
        assert isinstance(result, Container)
        assert str(result) == "container-id"
    else:
        assert result is None


@pytest.mark.parametrize("operation", OPERATIONS)
def test_stream_deadlines_preserve_shapes(
    client: DockerClient, operation: Operation
) -> None:
    result = invoke(client, operation, timeout=5, stream=True)

    assert isinstance(result, (ProcessStream, MappedProcessStream))
    with result:
        lines = list(result)
    if operation in ("build", "bake"):
        assert lines == ["container-id\n"]
    else:
        assert lines == [("stdout", b"container-id\n")]


def test_bake_deadline_preserves_config_type(client: DockerClient) -> None:
    result: BakeResult = client.buildx.bake(print=True, timeout=5)

    assert result == {"target": {"app": {"context": "."}}}


def test_bake_config_launch_has_its_own_deadline(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "print")

    with pytest.raises(DockerTimeout) as expired:
        client.buildx.bake(timeout=0.4)

    assert "--print" in expired.value.docker_command


type Subscription = Literal[
    "container_logs",
    "root_logs",
    "handle_logs",
    "compose_logs",
    "service_logs",
    "compose_events",
    "system_events",
]
LOG_SUBSCRIPTIONS: tuple[Subscription, ...] = (
    "container_logs",
    "root_logs",
    "handle_logs",
    "compose_logs",
    "service_logs",
)
STREAM_SUBSCRIPTIONS: tuple[Subscription, ...] = (
    "container_logs",
    "root_logs",
    "compose_logs",
    "service_logs",
    "compose_events",
    "system_events",
)


def subscribe(
    client: DockerClient,
    operation: Subscription,
    *,
    timeout: float | None,
    stream: bool = True,
) -> str | Iterable[tuple[str, bytes] | ComposeEvent | DockerEvent]:
    """Start a selected log or event subscription against the local driver.

    Args:
        client: Client configured to run the local Python driver.
        operation: Subscription entry point to exercise.
        timeout: Per-subscription duration in seconds, or None for no deadline.
        stream: Stream logs when true; false collects text. Handle logs always
            collect text, and event subscriptions always stream typed events.

    Returns:
        Collected log text or a caller-owned stream of labeled log bytes or
        typed events. Close streams or use their context manager on early exit;
        launching starts the child and deadline before consumption.

    Raises:
        ValueError: If timeout is not a finite positive duration or None.
        DockerTimeout: If collected logs exceed the deadline. Streams report
            expiry during consumption or context-manager exit instead.
    """
    match operation:
        case "container_logs":
            return client.container.logs(
                "workload", follow=True, stream=stream, timeout=timeout
            )
        case "root_logs":
            return client.logs("workload", follow=True, stream=stream, timeout=timeout)
        case "handle_logs":
            return Container(
                client.client_config, "workload", is_immutable_id=True
            ).logs(timeout=timeout)
        case "compose_logs":
            return client.compose.logs(
                ["web"], follow=True, stream=stream, timeout=timeout
            )
        case "service_logs":
            return client.service.logs(
                "workload", follow=True, stream=stream, timeout=timeout
            )
        case "compose_events":
            return client.compose.events(["web"], timeout=timeout)
        case "system_events":
            return client.system.events(filters={"container": "web"}, timeout=timeout)
        case unreachable:
            assert_never(unreachable)


def assert_released(raw: ProcessStream) -> None:
    """Check process completion, watchdog and reader shutdown, and output closure."""
    assert raw._process.returncode is not None
    assert not raw._watchdog.is_alive()
    assert all(not thread.is_alive() for thread in raw._threads)
    assert all(
        pipe.closed
        for pipe in (raw._process.stdout, raw._process.stderr)
        if pipe is not None
    )


@pytest.mark.parametrize("operation", (*STREAM_SUBSCRIPTIONS, "handle_logs"))
@pytest.mark.parametrize(
    "timeout", [0.0, -1.0, float("inf"), float("-inf"), float("nan"), True, False]
)
def test_invalid_subscription_timeout_precedes_selected_launch(
    client: DockerClient, tmp_path: Path, operation: Subscription, timeout: float
) -> None:
    with pytest.raises(ValueError):
        subscribe(client, operation, timeout=timeout)

    record = tmp_path / "launches.jsonl"
    launches = (
        [json.loads(line) for line in record.read_text().splitlines()]
        if record.exists()
        else []
    )
    assert all(args[1] == "inspect" for args in launches)


@pytest.mark.parametrize("operation", LOG_SUBSCRIPTIONS)
@pytest.mark.parametrize("timeout", [None, 5.0])
def test_collected_log_deadline_preserves_labeled_text(
    client: DockerClient, operation: Subscription, timeout: float | None
) -> None:
    result = subscribe(client, operation, timeout=timeout, stream=False)

    assert result == "app | first\nlast"


@pytest.mark.parametrize("operation", LOG_SUBSCRIPTIONS)
def test_collected_log_timeout_preserves_partial_output_and_reaps_child(
    client: DockerClient,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    operation: Subscription,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    launch = mocker.spy(utils, "_start_process")

    with pytest.raises(DockerTimeout) as expired:
        subscribe(client, operation, timeout=1.0, stream=False)

    assert expired.value.stdout is not None
    assert expired.value.stdout.endswith("last")
    assert expired.value.stderr == "diagnostic\ufffd"
    assert "logs" in expired.value.docker_command
    raw = launch.spy_return
    assert_released(raw)
    with raw:
        assert list(raw) == []


@pytest.mark.parametrize("operation", STREAM_SUBSCRIPTIONS)
def test_subscription_timeout_preserves_unconsumed_output_and_reports_once(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, operation: Subscription
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    result = subscribe(client, operation, timeout=1.0)
    assert isinstance(result, (ProcessStream, MappedProcessStream))
    raw = result._stream if isinstance(result, MappedProcessStream) else result
    try:
        assert raw._released.wait(timeout=3)

        with pytest.raises(DockerTimeout) as expired:
            next(result)

        assert expired.value.stdout is not None
        assert (
            "app | first\nlast" if "logs" in operation else '"action":"start"'
        ) in expired.value.stdout
        assert expired.value.stderr == "diagnostic\ufffd"
        assert_released(raw)
        with result:
            assert list(result) == []
    finally:
        result.close()


@pytest.mark.parametrize("operation", STREAM_SUBSCRIPTIONS)
def test_subscription_deadline_remains_active_after_first_record(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, operation: Subscription
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    result = subscribe(client, operation, timeout=5.0)
    assert isinstance(result, (ProcessStream, MappedProcessStream))
    raw = result._stream if isinstance(result, MappedProcessStream) else result
    with result:
        next(result)
        with raw._queue.condition:
            assert raw._queue.condition.wait_for(lambda: bool(raw._stderr), timeout=3)
        assert raw._deadline is not None
        expired_at = raw._deadline + 1
        monkeypatch.setattr("pywhales._process_stream.monotonic", lambda: expired_at)

        with pytest.raises(DockerTimeout) as expired:
            next(result)

        assert expired.value.stderr == "diagnostic\ufffd"
        assert_released(raw)
        assert list(result) == []


@pytest.mark.parametrize("operation", STREAM_SUBSCRIPTIONS)
def test_subscription_deadline_does_not_interrupt_post_exit_drain(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, operation: Subscription
) -> None:
    result = subscribe(client, operation, timeout=5.0)
    assert isinstance(result, (ProcessStream, MappedProcessStream))
    raw = result._stream if isinstance(result, MappedProcessStream) else result
    with result:
        assert raw._process.wait(timeout=3) == 0
        assert raw._deadline is not None
        expired_at = raw._deadline + 1
        monkeypatch.setattr("pywhales._process_stream.monotonic", lambda: expired_at)

        records = list(result)

    if isinstance(result, ProcessStream):
        assert records == [("stdout", b"app | first\n"), ("stdout", b"last")]
    else:
        assert len(records) == 1
        event = records[0]
        assert isinstance(event, (ComposeEvent, DockerEvent))
        assert event.action == "start"
    assert_released(raw)


@pytest.mark.parametrize("operation", STREAM_SUBSCRIPTIONS)
@pytest.mark.parametrize("consume", [False, True])
def test_subscription_early_close_cancels_deadline_and_reaps_child(
    client: DockerClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: Subscription,
    consume: bool,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    result = subscribe(client, operation, timeout=5)
    assert isinstance(result, (ProcessStream, MappedProcessStream))
    raw = result._stream if isinstance(result, MappedProcessStream) else result
    with result:
        if consume:
            item = next(result)
            if isinstance(item, (ComposeEvent, DockerEvent)):
                assert item.action == "start"
            else:
                assert item == ("stdout", b"app | first\n")

        result.close()
        result.close()

    assert list(result) == []
    assert_released(raw)


@pytest.mark.parametrize("operation", ["compose_events", "system_events"])
@pytest.mark.parametrize("timeout", [None, 5.0])
def test_event_deadline_preserves_typed_mapping_and_eof(
    client: DockerClient, operation: Subscription, timeout: float | None
) -> None:
    result = subscribe(client, operation, timeout=timeout)
    assert isinstance(result, MappedProcessStream)
    with result:
        events = list(result)

    assert len(events) == 1
    event = events[0]
    assert isinstance(
        event, ComposeEvent if operation == "compose_events" else DockerEvent
    )
    assert event.action == "start"
    assert_released(result._stream)


type TransferOperation = Literal[
    "image_pull", "compose_pull", "manifest_push", "plugin_push"
]
TRANSFER_OPERATIONS: tuple[TransferOperation, ...] = (
    "image_pull",
    "compose_pull",
    "manifest_push",
    "plugin_push",
)
STREAMABLE_TRANSFERS: tuple[TransferOperation, ...] = (
    "image_pull",
    "compose_pull",
)


def invoke_transfer(
    client: DockerClient,
    operation: TransferOperation,
    *,
    timeout: float | None,
    stream: bool = False,
) -> object:
    """Dispatch a registry-transfer scenario with its selected execution deadline.

    Args:
        client: Client configured to run the local Python driver.
        operation: Transfer entry point whose forwarding and timeout behavior is
            tested.
        timeout: Per-launch duration in seconds, or None for no deadline.
        stream: Return an owned output stream instead of an eager result when
            true. Manifest and plugin pushes have no streaming mode.

    Returns:
        An image for image pulls, None for pushes and non-streamed Compose
        pulls, or a caller-owned stream for streamed transfers.

    Raises:
        ValueError: If timeout is not a finite positive duration or None.
        DockerTimeout: If an eager launch exceeds its deadline. A streaming
            deadline is reported when the returned stream is consumed or exited.
    """
    match operation:
        case "image_pull":
            return client.image.pull(
                "built", quiet=not stream, stream_logs=stream, timeout=timeout
            )
        case "compose_pull":
            return client.compose.pull(
                "app", quiet=not stream, stream_logs=stream, timeout=timeout
            )
        case "manifest_push":
            return client.manifest.push("manifest-list", quiet=True, timeout=timeout)
        case "plugin_push":
            return client.plugin.push("plugin-ref", timeout=timeout)
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", TRANSFER_OPERATIONS)
@pytest.mark.parametrize(
    "timeout", [0.0, -1.0, float("inf"), float("-inf"), float("nan"), True, False]
)
def test_invalid_transfer_timeout_precedes_every_launch(
    client: DockerClient, tmp_path: Path, operation: TransferOperation, timeout: float
) -> None:
    with pytest.raises(ValueError):
        invoke_transfer(client, operation, timeout=timeout)

    assert not (tmp_path / "launches.jsonl").exists()


@pytest.mark.parametrize("operation", TRANSFER_OPERATIONS)
def test_transfer_deadline_reaches_selected_process(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, operation: TransferOperation
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")

    with pytest.raises(DockerTimeout):
        invoke_transfer(client, operation, timeout=0.4)


@pytest.mark.parametrize("operation", TRANSFER_OPERATIONS)
@pytest.mark.parametrize("timeout", [None, 5.0])
def test_transfer_deadline_preserves_results(
    client: DockerClient, operation: TransferOperation, timeout: float | None
) -> None:
    result = invoke_transfer(client, operation, timeout=timeout)

    match operation:
        case "image_pull":
            assert isinstance(result, Image)
            assert result.id == "sha256:built"
        case "compose_pull" | "manifest_push" | "plugin_push":
            assert result is None
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", STREAMABLE_TRANSFERS)
def test_transfer_stream_deadline_preserves_shape(
    client: DockerClient,
    operation: Literal["image_pull", "compose_pull"],
) -> None:
    result = invoke_transfer(client, operation, timeout=5, stream=True)

    if operation == "compose_pull":
        assert isinstance(result, ProcessStream)
        with result:
            lines = list(result)
        assert lines == [("stdout", b"container-id\n")]
    else:
        assert isinstance(result, MappedProcessStream)
        with result:
            lines = list(result)
        assert lines == [("built", b"container-id\n")]


COPY_BUDGETS = [
    pytest.param(0.4, 600.0, id="explicit-overrides-default"),
    pytest.param(None, 0.4, id="inherits-default"),
]


def launched(tmp_path: Path) -> list[list[str]]:
    """Return the argv of every driver launch so far, in launch order."""
    record = tmp_path / "launches.jsonl"
    return [json.loads(line) for line in record.read_text().splitlines()]


def assert_reaped(tmp_path: Path, prefix: list[str]) -> None:
    """Check each driver child whose argv starts with prefix is gone, not a zombie."""
    children: list[tuple[int, list[str]]] = [
        json.loads(line) for line in (tmp_path / "pids.jsonl").read_text().splitlines()
    ]
    pids = [pid for pid, args in children if args[: len(prefix)] == prefix]
    assert pids
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def copy_timeouts(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, default: float, block: str
) -> None:
    """Configure the client default and block the driver launches matching block."""
    client.client_config.default_timeout = default
    monkeypatch.setenv("PYWHALES_TEST_BLOCK_ON", block)


type ContainerTransfer = Literal["copy", "export", "copy_from", "copy_to"]
CONTAINER_TRANSFERS: tuple[ContainerTransfer, ...] = (
    "copy",
    "export",
    "copy_from",
    "copy_to",
)


@pytest.mark.parametrize(("timeout", "default"), COPY_BUDGETS)
@pytest.mark.parametrize("operation", CONTAINER_TRANSFERS)
def test_container_transfer_timeout_reaps_local_child(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: ContainerTransfer,
    timeout: float | None,
    default: float,
) -> None:
    destination = tmp_path / "out"
    handle = Container(client.client_config, "workload", is_immutable_id=True)
    blocked = ["container", "export" if operation == "export" else "cp"]
    copy_timeouts(client, monkeypatch, default, " ".join(blocked))

    with pytest.raises(DockerTimeout):
        match operation:
            case "copy":
                client.copy(("workload", "/data"), destination, timeout=timeout)
            case "export":
                client.export("workload", destination, timeout=timeout)
            case "copy_from":
                handle.copy_from("/data", destination, timeout=timeout)
            case "copy_to":
                handle.copy_to(destination, "/data", timeout=timeout)
            case unreachable:
                assert_never(unreachable)

    assert [args[:2] for args in launched(tmp_path)] == [blocked]
    assert_reaped(tmp_path, blocked)
    assert client.client_config.default_timeout == default


type ImageCopy = Literal["copy_from", "copy_to"]
IMAGE_COPIES: tuple[ImageCopy, ...] = ("copy_from", "copy_to")


def image_copy(
    client: DockerClient, operation: ImageCopy, timeout: float | None
) -> object:
    """Run an image copy helper by its string reference, with its implicit launches."""
    match operation:
        case "copy_from":
            return client.image.copy_from("built", "/data", "out", timeout=timeout)
        case "copy_to":
            return client.image.copy_to("built", "local", "/data", timeout=timeout)
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize(("timeout", "default"), COPY_BUDGETS)
@pytest.mark.parametrize("step", ["inspect", "pull"])
@pytest.mark.parametrize("operation", IMAGE_COPIES)
def test_image_copy_preflight_timeout_precedes_container_create(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: ImageCopy,
    step: Literal["inspect", "pull"],
    timeout: float | None,
    default: float,
) -> None:
    if step == "pull":
        monkeypatch.setenv("PYWHALES_TEST_FAIL_ON", "image inspect")
    copy_timeouts(client, monkeypatch, default, f"image {step}")

    with pytest.raises(DockerTimeout):
        image_copy(client, operation, timeout)

    launches = launched(tmp_path)
    assert launches[-1][:2] == ["image", step]
    assert all(args[0] == "image" for args in launches)
    assert_reaped(tmp_path, ["image", step])
    assert client.client_config.default_timeout == default


@pytest.mark.parametrize("body_fails", [False, True])
@pytest.mark.parametrize(("timeout", "default"), COPY_BUDGETS)
@pytest.mark.parametrize("operation", IMAGE_COPIES)
def test_image_copy_cleanup_timeout_keeps_body_failure(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: ImageCopy,
    timeout: float | None,
    default: float,
    body_fails: bool,
) -> None:
    if body_fails:
        monkeypatch.setenv("PYWHALES_TEST_FAIL_ON", "container cp")
    copy_timeouts(client, monkeypatch, default, "container rm")

    if body_fails:
        with pytest.raises(BaseExceptionGroup) as raised:
            image_copy(client, operation, timeout)
        body, cleanup = raised.value.exceptions
        assert isinstance(body, DockerException)
        assert not isinstance(body, DockerTimeout)
        assert isinstance(cleanup, DockerTimeout)
    else:
        with pytest.raises(DockerTimeout):
            image_copy(client, operation, timeout)

    assert launched(tmp_path)[-1][:2] == ["container", "rm"]
    assert_reaped(tmp_path, ["container", "rm"])


@pytest.fixture
def scratch_names(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Pin the helper image and container names a volume copy generates."""
    names = iter(("scratch-image", "scratch-container"))
    monkeypatch.setattr(
        "pywhales.components.volume.cli_wrapper.random_name", lambda: next(names)
    )
    return ("scratch-image", "scratch-container")


@pytest.mark.parametrize(("timeout", "default"), COPY_BUDGETS)
def test_volume_copy_build_preflight_timeout_removes_scratch_image(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scratch_names: tuple[str, str],
    timeout: float | None,
    default: float,
) -> None:
    image_name, _ = scratch_names
    copy_timeouts(client, monkeypatch, default, "buildx inspect")

    with pytest.raises(DockerTimeout):
        client.volume.copy(("data", "."), tmp_path / "out", timeout=timeout)

    launches = launched(tmp_path)
    assert all(args[0] != "container" for args in launches)
    assert launches[-1][:2] == ["image", "rm"]
    assert image_name in launches[-1]
    assert_reaped(tmp_path, ["buildx", "inspect"])
    assert client.client_config.default_timeout == default


@pytest.mark.parametrize("body_fails", [False, True])
@pytest.mark.parametrize(("timeout", "default"), COPY_BUDGETS)
def test_volume_copy_cleanup_timeout_still_removes_scratch_image(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scratch_names: tuple[str, str],
    timeout: float | None,
    default: float,
    body_fails: bool,
) -> None:
    image_name, container_name = scratch_names
    if body_fails:
        monkeypatch.setenv("PYWHALES_TEST_FAIL_ON", "container cp")
    copy_timeouts(client, monkeypatch, default, "container rm")

    if body_fails:
        with pytest.raises(BaseExceptionGroup) as raised:
            client.volume.copy(("data", "."), tmp_path / "out", timeout=timeout)
        body, cleanup = raised.value.exceptions
        assert isinstance(body, DockerException)
        assert not isinstance(body, DockerTimeout)
        assert isinstance(cleanup, DockerTimeout)
    else:
        with pytest.raises(DockerTimeout):
            client.volume.copy(("data", "."), tmp_path / "out", timeout=timeout)

    removals = [args for args in launched(tmp_path) if args[1] in ("rm", "remove")]
    assert [(args[:2], args[-1]) for args in removals] == [
        (["container", "rm"], container_name),
        (["image", "rm"], image_name),
    ]
    assert_reaped(tmp_path, ["container", "rm"])
    assert client.client_config.default_timeout == default


type LongLaunch = Literal[
    "image_push",
    "root_push",
    "container_start",
    "root_start",
    "handle_start",
    "compose_push",
    "compose_watch",
    "buildx_prune",
]
EAGER_LAUNCHES: tuple[LongLaunch, ...] = (
    "image_push",
    "root_push",
    "container_start",
    "root_start",
    "handle_start",
    "compose_push",
    "buildx_prune",
)
STREAM_LAUNCHES: tuple[LongLaunch, ...] = (
    "image_push",
    "root_push",
    "container_start",
    "root_start",
    "handle_start",
    "compose_watch",
    "buildx_prune",
)
LAUNCH_PREFIX: dict[LongLaunch, list[str]] = {
    "image_push": ["image", "push"],
    "root_push": ["image", "push"],
    "container_start": ["container", "start"],
    "root_start": ["container", "start"],
    "handle_start": ["container", "start"],
    "compose_push": ["compose", "push"],
    "compose_watch": ["compose", "watch"],
    "buildx_prune": ["buildx", "prune"],
}
BAD_TIMEOUTS = [0.0, -1.0, float("inf"), float("-inf"), float("nan"), True, False]


def launch_long(
    client: DockerClient,
    operation: LongLaunch,
    *,
    timeout: float | None,
    stream: bool = False,
) -> object:
    """Dispatch a push, start, watch, or prune scenario with its selected deadline.

    Args:
        client: Client configured to run the local Python driver.
        operation: Entry point to exercise. Push and prune capture output when
            blocking; attached starts return text; watch always streams.
        timeout: Per-launch duration in seconds, or None to inherit the default.
        stream: Return an owned output stream instead of an eager result.

    Returns:
        None for eager push, unattached results and prune, captured text for an
        attached start, or a caller-owned stream.

    Raises:
        ValueError: If timeout is not a finite positive duration or None.
        DockerTimeout: If an eager launch exceeds its deadline. Streams report
            expiry when consumed or exited.
    """
    match operation:
        case "image_push":
            return client.image.push(
                "built", quiet=not stream, stream_logs=stream, timeout=timeout
            )
        case "root_push":
            return client.push(
                "built", quiet=not stream, stream_logs=stream, timeout=timeout
            )
        case "container_start":
            return client.container.start(
                "workload", True, stream=stream, timeout=timeout
            )
        case "root_start":
            return client.start("workload", True, stream=stream, timeout=timeout)
        case "handle_start":
            return Container(
                client.client_config, "workload", is_immutable_id=True
            ).start(True, stream=stream, timeout=timeout)
        case "compose_push":
            return client.compose.push(timeout=timeout)
        case "compose_watch":
            return client.compose.watch("web", timeout=timeout)
        case "buildx_prune":
            return client.buildx.prune(stream_logs=stream, timeout=timeout)
        case unreachable:
            assert_never(unreachable)


def first_record(operation: LongLaunch) -> object:
    """Return the record the driver's first stdout line becomes for an operation."""
    if operation in ("image_push", "root_push"):
        return ("built", b"container-id\n")
    if operation == "buildx_prune":
        return "container-id\n"
    return ("stdout", b"container-id\n")


def owned_stream(result: object) -> ProcessStream | MappedProcessStream[object]:
    """Return a launched stream typed for use as a context manager."""
    assert isinstance(result, (ProcessStream, MappedProcessStream))
    return result


def owned_process(result: object) -> ProcessStream:
    """Return the process stream beneath a launched stream, asserting ownership."""
    assert isinstance(result, (ProcessStream, MappedProcessStream))
    return result._stream if isinstance(result, MappedProcessStream) else result


def launches_of(tmp_path: Path) -> list[list[str]]:
    """Return recorded driver argv, or an empty list when nothing launched."""
    record = tmp_path / "launches.jsonl"
    return launched(tmp_path) if record.exists() else []


@pytest.mark.parametrize("timeout", BAD_TIMEOUTS)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("operation", STREAM_LAUNCHES)
def test_invalid_long_launch_timeout_precedes_every_launch(
    client: DockerClient,
    tmp_path: Path,
    operation: LongLaunch,
    stream: bool,
    timeout: float,
) -> None:
    with pytest.raises(ValueError):
        launch_long(client, operation, timeout=timeout, stream=stream)

    assert launches_of(tmp_path) == []


@pytest.mark.parametrize("timeout", BAD_TIMEOUTS)
def test_invalid_long_launch_timeout_precedes_compose_push_and_empty_requests(
    client: DockerClient, tmp_path: Path, timeout: float
) -> None:
    consumed: list[str] = []

    def references() -> Iterable[str]:
        consumed.append("read")
        yield "built"

    for call in (
        lambda: client.compose.push(timeout=timeout),
        lambda: client.compose.push([], timeout=timeout),
        lambda: client.compose.watch([], timeout=timeout),
        lambda: client.container.start([], timeout=timeout),
        lambda: client.start(references(), timeout=timeout),
        lambda: client.image.push([], timeout=timeout),
        lambda: client.push(references(), timeout=timeout),
        lambda: client.buildx.prune(filters=iter([("until", "1h")]), timeout=timeout),
    ):
        with pytest.raises(ValueError):
            call()

    assert consumed == []
    assert launches_of(tmp_path) == []


def test_empty_long_launches_return_none_without_a_child(
    client: DockerClient, tmp_path: Path
) -> None:
    assert client.image.push([], timeout=5) is None
    assert client.image.push([], stream_logs=True, timeout=5) is None
    assert client.container.start([], timeout=5) is None
    assert client.compose.push([], timeout=5) is None
    assert client.compose.watch([], timeout=5) is None

    assert launches_of(tmp_path) == []


@pytest.mark.parametrize("timeout", [None, 5.0])
@pytest.mark.parametrize("operation", EAGER_LAUNCHES)
def test_eager_long_launches_preserve_results(
    client: DockerClient, operation: LongLaunch, timeout: float | None
) -> None:
    result = launch_long(client, operation, timeout=timeout)

    if operation in ("container_start", "root_start", "handle_start"):
        assert result == "container-id"
    else:
        assert result is None


@pytest.mark.parametrize("operation", STREAM_LAUNCHES)
def test_long_launch_streams_preserve_records_and_close(
    client: DockerClient, operation: LongLaunch
) -> None:
    result = launch_long(client, operation, timeout=5, stream=True)

    with owned_stream(result) as stream:
        assert list(stream) == [first_record(operation)]
    assert_released(owned_process(result))


def test_unattached_start_and_detached_forms_use_their_own_launch(
    client: DockerClient, tmp_path: Path
) -> None:
    client.start("workload", timeout=5)
    Container(client.client_config, "workload", is_immutable_id=True).start(timeout=5)

    assert [args[:2] for args in launches_of(tmp_path)] == [["container", "start"]] * 2


@pytest.mark.parametrize("prune", [False, True])
def test_watch_keeps_explicit_prune_value_with_a_deadline(
    client: DockerClient, tmp_path: Path, prune: bool
) -> None:
    watch = client.compose.watch(["web"], prune=prune, timeout=5)
    assert watch is not None
    with watch:
        list(watch)

    assert f"--prune={str(prune).lower()}" in launches_of(tmp_path)[0]


@pytest.mark.parametrize("stream", [False, True])
def test_buildx_prune_deadline_is_local_and_not_a_cli_flag(
    client: DockerClient, tmp_path: Path, stream: bool
) -> None:
    result = client.buildx.prune(all=True, stream_logs=stream, timeout=5)
    if stream:
        assert isinstance(result, MappedProcessStream)
        with result:
            list(result)

    argv = launches_of(tmp_path)[0]
    assert argv[:2] == ["buildx", "prune"]
    assert "--force" in argv
    assert not any("timeout" in arg for arg in argv)


@pytest.mark.parametrize("operation", EAGER_LAUNCHES)
def test_eager_long_launch_timeout_keeps_partial_output_and_reaps_child(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: LongLaunch,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    prefix = LAUNCH_PREFIX[operation]

    with pytest.raises(DockerTimeout) as expired:
        launch_long(client, operation, timeout=1.0)

    assert expired.value.stdout is not None
    assert "container-id" in expired.value.stdout
    assert expired.value.stderr == "diagnostic\ufffd"
    assert all(word in expired.value.docker_command for word in prefix)
    assert_reaped(tmp_path, prefix)


@pytest.mark.parametrize("operation", EAGER_LAUNCHES)
def test_eager_long_launch_inherits_the_client_default_when_timeout_is_none(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: LongLaunch,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    client.client_config.default_timeout = 0.8

    with pytest.raises(DockerTimeout):
        launch_long(client, operation, timeout=None)

    assert_reaped(tmp_path, LAUNCH_PREFIX[operation])


@pytest.mark.parametrize(
    ("operation", "stream"),
    [(operation, False) for operation in EAGER_LAUNCHES]
    + [(operation, True) for operation in STREAM_LAUNCHES],
)
def test_natural_nonzero_exit_keeps_exception_type(
    client: DockerClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: LongLaunch,
    stream: bool,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_FAIL_ON", " ".join(LAUNCH_PREFIX[operation]))

    with pytest.raises(DockerException) as failed:
        result = launch_long(client, operation, timeout=5, stream=stream)
        if stream:
            with owned_stream(result) as records:
                list(records)

    assert not isinstance(failed.value, DockerTimeout)
    assert failed.value.stderr is not None
    assert "induced failure" in failed.value.stderr


@pytest.mark.parametrize("operation", STREAM_LAUNCHES)
def test_long_launch_stream_timeout_keeps_output_and_reports_once(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, operation: LongLaunch
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    result = launch_long(client, operation, timeout=1.0, stream=True)
    raw = owned_process(result)
    try:
        assert raw._released.wait(timeout=3)

        with pytest.raises(DockerTimeout) as expired:
            next(owned_stream(result))

        assert expired.value.stdout is not None
        assert "container-id" in expired.value.stdout
        assert expired.value.stderr == "diagnostic\ufffd"
        assert_released(raw)
        with owned_stream(result) as closed:
            assert list(closed) == []
    finally:
        owned_stream(result).close()


@pytest.mark.parametrize("operation", STREAM_LAUNCHES)
def test_long_launch_stream_deadline_does_not_interrupt_post_exit_drain(
    client: DockerClient, monkeypatch: pytest.MonkeyPatch, operation: LongLaunch
) -> None:
    result = launch_long(client, operation, timeout=5.0, stream=True)
    raw = owned_process(result)
    with owned_stream(result) as stream:
        assert raw._process.wait(timeout=3) == 0
        assert raw._deadline is not None
        expired_at = raw._deadline + 1
        monkeypatch.setattr("pywhales._process_stream.monotonic", lambda: expired_at)

        assert list(stream) == [first_record(operation)]

    assert_released(raw)


@pytest.mark.parametrize("consume", [False, True])
@pytest.mark.parametrize("operation", STREAM_LAUNCHES)
def test_long_launch_stream_early_close_reaps_child(
    client: DockerClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: LongLaunch,
    consume: bool,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    result = launch_long(client, operation, timeout=5, stream=True)
    raw = owned_process(result)
    with owned_stream(result) as stream:
        if consume:
            assert next(stream) == first_record(operation)
        stream.close()
        stream.close()

    assert list(owned_stream(result)) == []
    assert_released(raw)


def push_names(count: int) -> list[str]:
    """Return distinct image names that the driver's image inspect accepts."""
    return [f"img{index}" for index in range(count)]


def set_delays(monkeypatch: pytest.MonkeyPatch, delays: dict[str, float]) -> None:
    """Make driver launches whose argv contains a key's words sleep that long."""
    monkeypatch.setenv("PYWHALES_TEST_DELAYS", json.dumps(delays))


@pytest.mark.parametrize("names", [push_names(1), push_names(2)])
@pytest.mark.parametrize("stream", [False, True])
def test_push_inspect_preflight_is_outside_the_explicit_budget(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    names: list[str],
    stream: bool,
) -> None:
    set_delays(monkeypatch, {"image inspect": 1.2})

    started = perf_counter()
    result = client.image.push(names, quiet=not stream, stream_logs=stream, timeout=0.6)
    if stream:
        assert result is not None
        with result:
            assert sorted(name for name, _ in result) == sorted(names)
    else:
        assert result is None

    assert perf_counter() - started > 1.2
    commands = [args[:2] for args in launches_of(tmp_path)]
    assert commands[0] == ["image", "inspect"]
    assert commands.count(["image", "push"]) == len(names)


def test_blocking_pool_tasks_are_timed_from_their_own_launch(
    client: DockerClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_delays(monkeypatch, {"image push": 0.5})
    names = push_names(12)

    started = perf_counter()
    client.image.push(names, quiet=True, timeout=1.0)

    assert perf_counter() - started > 1.0
    assert [args[:2] for args in launches_of(tmp_path)].count(["image", "push"]) == 12


def test_lazy_stream_children_are_timed_from_their_own_launch(
    client: DockerClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_delays(monkeypatch, {"image push": 0.5})
    names = push_names(12)

    stream = client.image.push(names, stream_logs=True, timeout=1.0)
    assert stream is not None
    sleep(1.2)
    assert [args[:2] for args in launches_of(tmp_path)] == [["image", "inspect"]]

    started = perf_counter()
    with stream:
        records = list(stream)

    assert perf_counter() - started > 1.0
    assert sorted(name for name, _ in records) == sorted(names)


@pytest.mark.parametrize("stream", [False, True])
def test_every_pool_and_queued_child_expires_and_is_reaped(
    client: DockerClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    names = push_names(6)

    with pytest.raises(DockerTimeout) as expired:
        result = client.image.push(
            names, quiet=not stream, stream_logs=stream, timeout=1.0
        )
        if stream:
            assert result is not None
            with result:
                list(result)

    assert "push" in expired.value.docker_command
    assert_reaped(tmp_path, ["image", "push"])


def test_first_multi_image_failure_cancels_siblings_without_a_timeout(
    client: DockerClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYWHALES_TEST_BLOCK", "1")
    monkeypatch.setenv("PYWHALES_TEST_FAIL_ON", "image push img0")

    stream = client.image.push(push_names(6), stream_logs=True, timeout=30)
    assert stream is not None
    with pytest.raises(DockerException) as failed, stream:
        list(stream)

    assert not isinstance(failed.value, DockerTimeout)
    assert failed.value.stderr is not None
    assert "induced failure" in failed.value.stderr
    assert_reaped(tmp_path, ["image", "push"])
