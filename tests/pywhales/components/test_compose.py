import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from os import makedirs, remove
from pathlib import Path
from types import FrameType
from typing import Never
from unittest.mock import Mock, patch

import pytest
import pytz
from pydantic import ValidationError

import pywhales
from pywhales import Container, DockerClient, DockerException
from pywhales.components.compose.models import ComposeConfig, ComposeEvent
from pywhales.components.container.models import ContainerState
from pywhales.components.top import ComposeTopResult, TopParseError
from pywhales.exceptions import NoSuchImage
from pywhales.test_utils import get_all_jsons, random_name
from pywhales.utils import PROJECT_ROOT, stream_stdout_and_stderr


@patch("pywhales.components.compose.cli_wrapper.run")
@pytest.mark.parametrize("services", [None, "web", ["web", "worker"]])
def test_top_uses_project_configuration_when_selecting_services(
    run_mock: Mock, services: str | list[str] | None
) -> None:
    client = DockerClient(
        client_call=[sys.executable, "engine"],
        context="remote",
        compose_files=["first.yml", "second.yml"],
        compose_profiles=["debug"],
        compose_env_files=["vars.env"],
        compose_project_name="owned",
        compose_project_directory="/project",
        compose_compatibility=True,
    )
    run_mock.return_value = (
        "SERVICE # PID CMD\nweb 1 7 python  server.py --name two words"
    )

    result = client.compose.top(services)

    assert isinstance(result, ComposeTopResult)
    assert result.sections[0].container is None
    assert result.sections[0].headings == ("SERVICE", "#", "PID", "CMD")
    assert result.sections[0].rows == (
        ("web", "1", "7", "python  server.py --name two words"),
    )
    selected = (
        []
        if services is None
        else [services]
        if isinstance(services, str)
        else services
    )
    run_mock.assert_called_once_with(
        [
            sys.executable,
            "engine",
            "--context",
            "remote",
            "compose",
            "--file",
            "first.yml",
            "--file",
            "second.yml",
            "--profile",
            "debug",
            "--env-file",
            "vars.env",
            "--project-name",
            "owned",
            "--project-directory",
            "/project",
            "--compatibility",
            "top",
            *selected,
        ]
    )


@patch("pywhales.components.compose.cli_wrapper.run")
def test_top_keeps_sections_separate_when_compose_groups_containers(
    run_mock: Mock,
) -> None:
    client = DockerClient(client_call=[sys.executable])
    output = "owned-web-1\nUID PID CMD\n---------------------\nroot 7 python  web.py\n\nowned-web-2\nPID USER COMMAND\n8 worker sleep 300\n"
    run_mock.return_value = output

    result = client.compose.top()

    assert result.raw_output == output
    assert [
        (section.container, section.headings, section.rows)
        for section in result.sections
    ] == [
        ("owned-web-1", ("UID", "PID", "CMD"), (("root", "7", "python  web.py"),)),
        ("owned-web-2", ("PID", "USER", "COMMAND"), (("8", "worker", "sleep 300"),)),
    ]


@pytest.mark.parametrize("raw", [False, True])
@patch("pywhales.components.compose.cli_wrapper.run")
def test_top_is_noop_when_service_selection_is_empty(run_mock: Mock, raw: bool) -> None:
    client = DockerClient(client_call=["nonexistent-executable-for-top-noop"])

    result = client.compose.top([], raw=raw)

    assert result == ("" if raw else ComposeTopResult("", ()))
    run_mock.assert_not_called()


@patch("pywhales.components.compose.cli_wrapper.run")
@pytest.mark.parametrize("output", ["", "\n\n"])
def test_top_returns_empty_sections_when_project_has_no_processes(
    run_mock: Mock, output: str
) -> None:
    client = DockerClient(client_call=[sys.executable])
    run_mock.return_value = output

    result = client.compose.top()

    assert result == ComposeTopResult(output, ())


@patch("pywhales.components.compose.cli_wrapper.run")
@pytest.mark.parametrize(
    "output",
    ["owned-web-1", "owned-web-1\nPID CMD\n7", "SERVICE # CMD PID\nweb 1 sleep 300 7"],
)
def test_top_rejects_malformed_sections_when_parsing_is_requested(
    run_mock: Mock, output: str
) -> None:
    client = DockerClient(client_call=[sys.executable])
    run_mock.return_value = output

    with pytest.raises(TopParseError, match="raw=True"):
        client.compose.top()


@patch(
    "pywhales.components.compose.cli_wrapper.run", return_value="unrecognized layout\n"
)
def test_top_exposes_raw_output_when_compose_layout_is_unknown(run_mock: Mock) -> None:
    client = DockerClient(client_call=[sys.executable])

    result = client.compose.top("web", raw=True)

    assert result == "unrecognized layout\n"


@patch("pywhales.utils.subprocess.run")
@pytest.mark.parametrize("raw", [False, True])
def test_top_retains_compose_exception_when_service_is_missing(
    run_mock: Mock, raw: bool
) -> None:
    client = DockerClient(client_call=[sys.executable])
    run_mock.return_value = subprocess.CompletedProcess(
        [], 1, b"", b"no such service: absent"
    )

    with pytest.raises(DockerException) as error:
        client.compose.top("absent", raw=raw)

    assert error.value.stderr == "no such service: absent"
    assert error.value.docker_command[-2:] == ["top", "absent"]


def test_top_preserves_attribution_when_inspecting_owned_live_project(
    docker_client: DockerClient, tmp_path: Path
) -> None:
    compose_file = tmp_path / "top-compose.yml"
    compose_file.write_text(
        'services:\n  web:\n    image: python:3.14.8-slim-trixie\n    command: ["sleep", "300"]\n'
        '  worker:\n    image: python:3.14.8-slim-trixie\n    command: ["sleep", "301"]\n'
    )
    project = DockerClient(
        client_call=docker_client.client_config.client_call,
        compose_files=[compose_file],
        compose_project_name=f"top-{random_name()}",
    )
    try:
        project.compose.up(detach=True)

        result = project.compose.top("web")

        assert result.sections
        assert all(
            "sleep 301" not in row[-1]
            for section in result.sections
            for row in section.rows
        )
        assert any(
            "sleep 300" in row[-1]
            for section in result.sections
            for row in section.rows
        )
        for section in result.sections:
            if section.container is None:
                assert section.headings[:2] == ("SERVICE", "#")
                assert all(row[0] == "web" for row in section.rows)
            else:
                assert "web" in section.container
    finally:
        project.compose.down(timeout=0)


pytestmark = pytest.mark.skipif(
    not pywhales.docker.compose.is_installed(),
    reason="Those tests need docker compose.",
)

docker = DockerClient(
    compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
    compose_compatibility=True,
)


def _container_state(container: Container) -> ContainerState:
    state = container.state
    assert state is not None
    return state


def _collect_logs(logs: Iterable[tuple[str, bytes]] | None) -> list[tuple[str, bytes]]:
    assert logs is not None
    return list(logs)


def mock_KeyboardInterrupt(signum: int, frame: FrameType | None) -> Never:
    raise KeyboardInterrupt("Time is up")


def test_build_empty_list_of_services() -> None:
    previous_images = set(docker.image.list())
    docker.compose.build([])
    assert previous_images == set(docker.image.list())


@patch("pywhales.components.compose.cli_wrapper.run")
def test__build_with_dependencies(run_mock: Mock) -> None:
    test_services = ["test_a", "test_b"]
    docker.compose.build(services=test_services, with_dependencies=True)
    run_mock.assert_called_once()
    assert "--with-dependencies" in run_mock.call_args.args[0]


def test_create_empty_list_of_services() -> None:
    previous_containers = set(docker.ps(all=True))
    docker.compose.create([])
    assert previous_containers == set(docker.ps(all=True))


def test_kill_empty_list_of_services() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    time.sleep(1)
    all_running_containers = set(docker.ps())
    docker.compose.kill([])
    assert all_running_containers == set(docker.ps())
    docker.compose.down(timeout=1)


