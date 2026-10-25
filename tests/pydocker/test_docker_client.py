import re
import sys
import time
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from pydocker import DockerClient, Image
from pydocker.components.top import ContainerTopResult
from pydocker.exceptions import DockerException


@patch(
    "pydocker.components.container.cli_wrapper.run", return_value="PID CMD\n1 sleep 60"
)
def test_top_dispatches_process_listing_when_root_alias_is_used(run_mock: Mock) -> None:
    client = DockerClient(client_call=[sys.executable, "engine"], context="remote")

    result = client.top("workload")

    assert isinstance(result, ContainerTopResult)
    assert result.rows == (("1", "sleep 60"),)
    run_mock.assert_called_once_with(
        [
            Path(sys.executable),
            "engine",
            "--context",
            "remote",
            "container",
            "top",
            "workload",
        ]
    )


@patch("pydocker.components.container.cli_wrapper.run", return_value="")
def test_stop_dispatch_is_unchanged_when_root_and_component_are_used(
    run_mock: Mock,
) -> None:
    client = DockerClient(client_call=[sys.executable, "engine"])

    client.stop("first", time=0)
    client.container.stop("second", time=1)

    assert run_mock.call_args_list == [
        call(
            [Path(sys.executable), "engine", "container", "stop", "--time", 0, "first"]
        ),
        call(
            [Path(sys.executable), "engine", "container", "stop", "--time", 1, "second"]
        ),
    ]


@pytest.mark.parametrize("component_stop", [False, True])
def test_top_leaves_running_then_stop_stops_when_inspecting_live_container(
    docker_client: DockerClient, component_stop: bool
) -> None:
    container = docker_client.run(
        "python:3.14.8-slim-trixie", ["sleep", "300"], detach=True
    )
    try:
        result = docker_client.top(container)

        assert result.rows
        assert any("sleep 300" in row[-1] for row in result.rows)
        container.reload()
        assert container.state is not None
        assert container.state.running is True
        stop = docker_client.container.stop if component_stop else docker_client.stop
        stop(container, time=0)
        container.reload()
        assert container.state is not None
        assert container.state.running is False
    finally:
        docker_client.remove(container, force=True)


def test_version(ctr_client: DockerClient) -> None:
    version_info = ctr_client.version()
    assert version_info.client is not None
    assert version_info.client.version is not None
    assert re.match(r"\d+\.\d+", version_info.client.version)
    assert version_info.server is not None
    assert version_info.server.version is not None
    assert re.match(r"\d+\.\d+", version_info.server.version)


def test_login_logout(
    docker_client: DockerClient, docker_registry_without_login: str
) -> None:
    busybox_image = docker_client.pull("busybox")
    assert isinstance(busybox_image, Image)
    busybox_image.tag(f"{docker_registry_without_login}/my_busybox")
    with pytest.raises(DockerException):
        docker_client.push(f"{docker_registry_without_login}/my_busybox")
    docker_client.login(
        docker_registry_without_login, username="my_user", password="my_password"
    )
    assert (
        docker_registry_without_login
        in (Path.home() / ".docker" / "config.json").read_text()
    )
    docker_client.push(f"{docker_registry_without_login}/my_busybox")
    docker_client.push(
        [f"{docker_registry_without_login}/my_busybox" for _ in range(2)]
    )
    docker_client.pull(f"{docker_registry_without_login}/my_busybox")
    docker_client.logout(docker_registry_without_login)
    assert (
        docker_registry_without_login
        not in (Path.home() / ".docker" / "config.json").read_text()
    )


@pytest.mark.skipif(True, reason="It doesn't work in the ci")
def test_docker_client_options(docker_client: DockerClient) -> None:
    if docker_client.container.exists("test_dind_container"):
        docker_client.container.remove("test_dind_container", force=True, volumes=True)

    with docker_client.run(
        "docker:20.10.16-dind",
        ["dockerd", "--host=tcp://0.0.0.0:2375", "--tls=false"],
        remove=True,
        privileged=True,
        publish=[(2380, 2375)],
        detach=True,
        name="test_dind_container",
    ):
        time.sleep(10)
        dind_client = DockerClient(host="tcp://localhost:2380")
        assert dind_client.image.list() == []
        assert "Hello from Docker!" in dind_client.run("hello-world")

        dind_client = DockerClient(
            client_call=["docker", "--host=tcp://localhost:2380"]
        )
        assert "Hello from Docker!" in dind_client.run("hello-world")
