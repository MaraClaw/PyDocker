import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pydocker import Config, DockerClient
from pydocker.client_config import ParsingError
from pydocker.components.config.models import ConfigInspectResult
from pydocker.exceptions import DockerException


def test_batch_inspect_matches_config_references_and_hydrates_cache() -> None:
    client = DockerClient(host="inventory-host")
    client.client_config._client_call_with_path = ["docker-test"]
    alpha = {"ID": "a" * 27, "Spec": {"Name": "alpha", "Data": "YQ=="}}
    bravo = {"ID": "b" * 27, "Spec": {"Name": "bravo", "Data": "Yg=="}}
    references = ["bravo", "a" * 27, "b" * 8, "alpha", "bravo"]
    response = json.dumps([alpha, bravo, alpha, bravo, bravo]).encode()
    with (
        patch(
            "subprocess.run", return_value=CompletedProcess([], 0, response, b"")
        ) as command,
        patch("pydocker.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        resources = client.config.inspect(references)
        assert [resource.id for resource in resources] == [
            "b" * 27,
            "a" * 27,
            "b" * 27,
            "a" * 27,
            "b" * 27,
        ]
        assert [
            resource.spec.name for resource in resources if resource.spec is not None
        ] == ["bravo", "alpha", "bravo", "alpha", "bravo"]
        assert all(
            isinstance(resource._get_inspect_result(), ConfigInspectResult)
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
            "config",
            "inspect",
            *references,
        ]


def test_batch_inspect_config_scalar_and_lazy_list_contracts() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess(
            [], 0, b'[{"ID":"identity","Spec":{"Name":"named"}}]', b""
        ),
    ) as command:
        resource = client.config.inspect("named")
        assert isinstance(resource, Config)
        assert resource.id == "identity"
        assert isinstance(resource._inspect_result, ConfigInspectResult)
        command.return_value = CompletedProcess([], 0, b"identity", b"")
        listed = client.config.list()
        assert listed[0].id == "identity"
        assert listed[0]._inspect_result is None
        assert command.call_count == 2


def test_batch_inspect_empty_config_avoids_executable_lookup() -> None:
    client = DockerClient(client_call=["not-installed"])
    with (
        patch("shutil.which", side_effect=AssertionError("executable lookup")),
        patch("subprocess.run", side_effect=AssertionError("subprocess")),
    ):
        assert client.config.inspect([]) == []


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
        '[{"ID":"one","Spec":{"Name":"first"}},{"ID":"two","Spec":{"Name":"first"}}]',
    ],
)
def test_batch_inspect_config_rejects_invalid_response(payload: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, payload.encode(), b""),
        ),
        patch.object(Config, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        client.config.inspect(["first", "second"])
    hydrate.assert_not_called()


def test_batch_inspect_config_preserves_partial_command_failure() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    output = b'[{"ID":"first"}]'
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 17, output, b"permission denied"),
        ) as command,
        patch.object(Config, "_set_inspect_result") as hydrate,
        pytest.raises(DockerException) as failure,
    ):
        client.config.inspect(["first", "second"])
    assert failure.value.return_code == 17
    assert failure.value.stdout == output.decode()
    assert failure.value.stderr == "permission denied"
    assert failure.value.docker_command == [
        "docker-test",
        "config",
        "inspect",
        "first",
        "second",
    ]
    command.assert_called_once()
    hydrate.assert_not_called()


@pytest.mark.usefixtures("swarm_mode")
def test_list_configs(docker_client: DockerClient) -> None:
    assert docker_client.config.list() == []


@pytest.mark.usefixtures("swarm_mode")
def test_create_delete_config(docker_client: DockerClient, tmp_path: Path) -> None:
    config_file = tmp_path / "config.conf"
    config_file.write_text("hello world")
    my_conf = docker_client.config.create("my_conf", config_file)
    with my_conf:
        assert my_conf.spec is not None
        assert my_conf.spec.name == "my_conf"
        assert docker_client.config.list() == [my_conf]
        assert docker_client.config.inspect("my_conf") == my_conf
        repr(docker_client.config.list())


@pytest.mark.usefixtures("swarm_mode")
def test_labels_config(docker_client: DockerClient, tmp_path: Path) -> None:
    config_file = tmp_path / "config.conf"
    config_file.write_text("hello world")
    my_conf = docker_client.config.create(
        "my_conf", config_file, labels={"dodo": "dada"}
    )
    with my_conf:
        assert my_conf.spec is not None
        assert my_conf.spec.name == "my_conf"
        assert docker_client.config.list(filters={"label": "dodo=dada"}) == [my_conf]
        assert docker_client.config.list(filters={"label": "dodo=dadu"}) == []


@pytest.mark.usefixtures("swarm_mode")
def test_remove_empty_config_list(docker_client: DockerClient, tmp_path: Path) -> None:
    config_file = tmp_path / "config.conf"
    config_file.write_text("hello world")
    with docker_client.config.create("my_conf", config_file) as my_conf:
        assert docker_client.config.list() == [my_conf]
        docker_client.config.remove([])
        assert docker_client.config.list() == [my_conf]