def test_wait_for_service() -> None:
    docker = pywhales.DockerClient(
        compose_files=[Path(__file__).parent / "compose-with-healthcheck.yml"]
    )
    # ensure environment is clean
    docker.compose.down(timeout=1, volumes=True)

    docker.compose.up(["my_redis"], detach=True)
    # this should be too fast and the healthcheck shouldn't be ready
    container = docker.compose.ps()[0]
    assert container.state is not None
    assert container.state.health is not None
    assert container.state.health.status == "starting"
    docker.compose.down(timeout=1, volumes=True)

    # now we use wait, and we check that it's healthy as soon as the function returns
    docker.compose.up(["my_redis"], detach=True, wait=True)
    container = docker.compose.ps()[0]
    assert container.state is not None
    assert container.state.health is not None
    assert container.state.health.status == "healthy"
    docker.compose.down(timeout=1, volumes=True)

    # now we use wait with timeout and check that timeout respects
    with pytest.raises(DockerException) as exc_info:
        _collect_logs(
            docker.compose.up(
                ["unhealthy_service"],
                detach=True,
                wait=True,
                wait_timeout=1,
                stream_logs=True,
            )
        )
    stderr = exc_info.value.stderr
    assert stderr is not None
    assert "application not healthy after" in stderr
    docker.compose.down(timeout=1, volumes=True)


def test_pause_empty_list_of_services() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    time.sleep(1)
    number_of_paused_containers = len(
        [x for x in docker.ps() if _container_state(x).paused is True]
    )
    docker.compose.pause([])
    assert number_of_paused_containers == len(
        [x for x in docker.ps() if _container_state(x).paused is True]
    )
    docker.compose.down(timeout=1)


def test_remove_empty_list_of_services() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    time.sleep(1)
    number_of_containers = len(docker.ps())
    docker.compose.rm([], stop=True)
    assert number_of_containers == len(docker.ps())
    docker.compose.down(timeout=1)


def test_pull_empty_list_of_services() -> None:
    len_list_of_images = len(docker.image.list())
    docker.compose.pull([])
    assert len_list_of_images == len(docker.image.list())


def test_push_empty_list_of_services() -> None:
    docker.compose.push([])


def test_compose_project_name() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_project_name="dudu",
        compose_compatibility=True,
    )
    docker.compose.up(["busybox", "alpine"], detach=True)
    containers = docker.compose.ps()
    container_names = {x.name for x in containers}
    assert container_names == {"dudu_busybox_1", "dudu_alpine_1"}
    docker.compose.down(timeout=1)


def test_docker_compose_build() -> None:
    docker.compose.build()
    docker.compose.build(["my_service"])
    docker.compose.build("my_service")
    docker.image.remove("some_random_image")


def test_docker_compose_build_stream() -> None:
    logs = _collect_logs(docker.compose.build(["my_service"], stream_logs=True))
    assert len(logs) >= 3
    logs_as_big_binary = b""
    for log_type, log_value in logs:
        assert log_type in ("stdout", "stderr")
        logs_as_big_binary += log_value

    assert b"load .dockerignore" in logs_as_big_binary
    assert b"DONE" in logs_as_big_binary

    docker.image.remove("some_random_image")


def test_docker_compose_build_with_arguments() -> None:
    docker.compose.build(
        build_args={"PYTHON_VERSION": "3.7"},
        cache=False,
        progress="plain",
        pull=True,
        quiet=True,
    )
    docker.image.remove("some_random_image")


def test_docker_compose_up_down() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/dummy_compose_ends_quickly.yml"
        ],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox", "alpine"])
    output_of_down = docker.compose.down(timeout=1)
    assert output_of_down is None
    assert docker.compose.ps() == []


@pytest.mark.parametrize(
    "dependencies_value, expected_running_containers", [(True, 2), (False, 1)]
)
def test_docker_compose_up_dependencies(
    dependencies_value: bool, expected_running_containers: int
) -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_compatibility=True,
    )

    try:
        docker.compose.up(
            ["busybox-2-electric-boogaloo"],
            dependencies=dependencies_value,
            detach=True,
        )
        running_containers = docker.compose.ps()
    finally:
        docker.compose.down(timeout=1)

    assert len(running_containers) == expected_running_containers


def test_docker_compose_up_down_streaming() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/dummy_compose_ends_quickly.yml"
        ],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox", "alpine"])
    logs = _collect_logs(docker.compose.down(timeout=1, stream_logs=True))

    assert len(logs) >= 3
    logs_as_big_binary = b""
    for log_type, log_value in logs:
        assert log_type in ("stdout", "stderr")
        logs_as_big_binary += log_value

    assert b"Removed" in logs_as_big_binary
    assert b"Container components_alpine_1" in logs_as_big_binary


def test_docker_compose_down_services() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT
            / "tests/pywhales/components/dummy_compose_with_container_names.yml"
        ],
        compose_compatibility=True,
        compose_project_name="test_docker_compose_down_services",
    )

    docker.compose.up(["busybox", "alpine"], detach=True)
    assert len(docker.compose.ps()) == 2

    output_of_down = docker.compose.down(services=["busybox"])
    assert output_of_down is None
    assert len(docker.compose.ps()) == 1
    active_container = docker.compose.ps()[0]
    assert active_container.name == "alpine", (
        f"actual container name: {active_container.name}"
    )

    output_of_down = docker.compose.down(services=["alpine"])
    assert output_of_down is None
    assert docker.compose.ps() == []


def test_docker_compose_down_no_services() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT
            / "tests/pywhales/components/dummy_compose_with_container_names.yml"
        ],
        compose_compatibility=True,
        compose_project_name="test_docker_compose_down_no_services",
    )
    docker.compose.up(["busybox", "alpine"], detach=True)
    initial_containers = docker.compose.ps()
    assert len(initial_containers) == 2

    output_of_down = docker.compose.down(services=[])
    assert output_of_down is None
    final_containers = docker.compose.ps()
    assert final_containers == initial_containers

    output_of_down = docker.compose.down()
    assert output_of_down is None
    assert docker.compose.ps() == []


@patch("pywhales.components.compose.cli_wrapper.run")
def test_docker_compose_cli_down_all_services(run_mock: Mock) -> None:
    docker.compose.down()
    run_mock.assert_called_once_with(
        [*docker.client_config.docker_compose_cmd, "down"],
        capture_stderr=False,
        capture_stdout=False,
    )


@patch("pywhales.components.compose.cli_wrapper.run")
def test_docker_compose_cli_down_multiple_services(run_mock: Mock) -> None:
    test_services = ["test_a", "test_b"]
    docker.compose.down(services=test_services)
    run_mock.assert_called_once_with(
        [*docker.client_config.docker_compose_cmd, "down", *test_services],
        capture_stderr=False,
        capture_stdout=False,
    )


@patch("pywhales.components.compose.cli_wrapper.run")
def test_docker_compose_cli_down_single_service(run_mock: Mock) -> None:
    test_service = "test_a"
    docker.compose.down(services=test_service)
    run_mock.assert_called_once_with(
        [*docker.client_config.docker_compose_cmd, "down", test_service],
        capture_stderr=False,
        capture_stdout=False,
    )


@patch("pywhales.components.compose.cli_wrapper.run")
def test_docker_compose_cli_down_services_no_op(run_mock: Mock) -> None:
    docker.compose.down(services=[])
    run_mock.assert_not_called()


def test_no_containers() -> None:
    assert docker.compose.ps() == []


def test_docker_compose_up_detach_down() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    docker.compose.down(timeout=1)


def test_docker_compose_up_detach_down_with_scales() -> None:
    docker.compose.up(
        ["my_service", "busybox", "alpine"],
        detach=True,
        scales={"busybox": 2, "alpine": 3},
    )
    assert len(docker.compose.ps()) == 6

    docker.compose.up(
        ["my_service", "busybox", "alpine"],
        detach=True,
        scales={"busybox": 2, "alpine": 5},
    )
    assert len(docker.compose.ps()) == 8

    docker.compose.down(timeout=1)


