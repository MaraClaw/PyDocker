import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, assert_never

import pytest
from pytest_mock import MockerFixture

from pywhales import Container, DockerClient, Image, utils
from pywhales.components.buildx.cli_wrapper import BakeResult
from pywhales.components.compose.models import ComposeEvent
from pywhales.components.system.models import DockerEvent
from pywhales.exceptions import DockerTimeout
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
    driver = tmp_path / "driver.py"
    driver.write_text(
        """import json
import os
import sys
import threading
from pathlib import Path

record = Path(sys.argv[1])
args = sys.argv[2:]
with record.open('a') as output:
    output.write(json.dumps(args) + '\\n')
if args[:2] == ['buildx', 'inspect']:
    print('Name: selected')
elif args[:2] == ['buildx', 'ls']:
    print(json.dumps({'Name': 'selected', 'Driver': 'docker'}))
elif args[:2] == ['image', 'inspect']:
    print('[{"Id":"sha256:built"}]')
elif args[:2] == ['container', 'inspect']:
    print('[{"Id":"container-id"}]')
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
        if 'logs' in args or 'events' in args:
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
