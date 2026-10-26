import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import DockerClient, Node
from pywhales.client_config import ParsingError
from pywhales.components.node.models import NodeInspectResult
from pywhales.exceptions import DockerException
from pywhales.test_utils import get_all_jsons


def test_batch_inspect_matches_node_hostnames_and_hydrates_cache() -> None:
    client = DockerClient(host="inventory-host")
    client.client_config._client_call_with_path = ["docker-test"]
    alpha = {
        "ID": "a" * 27,
        "Spec": {"Name": "not-alpha"},
        "Description": {"Hostname": "alpha"},
    }
    bravo = {
        "ID": "b" * 27,
        "Spec": {"Name": "not-bravo"},
        "Description": {"Hostname": "bravo"},
    }
    references = ["bravo", "a" * 27, "b" * 8, "alpha", "bravo"]
    response = json.dumps([alpha, bravo, alpha, bravo, bravo]).encode()
    with (
        patch(
            "subprocess.run", return_value=CompletedProcess([], 0, response, b"")
        ) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        resources = client.node.inspect(references)
        assert [resource.id for resource in resources] == [
            "b" * 27,
            "a" * 27,
            "b" * 27,
            "a" * 27,
            "b" * 27,
        ]
        assert [
            resource.description.hostname
            for resource in resources
            if resource.description is not None
        ] == ["bravo", "alpha", "bravo", "alpha", "bravo"]
        assert all(
            isinstance(resource._get_inspect_result(), NodeInspectResult)
            for resource in resources
        )
        assert all(
            resource.client_config is client.client_config for resource in resources
        )
        assert resources[0] is not resources[4]
        command.assert_called_once()
        assert command.call_args.args[0] == [
            "docker-test",
            "--host",
            "inventory-host",
            "node",
            "inspect",
            *references,
        ]


def test_batch_inspect_node_scalar_and_lazy_list_contracts() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess(
            [], 0, b'[{"ID":"identity","Description":{"Hostname":"named"}}]', b""
        ),
    ) as command:
        resource = client.node.inspect("named")
        assert isinstance(resource, Node)
        assert resource.id == "identity"
        assert isinstance(resource._inspect_result, NodeInspectResult)
        command.return_value = CompletedProcess([], 0, b"identity", b"")
        listed = client.node.list()
        assert listed[0].id == "identity"
        assert listed[0]._inspect_result is None
        assert command.call_count == 2


def test_batch_inspect_empty_node_avoids_executable_lookup() -> None:
    client = DockerClient(client_call=["not-installed"])
    with (
        patch("shutil.which", side_effect=AssertionError("executable lookup")),
        patch("subprocess.run", side_effect=AssertionError("subprocess")),
    ):
        assert client.node.inspect([]) == []


@pytest.mark.parametrize(
    "payload",
    [
        "invalid json",
        "{}",
        "[]",
        "[null,null]",
        "[{},{}]",
        '[{"ID":"first"}]',
        '[{"ID":"first"},{"ID":"second"},{}]',
        '[{"ID":"wrong"},{"ID":"second"}]',
        '[{"ID":""},{"ID":"second"}]',
        '[{"ID":"first"},{"ID":"second","CreatedAt":"invalid"}]',
        '[{"ID":"one","Description":{"Hostname":"first"}},{"ID":"two","Description":{"Hostname":"first"}}]',
        '[{"ID":"one","Spec":{"Name":"first"}},{"ID":"two","Spec":{"Name":"second"}}]',
    ],
)
def test_batch_inspect_node_rejects_invalid_response(payload: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, payload.encode(), b""),
        ),
        patch.object(Node, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        client.node.inspect(["first", "second"])
    hydrate.assert_not_called()


def test_batch_inspect_node_preserves_partial_command_failure() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    output = b'[{"ID":"first"}]'
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 17, output, b"permission denied"),
        ) as command,
        patch.object(Node, "_set_inspect_result") as hydrate,
        pytest.raises(DockerException) as failure,
    ):
        client.node.inspect(["first", "second"])
    assert failure.value.return_code == 17
    assert failure.value.stdout == output.decode()
    assert failure.value.stderr == "permission denied"
    assert failure.value.docker_command == [
        "docker-test",
        "node",
        "inspect",
        "first",
        "second",
    ]
    command.assert_called_once()
    hydrate.assert_not_called()


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