def test_docker_compose_pause_unpause() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    docker.compose.pause()
    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.paused is True

    docker.compose.unpause()
    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.paused is not True

    docker.compose.pause(["my_service", "busybox"])

    assert (
        _container_state(docker.container.inspect("components_my_service_1")).paused
        is True
    )
    assert (
        _container_state(docker.container.inspect("components_busybox_1")).paused
        is True
    )

    docker.compose.down(timeout=1)


def test_docker_compose_create_down() -> None:
    create_result = docker.compose.create(pull="missing")
    assert create_result is None
    docker.compose.down()


def test_docker_compose_create_stream_down() -> None:
    docker.compose.down()
    logs = _collect_logs(
        docker.compose.create(["my_service", "busybox", "alpine"], stream_logs=True)
    )
    assert len(logs) >= 2
    full_logs_as_binary = b"".join(log for _, log in logs)
    assert b"Creating" in full_logs_as_binary
    assert b"components_busybox_1" in full_logs_as_binary
    docker.compose.down()


def test_docker_compose_config() -> None:
    compose_config = docker.compose.config()
    assert compose_config.services is not None
    assert compose_config.services["alpine"].image == "alpine:latest"

    compose_config = docker.compose.config(return_json=True)
    services = compose_config["services"]
    assert isinstance(services, dict)
    alpine = services["alpine"]
    assert isinstance(alpine, dict)
    assert alpine["image"] == "alpine:latest"


def test_docker_compose_create_extra_options_down() -> None:
    docker.compose.create(build=True, force_recreate=True)
    docker.compose.create(build=True, force_recreate=True)
    docker.compose.create(no_build=True, no_recreate=True)
    docker.compose.down()


def test_docker_compose_up_detach_down_extra_options() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    docker.compose.down(remove_orphans=True, remove_images="all", timeout=3)


def test_docker_compose_up_build() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], build=True, detach=True)
    with docker.image.inspect("some_random_image"):
        docker.compose.down()


def test_docker_compose_up_stream() -> None:
    logs = _collect_logs(
        docker.compose.up(
            ["my_service", "busybox", "alpine"],
            build=True,
            stream_logs=True,
            detach=True,
            recreate=True,
        )
    )
    assert len(logs) >= 2
    full_logs_as_binary = b"".join(log for _, log in logs)
    assert b"components_alpine_1" in full_logs_as_binary
    assert b"components_my_service_1" in full_logs_as_binary
    assert b"components_busybox_1" in full_logs_as_binary
    docker.compose.down()


def test_docker_compose_up_stop_rm() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], build=True, detach=True)
    docker.compose.stop(timeout=timedelta(seconds=3))
    docker.compose.rm(volumes=True)


def test_docker_compose_stop_stream() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], detach=True)
    logs = _collect_logs(docker.compose.stop(stream_logs=True))
    assert len(logs) >= 2
    assert all(source in ("stdout", "stderr") for source, _ in logs)
    full_logs_as_binary = b"".join(log for _, log in logs)
    assert re.search(
        rb"Container\s+components_busybox_1\s+Stopping\b", full_logs_as_binary
    )
    assert re.search(
        rb"Container\s+components_busybox_1\s+Stopped\b", full_logs_as_binary
    )
    assert (
        _container_state(docker.container.inspect("components_busybox_1")).running
        is False
    )


def test_docker_compose_up_rm() -> None:
    docker.compose.up(["my_service", "busybox", "alpine"], build=True, detach=True)
    docker.compose.rm(stop=True, volumes=True)


def test_docker_compose_up_down_some_services() -> None:
    docker.compose.up(["my_service", "busybox"], detach=True)
    docker.compose.down(timeout=1)


def test_docker_compose_up_pull_never() -> None:
    with contextlib.suppress(DockerException):
        docker.image.remove("alpine")
    with pytest.raises(DockerException):
        docker.compose.up(["alpine"], pull="never")


def test_docker_compose_up_no_recreate() -> None:
    docker.compose.up(["busybox"], detach=True)
    containers = docker.compose.ps()
    container_id = containers[0].id
    docker.compose.up(["busybox"], scales={"busybox": 2}, detach=True, recreate=False)
    container_ids = {x.id for x in docker.compose.ps()}
    assert container_id in container_ids
    docker.compose.down(timeout=1)


def test_docker_compose_ps() -> None:
    docker.compose.up(["my_service", "busybox"], detach=True)
    containers = docker.compose.ps()
    names = {x.name for x in containers}
    assert names == {"components_my_service_1", "components_busybox_1"}
    containers = docker.compose.ps(["my_service"])
    names = {x.name for x in containers}
    assert names == {"components_my_service_1"}
    docker.compose.down()


def test_docker_compose_start() -> None:
    docker.compose.create(["busybox"])
    assert _container_state(docker.compose.ps(all=True)[0]).running is not True
    assert docker.compose.ps() == []
    docker.compose.start(["busybox"])
    assert _container_state(docker.compose.ps()[0]).running is True
    docker.compose.down(timeout=1)


def test_docker_compose_start_stream() -> None:
    docker.compose.create(["busybox"])
    assert _container_state(docker.compose.ps(all=True)[0]).running is not True
    assert docker.compose.ps() == []
    logs = _collect_logs(docker.compose.start(["busybox"], stream_logs=True))
    assert len(logs) >= 2
    assert all(source in ("stdout", "stderr") for source, _ in logs)
    full_logs_as_binary = b"".join(log for _, log in logs)
    assert re.search(
        rb"Container\s+components_busybox_1\s+Starting\b", full_logs_as_binary
    )
    assert re.search(
        rb"Container\s+components_busybox_1\s+Started\b", full_logs_as_binary
    )
    assert _container_state(docker.compose.ps()[0]).running is True
    docker.compose.down(timeout=1)


def test_docker_compose_restart() -> None:
    docker.compose.up(["my_service"], detach=True)
    time.sleep(2)

    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.started_at is not None
        assert (datetime.now(pytz.utc) - container.state.started_at) > timedelta(
            seconds=2
        )

    docker.compose.restart(timeout=1)

    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.started_at is not None
        assert (datetime.now(pytz.utc) - container.state.started_at) < timedelta(
            seconds=2
        )

    docker.compose.down()


def test_docker_compose_restart_empty_list_of_services() -> None:
    docker.compose.up(["my_service"], detach=True)
    time.sleep(2)

    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.started_at is not None
        assert (datetime.now(pytz.utc) - container.state.started_at) > timedelta(
            seconds=2
        )

    docker.compose.restart([], timeout=1)

    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.started_at is not None
        assert (datetime.now(pytz.utc) - container.state.started_at) > timedelta(
            seconds=2
        )

    docker.compose.down()


def test_docker_compose_kill() -> None:
    docker.compose.up(["my_service", "busybox"], detach=True)

    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.running is True

    docker.compose.kill("busybox", signal=9)

    assert (
        _container_state(docker.container.inspect("components_busybox_1")).running
        is not True
    )

    docker.compose.down()


def test_docker_compose_pull() -> None:
    with contextlib.suppress(NoSuchImage):
        docker.image.remove("busybox")
    with contextlib.suppress(NoSuchImage):
        docker.image.remove("alpine")
    docker.compose.pull("busybox")
    docker.compose.pull(["busybox", "alpine"])
    docker.image.inspect(["busybox", "alpine"])

    # pull with quiet should work too
    docker.compose.pull(["busybox", "alpine"], quiet=True)


def test_docker_compose_pull_stream() -> None:
    with contextlib.suppress(NoSuchImage):
        docker.image.remove("busybox")
    logs = _collect_logs(docker.compose.pull("busybox", stream_logs=True))
    assert len(logs) >= 3
    logs_as_big_binary = b""
    for log_type, log_value in logs:
        assert log_type in ("stdout", "stderr")
        logs_as_big_binary += log_value
    assert re.search(
        rb"(?m)^[ \t]*(?:Image[ \t]+)?busybox(?::latest)?[ \t]+Pulling\b",
        logs_as_big_binary,
    )
    assert re.search(
        rb"(?m)^[ \t]*(?:Image[ \t]+)?busybox(?::latest)?[ \t]+Pulled\b",
        logs_as_big_binary,
    )
    docker.image.inspect("busybox:latest")


