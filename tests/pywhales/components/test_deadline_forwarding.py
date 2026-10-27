import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, assert_never
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

from pywhales import DockerClient, Image
from pywhales.components.buildx import cli_wrapper as buildx
from pywhales.components.compose import cli_wrapper as compose
from pywhales.components.container import cli_wrapper as container
from pywhales.exceptions import DockerTimeout


@pytest.fixture
def client() -> DockerClient:
    result = DockerClient()
    result.client_config._client_call_with_path = ["docker-test"]
    return result


@pytest.mark.parametrize("timeout", [None, 2.5])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("operation", ["run", "execute", "compose"])
def test_selected_container_and_compose_budget_forwarding(
    client: DockerClient,
    mocker: MockerFixture,
    operation: Literal["run", "execute", "compose"],
    stream: bool,
    timeout: float | None,
) -> None:
    module = compose if operation == "compose" else container
    execute = mocker.patch.object(
        module, "stream_stdout_and_stderr" if stream else "run", return_value="result"
    )

    match operation:
        case "run":
            client.run("app", pull="never", stream=stream, timeout=timeout)
        case "execute":
            client.execute(
                "app", ["cmd"], interactive=True, stream=stream, timeout=timeout
            )
        case "compose":
            client.compose.run("app", tty=False, stream=stream, timeout=timeout)
        case unreachable:
            assert_never(unreachable)

    execute.assert_called_once()
    assert execute.call_args.kwargs["timeout"] == timeout
    if operation != "compose":
        assert execute.call_args.kwargs["pass_fds"] == ()
    if operation == "execute" and stream:
        assert execute.call_args.kwargs["pipe_stdin"] is True
    if not stream:
        assert execute.call_args.kwargs["tty"] is False


@pytest.mark.parametrize("pull", ["missing", "always"])
def test_container_pull_is_outside_selected_budget(
    client: DockerClient, mocker: MockerFixture, pull: Literal["missing", "always"]
) -> None:
    image = mocker.patch("pywhales.components.image.cli_wrapper.ImageCLI").return_value
    execute = mocker.patch.object(container, "run", return_value="done")

    result = client.run("app", pull=pull, timeout=2.5)

    assert result == "done"
    if pull == "missing":
        image._pull_if_necessary.assert_called_once_with("app")
        image.pull.assert_not_called()
    else:
        image.pull.assert_called_once_with("app")
        image._pull_if_necessary.assert_not_called()
    assert execute.call_args.kwargs["timeout"] == 2.5


@pytest.mark.parametrize("driver", ["docker", "docker-container"])
@pytest.mark.parametrize("expire", [False, True])
def test_build_inspections_are_unbudgeted_and_iidfile_is_cleaned(
    client: DockerClient, mocker: MockerFixture, driver: str, expire: bool
) -> None:
    iidfiles: list[Path] = []

    def execute(args: Sequence[object], **kwargs: object) -> str:
        command = [str(arg) for arg in args]
        match command[2]:
            case "inspect":
                assert "timeout" not in kwargs
                return "Name: selected\n"
            case "ls":
                assert "timeout" not in kwargs
                return json.dumps({"Name": "selected", "Driver": driver})
            case "build":
                assert kwargs["timeout"] == 2.5
                if "--iidfile" in command:
                    iidfile = Path(command[command.index("--iidfile") + 1])
                    iidfiles.append(iidfile)
                    iidfile.write_text("sha256:built")
                if expire:
                    raise DockerTimeout(command, -9)
                return "done"
            case _:
                pytest.fail(f"Unexpected command: {command}")

    commands = mocker.patch.object(buildx, "run", side_effect=execute)
    image = Image(client.client_config, "sha256:built", is_immutable_id=True)
    inspect = mocker.patch(
        "pywhales.components.image.cli_wrapper.ImageCLI.inspect", return_value=image
    )

    if expire:
        with pytest.raises(DockerTimeout):
            client.build(".", load=True, tags="app", timeout=2.5)
        inspect.assert_not_called()
    else:
        assert client.build(".", load=True, tags="app", timeout=2.5) is image
        inspect.assert_called_once_with("sha256:built" if driver == "docker" else "app")

    assert commands.call_count == 3
    assert len(iidfiles) == (1 if driver == "docker" else 0)
    assert all(not path.parent.exists() for path in iidfiles)


@pytest.mark.parametrize("mode", ["push", "export", "report"])
def test_build_without_inspections_receives_selected_budget(
    client: DockerClient,
    mocker: MockerFixture,
    mode: Literal["push", "export", "report"],
) -> None:
    commands = mocker.patch.object(buildx, "run", return_value="report")
    inspect = mocker.patch.object(
        buildx.BuildxCLI, "inspect", side_effect=AssertionError("unexpected inspection")
    )

    match mode:
        case "push":
            result = client.build(".", push=True, timeout=2.5)
        case "export":
            result = client.build(
                ".", output={"type": "local", "dest": "out"}, timeout=2.5
            )
        case "report":
            result = client.build(".", call="check", timeout=2.5)
        case unreachable:
            assert_never(unreachable)

    assert result == ("report" if mode == "report" else None)
    commands.assert_called_once()
    assert commands.call_args.kwargs["timeout"] == 2.5
    inspect.assert_not_called()


@pytest.mark.parametrize("call", [None, "build", "check", "outline", "targets"])
@pytest.mark.parametrize("return_config", [False, True])
def test_bake_each_launch_receives_fresh_duration(
    client: DockerClient,
    mocker: MockerFixture,
    call: buildx.BuildxCall | None,
    return_config: bool,
) -> None:
    commands = mocker.patch.object(buildx, "run", return_value='{"target":{}}')

    client.buildx.bake(
        "app",
        call=call,
        return_config=return_config,
        variables={"TAG": "value"},
        timeout=2.5,
    )

    normal_build = call in (None, "build")
    assert commands.call_count == (2 if return_config and normal_build else 1)
    for launched in commands.call_args_list:
        assert launched.kwargs["timeout"] == 2.5
        assert launched.kwargs["env"] == {"TAG": "value"}
    if commands.call_count == 2:
        assert "--print" not in commands.call_args_list[0].args[0]
        assert "--print" in commands.call_args_list[1].args[0]


def test_bake_expired_build_skips_config_launch(
    client: DockerClient, mocker: MockerFixture
) -> None:
    commands = mocker.patch.object(
        buildx, "run", side_effect=DockerTimeout(["docker-test"], -9)
    )

    with pytest.raises(DockerTimeout):
        client.buildx.bake(timeout=2.5)

    commands.assert_called_once()


def test_buildx_stream_adapter_forwards_environment_and_deadline(
    mocker: MockerFixture,
) -> None:
    started = Mock()
    started.__iter__ = Mock(return_value=iter([("stderr", b"log\xff\n")]))
    launch = mocker.patch.object(
        buildx, "stream_stdout_and_stderr", return_value=started
    )
    variables: Mapping[str, str] = {"TAG": "value"}

    logs = buildx.stream_buildx_logs(
        ["docker-test", "buildx", "bake"], variables, timeout=2.5
    )

    launch.assert_called_once_with(
        ["docker-test", "buildx", "bake"], env=variables, timeout=2.5
    )
    assert next(logs) == "log\ufffd\n"
    logs.close()
    started.close.assert_called_once()
