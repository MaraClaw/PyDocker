import json
import tempfile
import time
from pathlib import Path

import pytest

from pydocker import DockerClient, Service
from pydocker.components.service.models import ServiceInspectResult
from pydocker.exceptions import NoSuchService, NotASwarmManager
from pydocker.test_utils import get_all_jsons, random_name


@pytest.mark.parametrize("json_file", get_all_jsons("services"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    ServiceInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


@pytest.mark.usefixtures("swarm_mode")
def test_tasks(docker_client: DockerClient) -> None:
    service = docker_client.service.create("busybox", ["sleep", "infinity"])

    tasks = service.ps()
    assert len(tasks) > 0
    assert tasks[0].desired_state == "running"
    docker_client.service.remove(service)


@pytest.mark.usefixtures("swarm_mode")
def test_get_logs(docker_client: DockerClient) -> None:
    with docker_client.service.create(
        "busybox", ["sh", "-c", "echo dodo && sleep infinity"]
    ) as my_service:
        assert my_service.ps()[0].desired_state == "running"
        assert docker_client.service.logs(my_service).split("|")[-1].strip() == "dodo"


@pytest.mark.usefixtures("swarm_mode")
def test_get_list_of_services(docker_client: DockerClient) -> None:
    with docker_client.service.create(
        "busybox", ["sh", "-c", "echo dodo && sleep infinity"]
    ) as my_service:
        list_of_services = docker_client.service.list()
        assert [my_service] == list_of_services


@pytest.mark.usefixtures("swarm_mode")
def test_list_filters(docker_client: DockerClient) -> None:
    random_label_value = random_name()
    with (
        docker_client.service.create(
            "busybox",
            ["sleep", "infinity"],
            labels={"dodo": random_label_value},
        ) as my_service,
        docker_client.service.create(
            "busybox",
            ["sleep", "infinity"],
            labels={"dodo": "something"},
        ),
    ):
        expected_services = docker_client.service.list(
            filters=[("label", f"dodo={random_label_value}")]
        )
        assert expected_services == [my_service]
        no_expected_services = docker_client.service.list(
            filters=[("label", f"dodo={random_name()}")]
        )
        assert len(no_expected_services) == 0


@pytest.mark.usefixtures("swarm_mode")
def test_get_list_of_services_no_services(docker_client: DockerClient) -> None:
    assert docker_client.service.list() == []


@pytest.mark.usefixtures("swarm_mode")
def test_remove_empty_services_list(docker_client: DockerClient) -> None:
    with docker_client.service.create(
        "busybox", ["sh", "-c", "echo dodo && sleep infinity"]
    ) as my_service:
        assert my_service in docker_client.service.list()
        set_services = set(docker_client.service.list())
        docker_client.service.remove([])
        assert set(docker_client.service.list()) == set_services


@pytest.mark.usefixtures("swarm_mode")
def test_service_scale(docker_client: DockerClient) -> None:
    service = docker_client.service.create("busybox", ["sleep", "infinity"])
    service.scale(3)
    time.sleep(0.4)
    assert service.spec is not None
    assert service.spec.mode is not None
    assert service.spec.mode["Replicated"] == {"Replicas": 3}
    service.update(replicas=1)
    time.sleep(0.4)
    assert service.spec is not None
    assert service.spec.mode is not None
    assert service.spec.mode["Replicated"] == {"Replicas": 1}


@pytest.mark.usefixtures("swarm_mode")
def test_context_manager(docker_client: DockerClient) -> None:
    my_service: Service | None = None
    with (
        pytest.raises(RuntimeError),
        docker_client.service.create(
            "busybox", ["sleep", "infinity"]
        ) as created_service,
    ):
        my_service = created_service
        assert created_service.spec is not None
        assert created_service.spec.task_template is not None
        assert created_service.spec.task_template.container_spec is not None
        assert created_service.spec.task_template.container_spec.image is not None
        assert created_service.spec.task_template.container_spec.image.startswith(
            "busybox"
        )
        assert created_service.exists()
        raise RuntimeError

    assert my_service is not None
    assert not my_service.exists()


@pytest.mark.usefixtures("swarm_mode")
def test_service_restart(docker_client: DockerClient) -> None:
    my_service = docker_client.service.create(
        "busybox",
        ["echo", "Hello"],
        detach=True,
        restart_condition="none",
        restart_max_attempts=0,
    )
    time.sleep(2)
    assert my_service.ps()[0].desired_state == "shutdown"
    my_service.remove()


@pytest.mark.usefixtures("swarm_mode")
def test_service_secrets(docker_client: DockerClient) -> None:
    secret_user = None
    secret_pass = None
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"supersecretuser")
        f.seek(0)
        secret_user = docker_client.secret.create("dbuser", f.name)
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"supersecretpass")
        f.seek(0)
        secret_pass = docker_client.secret.create("dbpass", f.name)

    with docker_client.service.create(
        "ubuntu",
        ["bash", "-c", "cat /run/secrets/{dbuser,dbpass} && sleep infinity"],
        secrets=[
            {
                "source": "dbuser",
            },
            {
                "source": "dbpass",
                "uid": "1000",
                "gid": "1000",
                "mode": "0400",
            },
        ],
    ) as my_service:
        assert my_service.ps()[0].desired_state == "running"
    secret_user.remove()
    secret_pass.remove()


@pytest.mark.usefixtures("swarm_mode")
def test_service_mounts(docker_client: DockerClient) -> None:
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"config")
        f.seek(0)
        with docker_client.service.create(
            "ubuntu",
            ["bash", "-c", f"cat {f.name} && sleep infinity"],
            mounts=[
                {
                    "type": "bind",
                    "source": f.name,
                    "destination": f.name,
                },
            ],
        ) as my_service:
            assert my_service.ps()[0].desired_state == "running"


@pytest.mark.parametrize("method", ["inspect", "remove", "ps"])
@pytest.mark.usefixtures("swarm_mode")
def test_some_functions_no_such_service(
    docker_client: DockerClient, method: str
) -> None:
    with pytest.raises(NoSuchService):
        getattr(docker_client.service, method)("DOODODGOIHURHURI")


@pytest.mark.usefixtures("swarm_mode")
def test_scale_no_such_service(docker_client: DockerClient) -> None:
    with pytest.raises(NoSuchService):
        docker_client.service.scale({"DOODODGOIHURHURI": 14})


def test_create_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.create("busybox", ["sleep", "infinity"])

    assert "not a swarm manager" in str(e.value).lower()


def test_inspect_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.inspect("dodo")

    assert "not a swarm manager" in str(e.value).lower()


def test_exists_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.exists("dodo")

    assert "not a swarm manager" in str(e.value).lower()


def test_list_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.list()

    assert "not a swarm manager" in str(e.value).lower()


def test_ps_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.ps("dodo")

    assert "not a swarm manager" in str(e.value).lower()


def test_remove_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.remove("dodo")

    assert "not a swarm manager" in str(e.value).lower()


def test_scale_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.scale({"dodo": 8})

    assert "not a swarm manager" in str(e.value).lower()


def test_update_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.service.update("dodo", image="busybox")

    assert "not a swarm manager" in str(e.value).lower()