def test_docker_compose_pull_ignore_pull_failures() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT
            / "tests/pywhales/components/dummy_compose_non_existent_image.yml"
        ]
    )
    with contextlib.suppress(NoSuchImage):
        docker.image.remove("ghost")
    docker.compose.pull(["ghost"], ignore_pull_failures=True)


def test_docker_compose_pull_include_deps() -> None:
    with contextlib.suppress(NoSuchImage):
        docker.image.remove("alpine")
    docker.compose.pull(["busybox-2-electric-boogaloo"], include_deps=True)
    docker.image.inspect(["alpine"])


def test_docker_compose_up_abort_on_container_exit() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/dummy_compose_ends_quickly.yml"
        ],
        compose_compatibility=True,
    )
    docker.compose.up("alpine", abort_on_container_exit=True)
    for container in docker.compose.ps():
        assert container.state is not None
        assert container.state.running is not True
    docker.compose.down()


def test_docker_compose_up_no_attach_services(
    capfd: pytest.CaptureFixture[str],
) -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/compose_logs.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(no_attach_services=["my_service"])

    container_names = [x.name for x in docker.compose.ps()]
    stdout, _ = capfd.readouterr()
    # Checking if we only attach to my_other_service
    expected_output = "Attaching to my_other_service_1, my_other_service_2\n"
    assert expected_output in stdout

    # Checking if the my_service is spun up even if it's not attached
    for name in container_names:
        assert name is not None
        if "my_service" in name:
            break
    else:
        raise AssertionError("my_service is not spun up as expected")
    docker.compose.down()


def test_passing_env_files(tmp_path: Path) -> None:
    compose_env_file = tmp_path / "dodo.env"
    compose_env_file.write_text("SOME_VARIABLE_TO_INSERT=hello\n")
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/dummy_compose_ends_quickly.yml"
        ],
        compose_env_file=compose_env_file,
        compose_compatibility=True,
    )
    output = docker.compose.config()

    assert output.services is not None
    environment = output.services["alpine"].environment
    assert isinstance(environment, dict)
    assert environment["SOME_VARIABLE"] == "hello"


def test_project_directory_env_files(tmp_path: Path) -> None:
    makedirs(tmp_path / "some/path")
    makedirs(tmp_path / "some/other/path")
    project_env_file_one = tmp_path / "some/path/one.env"
    project_env_file_two = tmp_path / "some/other/path/two.env"
    project_env_file_one.write_text("TEST=one\n")
    project_env_file_two.write_text("TEST=two\n")
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT
            / "tests/pywhales/components/dummy_compose_project_directory.yml"
        ],
        compose_project_directory=tmp_path,
        compose_compatibility=True,
    )
    output = docker.compose.config()

    assert output.services is not None
    environment_one = output.services["alpine_one"].environment
    environment_two = output.services["alpine_two"].environment
    assert isinstance(environment_one, dict)
    assert isinstance(environment_two, dict)
    assert environment_one["TEST"] == "one"
    assert environment_two["TEST"] == "two"


def test_entrypoint_loaded_in_config() -> None:
    services = docker.compose.config().services
    assert services is not None
    assert services["dodo"].entrypoint == ["/bin/sh"]


def test_config_complexe_compose() -> None:
    """Checking that the pydantic model does its job"""
    compose_file = PROJECT_ROOT / "tests/pywhales/components/complex-compose.yml"
    docker = DockerClient(compose_files=[compose_file], compose_compatibility=True)
    config = docker.compose.config()

    # Test my_service
    assert config.services is not None
    my_service = config.services["my_service"]
    assert my_service.build is not None
    assert my_service.build.context == (
        PROJECT_ROOT / "tests/pywhales/components/my_service_build"
    )
    assert my_service.image == "some_random_image"
    assert my_service.command == [
        "ping",
        "-c",
        "2",
        "www.google.com",
    ]
    assert my_service.hostname == "my_service_hostname"

    assert my_service.ports is not None
    assert my_service.ports[0].published == 5000
    assert my_service.ports[0].target == 5000

    assert my_service.volumes is not None
    assert my_service.volumes[0].source == "/tmp"
    assert my_service.volumes[0].target == "/tmp"
    assert my_service.volumes[1].source == "dodo"
    assert my_service.volumes[1].target == "/dodo"

    assert my_service.environment == {"DATADOG_HOST": "something"}
    # Test networks for my_service (Docker Compose normalizes list to dict)
    assert isinstance(my_service.networks, dict)
    assert "some-network" in my_service.networks
    assert "other-network" in my_service.networks
    assert my_service.networks["some-network"] is None
    assert my_service.networks["other-network"] is None

    assert my_service.deploy is not None
    assert my_service.deploy.placement is not None
    assert my_service.deploy.placement.constraints == ["node.labels.hello-world == yes"]
    assert my_service.deploy.resources is not None
    assert my_service.deploy.resources.limits is not None
    assert my_service.deploy.resources.limits.cpus == 2
    assert my_service.deploy.resources.limits.memory == 41943040

    assert my_service.deploy.resources.reservations is not None
    assert my_service.deploy.resources.reservations.cpus == 1
    assert my_service.deploy.resources.reservations.memory == 20971520

    assert my_service.deploy.replicas == 4

    # Test network_service with complex network configurations
    network_service = config.services["network_service"]
    assert network_service.image == "some_random_image"
    assert network_service.command == [
        "ping",
        "-c",
        "2",
        "www.google.com",
    ]
    assert network_service.hostname == "network_service_hostname"

    # Test network configurations for network_service
    assert network_service.networks is not None
    app_net_1_config = network_service.networks["app_net_1"]
    assert app_net_1_config is not None
    assert app_net_1_config.aliases == ["app-1-service", "app-1-server"]
    assert app_net_1_config.ipv4_address == "172.16.238.10"
    assert app_net_1_config.ipv6_address == "2001:3984:3989::10"
    assert app_net_1_config.priority == 1000

    app_net_2_config = network_service.networks["app_net_2"]
    assert app_net_2_config is not None
    assert app_net_2_config.aliases == ["app-2-service"]
    assert app_net_2_config.driver_opts == {
        "foo": "bar",
        "baz": "1",
    }  # Docker normalizes to strings
    assert app_net_2_config.gw_priority == 1

    app_net_3_config = network_service.networks["app_net_3"]
    assert app_net_3_config is not None
    assert app_net_3_config.link_local_ips == ["169.254.22.11", "169.254.22.13"]
    assert app_net_3_config.mac_address == "02:42:ac:11:00:02"
    assert app_net_3_config.priority == 100

    app_net_4_config = network_service.networks["app_net_4"]
    assert app_net_4_config is None

    app_net_5_config = network_service.networks["app_net_5"]
    assert app_net_5_config is None

    # Test network definitions
    assert config.networks is not None
    assert config.networks["some-network"].driver == "bridge"
    assert config.networks["some-network"].attachable is True
    assert config.networks["some-network"].labels["type"] == "frontend"

    assert config.networks["other-network"].internal is True
    ipam = config.networks["app_net_1"].ipam
    assert isinstance(ipam, dict)
    ipam_config = ipam["config"]
    assert isinstance(ipam_config, list)
    first_ipam_config = ipam_config[0]
    assert isinstance(first_ipam_config, dict)
    assert first_ipam_config["subnet"] == "172.16.238.0/24"
    assert config.networks["app_net_3"].enable_ipv6 is True

    assert config.volumes is not None
    assert config.volumes["dodo"].external is not True


