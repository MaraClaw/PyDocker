import json
from pathlib import Path

import pytest

from pydocker import DockerClient
from pydocker.components.node.models import NodeInspectResult
from pydocker.test_utils import get_all_jsons


@pytest.mark.parametrize("json_file", get_all_jsons("nodes"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    a: NodeInspectResult = NodeInspectResult(**json.loads(json_as_txt))
    if json_file.name == "1.json":
        assert a.description is not None
        assert a.description.resources is not None
        generic_resources = a.description.resources.generic_resources
        assert generic_resources is not None
        named_resource_spec = generic_resources[0].named_resource_spec
        assert named_resource_spec is not None
        assert named_resource_spec.kind == "gpu"
        assert named_resource_spec.value == "gpu-0"
        assert a.description.resources.nano_cpus == 4000000001


@pytest.mark.usefixtures("swarm_mode")
def test_list_nodes(docker_client: DockerClient) -> None:
    nodes = docker_client.node.list()
    assert nodes[0].id[:12] in repr(nodes)
    assert len(nodes) == 1


@pytest.mark.usefixtures("swarm_mode")
def test_add_label(docker_client: DockerClient) -> None:
    nodes = docker_client.node.list()
    nodes[0].update(labels_add={"foo": "bar"})
    assert nodes[0].spec is not None
    labels = nodes[0].spec.labels
    assert labels is not None
    assert labels["foo"] == "bar"


@pytest.mark.usefixtures("swarm_mode")
def test_remove_label(docker_client: DockerClient) -> None:
    nodes = docker_client.node.list()
    nodes[0].update(labels_add={"foo": "bar"})
    nodes[0].update(rm_labels=["foo"])
    assert nodes[0].spec is not None
    labels = nodes[0].spec.labels
    assert labels is not None
    assert "foo" not in labels


@pytest.mark.usefixtures("swarm_mode")
def test_tasks(docker_client: DockerClient) -> None:
    service = docker_client.service.create("busybox", ["sleep", "infinity"])

    current_node = docker_client.node.list()[0]
    tasks = current_node.ps()
    assert len(tasks) > 0
    assert tasks[0].desired_state == "running"
    docker_client.service.remove(service)


@pytest.mark.usefixtures("swarm_mode")
def test_list_tasks_node(docker_client: DockerClient) -> None:
    with docker_client.service.create("busybox", ["sleep", "infinity"]) as my_service:
        assert docker_client.node.ps([]) == []
        assert set(docker_client.node.ps()) == set(docker_client.service.ps(my_service))
