import json
from collections.abc import Mapping, Sequence
from inspect import Parameter, signature
from pathlib import Path
from typing import Literal, assert_never, get_overloads
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

from pywhales import Container, DockerClient, Image
from pywhales.components.buildx import cli_wrapper as buildx
from pywhales.components.compose import cli_wrapper as compose
from pywhales.components.container import cli_wrapper as container
from pywhales.components.service import cli_wrapper as service
from pywhales.components.system import cli_wrapper as system
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


@pytest.mark.parametrize("timeout", [None, 2.5])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("operation", ["container", "root", "compose", "service"])
def test_log_deadline_forwarding_keeps_inspection_unbudgeted(
    client: DockerClient,
    mocker: MockerFixture,
    operation: Literal["container", "root", "compose", "service"],
    stream: bool,
    timeout: float | None,
) -> None:
    module = {
        "container": container,
        "root": container,
        "compose": compose,
        "service": service,
    }[operation]
    inspect = mocker.patch.object(
        module, "run", return_value='[{"Id":"workload","ID":"workload"}]'
    )
    raw = mocker.MagicMock()
    raw.__iter__.return_value = iter([("stdout", b"app | log\n")])
    launch = mocker.patch.object(module, "stream_stdout_and_stderr", return_value=raw)
    client.client_config.compose_project_name = "observation"

    match operation:
        case "container":
            result = client.container.logs(
                "workload", tail=3, follow=True, stream=stream, timeout=timeout
            )
        case "root":
            result = client.logs(
                "workload", tail=3, follow=True, stream=stream, timeout=timeout
            )
        case "compose":
            result = client.compose.logs(
                "workload", "3", True, stream=stream, timeout=timeout
            )
        case "service":
            result = client.service.logs(
                "workload", tail=3, follow=True, stream=stream, timeout=timeout
            )
        case unreachable:
            assert_never(unreachable)

    assert result is raw if stream else result == "app | log\n"
    group = "container" if operation == "root" else operation
    expected = ["docker-test", group]
    if operation == "compose":
        expected += ["--project-name", "observation"]
    expected += ["logs"]
    if operation == "compose":
        expected += ["--no-color"]
    expected += ["--tail", "3", "--follow", "workload"]
    assert [str(arg) for arg in launch.call_args.args[0]] == expected
    assert launch.call_args.kwargs == {"timeout": timeout}
    if operation == "compose":
        inspect.assert_not_called()
    else:
        inspect.assert_called_once_with(["docker-test", group, "inspect", "workload"])


def test_container_handle_forwards_log_deadline(
    client: DockerClient, mocker: MockerFixture
) -> None:
    handle = Container(client.client_config, "workload", is_immutable_id=True)
    logs = mocker.patch.object(container.ContainerCLI, "logs", return_value="log\n")

    assert handle.logs(tail=3, timeout=2.5) == "log\n"

    logs.assert_called_once_with(
        handle,
        details=False,
        since=None,
        tail=3,
        timestamps=False,
        until=None,
        timeout=2.5,
    )


@pytest.mark.parametrize("timeout", [None, 2.5])
@pytest.mark.parametrize("operation", ["compose", "system"])
def test_event_deadline_forwarding_preserves_flags_and_mapping(
    client: DockerClient,
    mocker: MockerFixture,
    operation: Literal["compose", "system"],
    timeout: float | None,
) -> None:
    module = compose if operation == "compose" else system
    raw = mocker.MagicMock()
    raw.__iter__.return_value = iter(
        [
            ("stderr", b"not an event\n"),
            ("stdout", b'{"action":"start","Action":"start","service":"web"}\n'),
        ]
    )
    launch = mocker.patch.object(module, "stream_stdout_and_stderr", return_value=raw)
    client.client_config.compose_project_name = "observation"

    if operation == "compose":
        events = client.compose.events(["web"], timeout=timeout)
        expected = [
            "docker-test",
            "compose",
            "--project-name",
            "observation",
            "events",
            "--json",
            "web",
        ]
    else:
        events = client.system.events(filters={"container": "web"}, timeout=timeout)
        expected = [
            "docker-test",
            "system",
            "events",
            "--format",
            "{{json .}}",
            "--filter",
            "container=web",
        ]
    with events:
        result = list(events)

    assert [event.action for event in result] == ["start"]
    assert list(launch.call_args.args[0]) == expected
    assert launch.call_args.kwargs == {"timeout": timeout}


def test_subscription_deadlines_are_keyword_only_in_every_overload(
    client: DockerClient,
) -> None:
    for method in (
        container.Container.logs,
        client.container.logs,
        client.logs,
        client.compose.logs,
        client.service.logs,
        client.compose.events,
        client.system.events,
    ):
        for variant in (method, *get_overloads(method)):
            parameter = signature(variant).parameters["timeout"]
            assert parameter.kind is Parameter.KEYWORD_ONLY
            assert parameter.default is None