def test_compose_down_networks() -> None:
    compose_file = PROJECT_ROOT / "tests/pywhales/components/cleanup-compose.yml"
    project_name = f"cleanup-{random_name()}"
    docker = DockerClient(
        compose_files=[compose_file],
        compose_project_name=project_name,
        compose_compatibility=True,
    )
    expected_network_names = {
        f"{project_name}_{network}"
        for network in (
            "some-network",
            "other-network",
            "app_net_1",
            "app_net_2",
            "app_net_3",
            "app_net_4",
            "app_net_5",
        )
    }
    try:
        docker.compose.up(["my_service", "network_service"], detach=True)
        network_names = {network.name for network in docker.network.list()}
        assert expected_network_names <= network_names

        docker.compose.down()

        network_names_after = {network.name for network in docker.network.list()}
        assert expected_network_names.isdisjoint(network_names_after)
    finally:
        docker.compose.down(volumes=True, timeout=0)


def test_compose_down_volumes() -> None:
    compose_file = PROJECT_ROOT / "tests/pywhales/components/cleanup-compose.yml"
    project_name = f"cleanup-{random_name()}"
    docker = DockerClient(
        compose_files=[compose_file],
        compose_project_name=project_name,
        compose_compatibility=True,
    )
    volume_name = f"{project_name}_dodo"
    try:
        docker.compose.up(["my_service"], detach=True)
        assert docker.volume.exists(volume_name)

        docker.compose.down()
        assert docker.volume.exists(volume_name)

        docker.compose.down(volumes=True)
        assert not docker.volume.exists(volume_name)
    finally:
        docker.compose.down(volumes=True, timeout=0)


@pytest.mark.parametrize("json_file", get_all_jsons("compose"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    config: ComposeConfig = ComposeConfig(**json.loads(json_as_txt))
    if json_file.name == "0.json":
        assert config.services is not None
        labels = config.services["traefik"].labels
        assert labels is not None
        assert labels["traefik.enable"] == "true"


def test_compose_run_simple() -> None:
    result = docker.compose.run("alpine", ["echo", "dodo"], remove=True, tty=False)
    assert result == "dodo"


def test_compose_run_detach() -> None:
    container = docker.compose.run("alpine", ["echo", "dodo"], detach=True, tty=False)
    assert isinstance(container, Container)

    time.sleep(0.1)
    assert container.state is not None
    assert container.state.running is not True
    assert container.logs() == "dodo\n"


def test_docker_compose_run_labels() -> None:
    container = docker.compose.run(
        "alpine",
        ["echo", "dodo"],
        labels={"traefik.enable": "false", "hello": "world"},
        detach=True,
        tty=False,
    )
    assert isinstance(container, Container)

    time.sleep(0.1)
    assert container.config is not None
    labels = container.config.labels
    print(labels)
    assert labels is not None
    assert labels.get("traefik.enable") == "false"
    assert labels.get("hello") == "world"
    docker.compose.down(timeout=1)


def test_compose_run_entrypoint() -> None:
    result = docker.compose.run(
        "dodo",
        ["cmd-is-argument-for-echo"],
        entrypoint="/bin/echo",
        remove=True,
        tty=False,
    )

    assert result == "cmd-is-argument-for-echo"


def test_compose_run_envs() -> None:
    result = docker.compose.run(
        "dodo",
        ["-c", "echo $VAR1 $VAR2"],
        envs={"VAR1": "hello", "VAR2": "world"},
        remove=True,
        tty=False,
    )

    assert result == "hello world"


def test_compose_run_volume() -> None:
    with tempfile.NamedTemporaryFile() as fst, tempfile.NamedTemporaryFile() as snd:
        fst.write(b"Hello ")
        snd.write(b" Year")
        fst.flush()
        snd.flush()

        result = docker.compose.run(
            "dodo",
            [
                "-c",
                "echo New >> /mounted/fst.txt"
                "&& echo $(cat /mounted/fst.txt)$(cat /mounted/snd.txt)",
            ],
            volumes=[
                (f"{fst.name}", "/mounted/fst.txt"),
                (f"{snd.name}", "/mounted/snd.txt", "ro"),
            ],
            remove=True,
            tty=False,
        )

        assert result == "Hello New Year"


def test_compose_run_volume_with_parameter() -> None:
    with (
        tempfile.NamedTemporaryFile() as read_only_file,
        pytest.raises(DockerException) as error,
    ):
        docker.compose.run(
            "dodo",
            ["-c", "echo test > /mounted/read_only_file.txt"],
            volumes=[(f"{read_only_file.name}", "/mounted/read_only_file.txt", "ro")],
            remove=True,
            tty=False,
        )
    assert error.value.stderr is not None
    assert (
        "can't create /mounted/read_only_file.txt: Read-only file system"
        in error.value.stderr
    )


def test_compose_run_service_ports_flag() -> None:
    """Test that --service-ports flag is included in the command."""

    with patch("pywhales.components.compose.cli_wrapper.run") as mock_run:
        mock_run.return_value = ""
        docker.compose.run(
            "my_service",
            command=["echo", "hello"],
            service_ports=True,
            detach=True,
            tty=False,
        )

        call_args = mock_run.call_args[0][0]
        assert "--service-ports" in call_args


@pytest.mark.parametrize("service_ports", [True, False])
def test_compose_run_service_ports_integration(service_ports: bool) -> None:
    """Test that service ports are correctly mapped when using --service-ports."""

    docker_client = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/service_ports.yml"],
        compose_compatibility=True,
    )
    container = docker_client.compose.run(
        "web",
        detach=True,
        service_ports=service_ports,
        remove=False,
        tty=False,
    )
    assert isinstance(container, Container)
    try:
        # Verify the container has the port mapping
        assert container.host_config is not None
        port_bindings = container.host_config.port_bindings
        assert port_bindings is not None
        if service_ports:
            tcp_bindings = port_bindings["80/tcp"]
            assert tcp_bindings is not None
            assert len(tcp_bindings) == 1
            assert tcp_bindings[0].host_port == "8074"

        else:
            assert len(port_bindings) == 0
    finally:
        container.stop()
        container.remove(force=True)


def test_compose_version() -> None:
    assert re.fullmatch(
        r"Docker Compose version v?\d+\.\d+\.\d+(?:[-+]\S+)?",
        docker.compose.version(),
    )


@pytest.mark.parametrize("source", ["stdout", "stderr"])
@pytest.mark.parametrize("follow", [False, True])
def test_eager_compose_logs_release_child_when_decoding_fails(
    source: str, follow: bool
) -> None:
    client = DockerClient(client_call=[sys.executable])
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                "import sys; print('ready', flush=True); sys.stdin.buffer.read(1); "
                f"sys.{source}.buffer.write(b'\\xff\\n'); sys.{source}.buffer.flush(); "
                "sys.stdin.buffer.read(1)",
            ],
            pipe_stdin=True,
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ) as start,
    ):
        assert raw._queue.get(timeout=3) == ("stdout", b"ready\n")
        assert raw.stdin is not None
        raw.stdin.write(b"x")
        raw.stdin.flush()

        with pytest.raises(UnicodeDecodeError):
            client.compose.logs("web", follow=follow)

        assert raw._process.returncode == (
            1 if sys.platform == "win32" else -signal.SIGKILL
        )
        assert all(not thread.is_alive() for thread in raw._threads)
        assert all(
            pipe.closed
            for pipe in (raw.stdin, raw._process.stdout, raw._process.stderr)
            if pipe is not None
        )
        assert ("--follow" in start.call_args.args[0]) is follow


@pytest.mark.parametrize("source", ["stdout", "stderr"])
def test_eager_compose_logs_preserve_text_when_child_exits(source: str) -> None:
    client = DockerClient(client_call=[sys.executable])
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                f"import sys; sys.{source}.buffer.write(b'first\\nlast')",
            ]
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        output = client.compose.logs("web")

        assert output == "first\nlast"
        assert raw._process.returncode == 0
        assert all(not thread.is_alive() for thread in raw._threads)


