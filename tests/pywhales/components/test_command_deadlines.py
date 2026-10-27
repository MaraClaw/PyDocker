import sys
from pathlib import Path
from typing import Literal, assert_never

import pytest

from pywhales import Container, DockerClient, Image
from pywhales.components.buildx.cli_wrapper import BakeResult
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
else:
    if '--iidfile' in args:
        Path(args[args.index('--iidfile') + 1]).write_text('sha256:built')
    if '--print' in args:
        print('{"target":{"app":{"context":"."}}}', flush=True)
    else:
        print('container-id', flush=True)
    block = os.environ.get('PYWHALES_TEST_BLOCK')
    if block == '1' or (block == 'print' and '--print' in args):
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