@pytest.mark.parametrize("source", ["stdout", "stderr"])
@pytest.mark.parametrize("follow", [False, True])
def test_streaming_compose_logs_retain_caller_ownership(
    source: str, follow: bool
) -> None:
    client = DockerClient(client_call=[sys.executable])
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                "import sys; print('ready', flush=True); sys.stdin.buffer.read(1); "
                f"sys.{source}.buffer.write(b'\\xff\\n'); sys.{source}.buffer.flush(); "
                "sys.stdin.buffer.read(1)",
            ],
            pipe_stdin=True,
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ) as start,
    ):
        assert raw._queue.get(timeout=3) == ("stdout", b"ready\n")

        output = client.compose.logs("web", follow=follow, stream=True)

        assert output is raw
        assert raw._process.poll() is None
        assert raw.stdin is not None
        raw.stdin.write(b"x")
        raw.stdin.flush()
        assert next(iter(output)) == (source, b"\xff\n")
        assert raw._process.poll() is None
        assert ("--follow" in start.call_args.args[0]) is follow
        raw.close()
        assert raw._process.returncode is not None
        assert all(not thread.is_alive() for thread in raw._threads)


def test_compose_logs_simple_use_case() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/compose_logs.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(detach=True)
    # Wait some seconds to let the container to complete the execution of ping
    # and print the statistics
    time.sleep(15)
    full_output = docker.compose.logs()
    assert "error with my_other_service" in full_output
    assert "--- www.google.com ping statistics ---" in full_output
    docker.compose.down(timeout=1)


def test_compose_logs_stream() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/compose_logs.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(detach=True)
    time.sleep(15)
    logs = docker.compose.logs(stream=True)
    logs = list(logs)
    assert any("error with my_other_service" in log[1].decode() for log in logs)
    assert any(
        "--- www.google.com ping statistics ---" in log[1].decode() for log in logs
    )

    docker.compose.down(timeout=1)


def test_compose_logs_follow() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/compose_logs.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(detach=True)

    signal.signal(signal.SIGALRM, mock_KeyboardInterrupt)
    signal.alarm(15)

    start = datetime.now()
    full_output = ""
    try:
        for _output_type, current_output in docker.compose.logs(
            follow=True, stream=True
        ):
            full_output += current_output.decode()
        # interrupt the alarm in case the command ends before the timeout
        signal.alarm(0)
    # catch and ignore the exception when the command is interruped by the timeout
    except KeyboardInterrupt:
        pass

    end = datetime.now()

    # 5 seconds because the command can end before the timeout (set to 15 seconds)...
    # but it is enough to verify that the follow flag was working
    # otherwise the logs command should completed in much less than 5 seconds
    assert (end - start).seconds >= 5

    assert "error with my_other_service" in full_output
    assert "--- www.google.com ping statistics ---" in full_output

    docker.compose.down(timeout=1)


def test_compose_events() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/compose-with-healthcheck.yml"
        ],
        compose_compatibility=True,
    )

    docker.compose.up(["unhealthy_service"], detach=True)

    events_iterator = docker.compose.events(["unhealthy_service"])

    events = []
    for event in events_iterator:
        events.append(event)
        if len(events) >= 3:  # Collect a few events for testing
            break

    assert len(events) >= 3
    for event in events:
        assert isinstance(event, ComposeEvent)
        assert event.service == "unhealthy_service"
        assert event.action is not None
        assert event.time is not None
        assert event.id is not None

    docker.compose.down(timeout=1)


@pytest.mark.parametrize("privileged", [True, False])
def test_compose_execute_no_tty(privileged: bool) -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox"], detach=True)
    time.sleep(2)
    full_output = docker.compose.execute(
        "busybox", ["echo", "dodo"], tty=False, privileged=privileged
    )
    assert full_output == "dodo"
    docker.compose.down(timeout=1)


# We can't test the TTY flag on execute because we can't have a true tty in pytest
# of course tty still works if pywhales is executed outside pytest.


def test_compose_execute_detach() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox"], detach=True)
    t1 = datetime.now()
    output = docker.compose.execute("busybox", ["sleep", "20"], detach=True, tty=False)
    execution_time = datetime.now() - t1
    assert execution_time.seconds < 20
    assert output is None
    docker.compose.down(timeout=1)


def test_compose_execute_envs() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox"], detach=True)
    output = docker.compose.execute(
        "busybox",
        ["sh", "-c", "echo $VAR1,$VAR2"],
        envs={"VAR1": "hello", "VAR2": "world"},
        tty=False,
    )
    assert output == "hello,world"
    docker.compose.down(timeout=1)


def test_compose_execute_user() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox"], detach=True)
    output = docker.compose.execute("busybox", ["whoami"], tty=False, user="sync")
    assert output == "sync"
    docker.compose.down(timeout=1)


def test_compose_execute_workdir() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_compatibility=True,
    )
    docker.compose.up(["busybox"], detach=True)
    assert (
        docker.compose.execute("busybox", ["pwd"], tty=False, workdir="/tmp") == "/tmp"
    )
    assert (
        docker.compose.execute("busybox", ["pwd"], tty=False, workdir="/proc")
        == "/proc"
    )
    docker.compose.down(timeout=1)


def test_compose_single_profile() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_profiles=["my_test_profile"],
        compose_compatibility=True,
    )
    docker.compose.up(detach=True)

    container_names = [x.name for x in docker.compose.ps()]
    assert "components_profile_test_service_1" in container_names

    docker.compose.down(timeout=1)


def test_compose_multiple_profiles() -> None:
    docker = DockerClient(
        compose_files=[PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"],
        compose_profiles=["my_test_profile", "my_test_profile2"],
        compose_compatibility=True,
    )
    docker.compose.up(detach=True)

    container_names = [x.name for x in docker.compose.ps()]
    assert "components_profile_test_service_1" in container_names
    assert "components_second_profile_test_service_1" in container_names

    docker.compose.down(timeout=1)


def test_compose_anon_volumes_recreate_not_enabled() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/compose_anon_volumes.yml"
        ],
    )
    docker.compose.up(detach=True)

    mounts_before_recreate = docker.compose.ps()[0].mounts
    assert mounts_before_recreate is not None
    volume_name_before_recreate = mounts_before_recreate[0].name

    docker.compose.up(detach=True, force_recreate=True)
    mounts_after_recreate = docker.compose.ps()[0].mounts
    assert mounts_after_recreate is not None
    volumes_name_after_recreate = mounts_after_recreate[0].name

    assert volume_name_before_recreate == volumes_name_after_recreate

    docker.compose.down(timeout=1)


def test_compose_anon_volumes_recreate_enabled() -> None:
    docker = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/compose_anon_volumes.yml"
        ],
    )
    docker.compose.up(detach=True)

    mounts_before_recreate = docker.compose.ps()[0].mounts
    assert mounts_before_recreate is not None
    volume_name_before_recreate = mounts_before_recreate[0].name

    docker.compose.up(detach=True, force_recreate=True, renew_anon_volumes=True)
    mounts_after_recreate = docker.compose.ps()[0].mounts
    assert mounts_after_recreate is not None
    volumes_name_after_recreate = mounts_after_recreate[0].name

    assert volume_name_before_recreate != volumes_name_after_recreate

    docker.compose.down(timeout=1)


def test_compose_port() -> None:
    d = DockerClient(
        compose_files=[
            PROJECT_ROOT
            / "tests/pywhales/components/dummy_compose_non_existent_image.yml"
        ]
    )
    service = "busybox"
    d.compose.up(services=[service], detach=True)

    expected_tcp_host, expected_tcp_port, expected_udp_host, expected_udp_port = (
        None,
        None,
        None,
        None,
    )
    matching_container: Container | None = None
    for candidate in d.compose.ps():
        assert candidate.name is not None
        if service in candidate.name:
            matching_container = candidate
            break
    assert matching_container is not None
    container = matching_container
    assert container.network_settings is not None
    published_ports = container.network_settings.ports
    assert published_ports is not None
    tcp_bindings = published_ports["3000/tcp"]
    assert isinstance(tcp_bindings, list)
    tcp_cfg = tcp_bindings[0]
    assert isinstance(tcp_cfg, dict)
    tcp_host_ip, tcp_host_port = tcp_cfg["HostIp"], tcp_cfg["HostPort"]
    assert isinstance(tcp_host_ip, str)
    assert isinstance(tcp_host_port, str)
    expected_tcp_host, expected_tcp_port = tcp_host_ip, int(tcp_host_port)
    udp_bindings = published_ports["4000/udp"]
    assert isinstance(udp_bindings, list)
    udp_cfg = udp_bindings[0]
    assert isinstance(udp_cfg, dict)
    udp_host_ip, udp_host_port = udp_cfg["HostIp"], udp_cfg["HostPort"]
    assert isinstance(udp_host_ip, str)
    assert isinstance(udp_host_port, str)
    expected_udp_host, expected_udp_port = udp_host_ip, int(udp_host_port)

    tcp_host, tcp_port = d.compose.port(service, "3000", protocol="tcp")
    assert expected_tcp_host == tcp_host
    assert expected_tcp_port == tcp_port

    udp_host, udp_port = d.compose.port(service, "4000", protocol="udp")
    assert expected_udp_host == udp_host
    assert expected_udp_port == udp_port

    with pytest.raises(DockerException) as err:
        d.compose.port(service, "4000", protocol="tcp")
    assert err.value.stderr is not None
    assert "no port 4000/tcp for container components-busybox-1" in err.value.stderr

    with pytest.raises(DockerException) as err:
        d.compose.port(service, "1111")
    assert err.value.stderr is not None
    assert "no port 1111/tcp for container components-busybox-1" in err.value.stderr

    with pytest.raises(ValueError) as err:
        d.compose.port("", "123")
    assert "Service cannot be empty" in str(err)

    with pytest.raises(ValueError) as err:
        d.compose.port(service, "")
    assert "Private port cannot be empty" in str(err)

    d.compose.down(timeout=1)


def test_compose_ls_project_multiple_statuses() -> None:
    d = DockerClient(
        compose_files=[
            PROJECT_ROOT / "tests/pywhales/components/dummy_compose_ends_quickly.yml",
            PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml",
        ],
        compose_compatibility=True,
        compose_project_name="test_compose_ls",
    )
    d.compose.up(["alpine", "dodo"], detach=True)
    time.sleep(2)

    projects = d.compose.ls(all=True)
    project = next(
        proj
        for proj in projects
        if proj.name == d.compose.client_config.compose_project_name
    )

    assert project.running == 1
    assert project.exited == 1
    assert project.paused == 0
    if project.config_files is not None and project.config_files:
        assert sorted(project.config_files) == sorted(d.client_config.compose_files)

    d.compose.down(timeout=1)


def check_number_of_running_containers(
    docker_client: DockerClient, expected: int, countable_container_ids: Iterable[str]
) -> None:
    """
    Check that we have the expected number of running containers out of the specified ones,
    Running containers that do not have their id in the list won't be counted.
    :param docker_client: docker client to use
    :param expected: the number of expected running containers
    :param countable_container_ids: a list of container ids
    :return: None
    """
    containers = docker_client.ps()
    container_ids = {container.id for container in containers}
    assert len(set(countable_container_ids).intersection(container_ids)) == expected


def test_docker_compose_up_remove_orphans() -> None:
    compose_file_descriptor, compose_file_name = tempfile.mkstemp(
        prefix="test_docker_compose_up_remove_orphans_", suffix=".yml"
    )
    os.close(compose_file_descriptor)
    compose_file = Path(compose_file_name)
    docker = DockerClient(
        compose_files=[compose_file],
        compose_compatibility=True,
    )
    base_cfg = """version: "3.7"
services:
  busybox1:
    image: busybox:latest
    command: sleep infinity
"""
    service_to_remove = """  busybox2:
    image: busybox:latest
    command: sleep infinity
"""

    # writing the docker compose file with 2 services configured
    compose_file.write_text(base_cfg + service_to_remove)

    docker.compose.up(detach=True)
    compose_containers = docker.compose.ps()
    assert len(compose_containers) == 2
    compose_container_ids = {container.id for container in compose_containers}

    # updating the docker compose file to have only 1 service configured
    compose_file.write_text(base_cfg)

    docker.compose.up(detach=True)
    # both containers running
    check_number_of_running_containers(docker, 2, compose_container_ids)

    # calling with remove_orphans flag
    docker.compose.up(detach=True, remove_orphans=True)
    # orphan container (of the removed service) was stopped
    check_number_of_running_containers(docker, 1, compose_container_ids)

    docker.compose.down(timeout=1)
    check_number_of_running_containers(docker, 0, compose_container_ids)
    remove(compose_file)


def test_docker_compose_run_build() -> None:
    docker.compose.run("my_service", build=True, detach=True, tty=False)
    docker.compose.stop()
    docker.compose.rm()
    image_tags = docker.image.list("some_random_image")[0].repo_tags
    assert image_tags is not None
    services = docker.compose.config(return_json=True)["services"]
    assert isinstance(services, dict)
    my_service = services["my_service"]
    assert isinstance(my_service, dict)
    assert my_service["image"] == image_tags[0].split(":latest")[0]
    docker.image.remove("some_random_image", force=True)


def test_build_args() -> None:
    compose_file = PROJECT_ROOT / "tests/pywhales/components/test-build-args.yml"
    docker = DockerClient(compose_files=[compose_file])
    config = docker.compose.config()

    assert config.services is not None
    my_service = config.services["my_service"]
    assert my_service.build is not None
    assert (
        my_service.build.context
        == (compose_file.parent / "my_service_build").absolute()
    )
    assert my_service.build.dockerfile == Path("docker/somefile.dockerfile")
    assert my_service.build.args == {
        "python_version": "3.78",
        "python_version_1": "3.78",
    }
    assert my_service.build.labels == {
        "com.example.description": "Accounting webapp",
        "com.example.department": "Finance",
    }
    assert my_service.image == "some_random_image"
    assert my_service.command == [
        "ping",
        "-c",
        "7",
        "www.google.com",
    ]

    assert my_service.ports is not None
    assert my_service.ports[0].published == 5000
    assert my_service.ports[0].target == 5000

    assert my_service.volumes is not None
    assert my_service.volumes[0].source == "/tmp"
    assert my_service.volumes[0].target == "/tmp"
    assert my_service.volumes[1].source == "dodo"
    assert my_service.volumes[1].target == "/dodo"

    assert my_service.environment == {"DATADOG_HOST": "something"}


@patch("pywhales.components.compose.cli_wrapper.run")
def test_compose_publish_cli_basic(run_mock: Mock) -> None:
    test_application = "repo/app:tag"
    docker.compose.publish(test_application)
    run_mock.assert_called_once_with(
        [*docker.client_config.docker_compose_cmd, "publish", test_application],
        capture_stderr=False,
        capture_stdout=False,
    )


@patch("pywhales.components.compose.cli_wrapper.run")
def test_compose_publish_cli_all_flags(run_mock: Mock) -> None:
    docker.compose.publish(
        repository="repo/image:tag",
        oci_version="1.1.0",
        resolve_image_digests=True,
        with_env=True,
        allow_publishing_sensitive_data=True,
    )

    run_mock.assert_called_once_with(
        [
            *docker.client_config.docker_compose_cmd,
            "publish",
            "--oci-version",
            "1.1.0",
            "--resolve-image-digests",
            "--with-env",
            "--yes",
            "repo/image:tag",
        ],
        capture_stderr=False,
        capture_stdout=False,
    )


def test_docker_compose_publish(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "services:\n"
        "  busybox:\n    image: busybox:latest\n    command: sleep infinity\n"
        "  alpine:\n    image: alpine:latest\n    command: sleep infinity\n"
        "    environment:\n"
        "      DD_API_KEY: __your_datadog_api_key_here__\n"
        "      POSTGRES_HOST_AUTH_METHOD: trust\n"
        "  dodo:\n    image: busybox:latest\n    entrypoint: /bin/sh\n"
    )
    project_name = f"publish-{random_name()}"
    docker = DockerClient()
    docker_executable = str(docker.client_config.get_client_call_with_path()[0])
    registry_capabilities: list[bool] = []
    # Compose 2.35 lacks these flags; newer releases need them for HTTP registries.
    for arguments in (
        ["compose", "publish", "--insecure-registry", "--help"],
        ["compose", "--insecure-registry", "127.0.0.1:12345", "--help"],
    ):
        result = subprocess.run(
            [docker_executable, *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if "unknown flag: --insecure-registry" not in result.stderr.splitlines():
            result.check_returncode()
        registry_capabilities.append(result.returncode == 0)
    publish_registry_supported, oci_registry_supported = registry_capabilities

    with docker.run(
        "registry:3",
        remove=True,
        privileged=False,
        publish=[("127.0.0.1::5000",)],
        detach=True,
        name=f"test-registry-{random_name()}",
    ) as registry:
        assert registry.network_settings is not None
        published_ports = registry.network_settings.ports
        assert published_ports is not None
        bindings = published_ports["5000/tcp"]
        assert isinstance(bindings, list)
        binding = bindings[0]
        assert isinstance(binding, dict)
        assert binding["HostIp"] == "127.0.0.1"
        registry_address = f"{binding['HostIp']}:{binding['HostPort']}"
        repository = f"{registry_address}/repo/app:tag"
        registry_cli = tmp_path / "registry_cli.py"
        registry_cli.write_text(
            "import os\n"
            "import sys\n"
            "\n"
            "args = sys.argv[1:]\n"
            f"if 'publish' in args and {publish_registry_supported!r}:\n"
            "    args.insert(args.index('publish') + 1, '--insecure-registry')\n"
            f"elif 'publish' not in args and {oci_registry_supported!r}:\n"
            f"    args[1:1] = ['--insecure-registry', {registry_address!r}]\n"
            f"os.execvp({docker_executable!r}, [{docker_executable!r}, *args])\n"
        )
        client_call = [sys.executable, str(registry_cli)]
        compose_client = DockerClient(
            compose_files=[compose_file],
            compose_project_name=project_name,
            compose_compatibility=True,
            client_call=client_call,
        )

        app_client = DockerClient(
            compose_files=[f"oci://{repository}"],
            compose_project_name=project_name,
            client_call=client_call,
        )
        try:
            compose_client.compose.publish(
                repository,
                with_env=True,
                allow_publishing_sensitive_data=True,
            )
            app_client.compose.pull()
            app_client.compose.up(detach=True)
            app_client.compose.rm(stop=True)
        finally:
            compose_client.compose.down(volumes=True, timeout=0)


COMPOSE_WD_FIXTURE_DIR = (
    PROJECT_ROOT / "tests/pywhales/components/build_with_dependencies_test"
)
WD_DB_IMAGE = "withdeps_db"
WD_APP_IMAGE = "withdeps_app"


@pytestmark
def test_compose_build_with_dependencies(tmp_path: Path) -> None:
    """
    This test demonstrates that the pywhales `compose.build` wrapper
    correctly passes through `--with-dependencies` to `docker compose`.

    It uses a tiny compose project with:
      - `db` service (image: withdeps_db)
      - `app` service (image: withdeps_app, depends_on: db)

    We check that:
      1. Building only `app` does NOT build `db`.
      2. Building `app` WITH dependencies builds BOTH `app` and `db`.
    """

    # Copy the small compose project into a temp directory so the project name
    # is stable & isolated per test run.
    project_dir = tmp_path / "compose-project"
    project_dir.mkdir()

    shutil.copytree(COMPOSE_WD_FIXTURE_DIR, project_dir, dirs_exist_ok=True)
    compose_file = project_dir / "compose.yaml"

    # Create a dedicated client for this compose project
    client = DockerClient(
        compose_files=[str(compose_file)],
        compose_project_name="withdeps-test",
        compose_project_directory=str(project_dir),
    )

    # Make sure we start from a clean slate re: images
    for tag in (WD_DB_IMAGE, WD_APP_IMAGE):
        if docker.image.exists(tag):
            docker.image.remove(tag, force=True, prune=True)
        assert not docker.image.exists(tag), f"image {tag} should have been removed"

    # Build only the app service (no dependencies)
    client.compose.build(services=["app"])

    assert docker.image.exists(WD_APP_IMAGE), "app image should have been built"
    assert not docker.image.exists(WD_DB_IMAGE), (
        "db image should NOT have been built when building only 'app' "
        "without --with-dependencies"
    )

    # Clean up app image before the second build to make assertions clearer
    docker.image.remove(WD_APP_IMAGE, force=True, prune=True)
    assert not docker.image.exists(WD_APP_IMAGE)

    # Build app *with* dependencies
    client.compose.build(services=["app"], with_dependencies=True)

    assert docker.image.exists(WD_APP_IMAGE), (
        "app image should have been built with dependencies"
    )
    assert docker.image.exists(WD_DB_IMAGE), (
        "db image should have been built as a dependency"
    )

    # just in case leave it all clean
    docker.image.remove([WD_APP_IMAGE, WD_DB_IMAGE], force=True, prune=True)


@pytest.mark.parametrize("consume", [False, True])
def test_owned_compose_events_cancel_idle_child(consume: bool) -> None:
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                'print(\'{"type":"container","action":"start","service":"web"}\', flush=True); input()',
            ],
            pipe_stdin=True,
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ) as start,
    ):
        stream = docker.compose.events(["web", "worker"])
        with stream:
            if consume:
                event = next(stream)
                assert isinstance(event, ComposeEvent)
                assert event.action == "start"
                assert event.service == "web"
            stream.close()
            stream.close()

        assert list(stream) == []
        assert raw._process.poll() is not None
        assert all(not thread.is_alive() for thread in raw._threads)
        assert start.call_args.args[0] == [
            *docker.compose.docker_compose_cmd,
            "events",
            "--json",
            "web",
            "worker",
        ]


@pytest.mark.parametrize("payload", ["not-json", '{"time":"invalid"}'])
def test_owned_compose_events_release_child_on_parse_error(payload: str) -> None:
    with (
        stream_stdout_and_stderr(
            [sys.executable, "-c", f"print({payload!r}, flush=True); input()"],
            pipe_stdin=True,
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        stream = docker.compose.events()

        with pytest.raises((json.JSONDecodeError, ValidationError)):
            next(stream)

        assert raw._process.poll() is not None
        assert all(not thread.is_alive() for thread in raw._threads)
        assert list(stream) == []


def test_owned_compose_events_preserve_body_error() -> None:
    with (
        stream_stdout_and_stderr(
            [sys.executable, "-c", "input()"], pipe_stdin=True
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        stream = docker.compose.events()
        error = RuntimeError("event consumer failed")

        with pytest.raises(RuntimeError) as raised, stream:
            raise error

        assert raised.value is error
        assert raw._process.poll() is not None


@pytest.mark.parametrize("exit_code", [0, 8])
def test_owned_compose_events_preserve_eof_and_failure(exit_code: int) -> None:
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                'import sys; print(\'{"action":"start","service":"web"}\'); '
                "sys.stderr.write('No such image: missing\\n'); "
                f"sys.exit({exit_code})",
            ]
        ) as raw,
        patch(
            "pywhales.components.compose.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        stream = docker.compose.events()
        output: list[ComposeEvent] = []

        if exit_code:
            with pytest.raises(NoSuchImage) as raised:
                output.extend(stream)
            assert raised.value.return_code == exit_code
            assert raised.value.stderr == "No such image: missing\n"
        else:
            output.extend(stream)

        assert [event.action for event in output] == ["start"]
        assert raw._process.poll() == exit_code
        assert all(not thread.is_alive() for thread in raw._threads)
