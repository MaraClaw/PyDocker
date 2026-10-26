import datetime as dt
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import DockerClient, Secret
from pywhales.client_config import ParsingError
from pywhales.components.secret.models import SecretInspectResult
from pywhales.exceptions import DockerException


def test_batch_inspect_matches_secret_single_pass_references_and_hydrates_cache() -> (
    None
):
    client = DockerClient(host="inventory-host")
    client.client_config._client_call_with_path = ["docker-test"]
    alpha = {"ID": "a" * 27, "Spec": {"Name": "alpha", "Labels": {"role": "alpha"}}}
    bravo = {"ID": "b" * 27, "Spec": {"Name": "bravo", "Labels": {"role": "bravo"}}}
    references = ["bravo", "a" * 27, "b" * 8, "alpha", "bravo"]
    response = json.dumps([alpha, bravo, alpha, bravo, bravo]).encode()
    with (
        patch(
            "subprocess.run", return_value=CompletedProcess([], 0, response, b"")
        ) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        resources = client.secret.inspect(iter(references))
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
            isinstance(resource._get_inspect_result(), SecretInspectResult)
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
            "secret",
            "inspect",
            *references,
        ]


def test_batch_inspect_secret_scalar_and_lazy_list_contracts() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess(
            [], 0, b'[{"ID":"identity","Spec":{"Name":"named"}}]', b""
        ),
    ) as command:
        resource = client.secret.inspect("named")
        assert isinstance(resource, Secret)
        assert resource.id == "identity"
        assert isinstance(resource._inspect_result, SecretInspectResult)
        command.return_value = CompletedProcess([], 0, b"identity", b"")
        listed = client.secret.list()
        assert listed[0].id == "identity"
        assert listed[0]._inspect_result is None
        assert command.call_count == 2


def test_batch_inspect_empty_secret_avoids_executable_lookup() -> None:
    client = DockerClient(client_call=["not-installed"])
    with (
        patch("shutil.which", side_effect=AssertionError("executable lookup")),
        patch("subprocess.run", side_effect=AssertionError("subprocess")),
    ):
        assert client.secret.inspect([]) == []
        assert client.secret.inspect(iter(())) == []


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
def test_batch_inspect_secret_rejects_invalid_response(payload: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, payload.encode(), b""),
        ),
        patch.object(Secret, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        client.secret.inspect(["first", "second"])
    hydrate.assert_not_called()


def test_batch_inspect_secret_preserves_partial_command_failure() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    output = b'[{"ID":"first"}]'
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 17, output, b"permission denied"),
        ) as command,
        patch.object(Secret, "_set_inspect_result") as hydrate,
        pytest.raises(DockerException) as failure,
    ):
        client.secret.inspect(["first", "second"])
    assert failure.value.return_code == 17
    assert failure.value.stdout == output.decode()
    assert failure.value.stderr == "permission denied"
    assert failure.value.docker_command == [
        "docker-test",
        "secret",
        "inspect",
        "first",
        "second",
    ]
    command.assert_called_once()
    hydrate.assert_not_called()


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_update_auto_lock_managers(
    docker_client: DockerClient, tmp_path: Path
) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("hello")
    my_secret = docker_client.secret.create("my_secret", secret_file)
    date_after_creation = dt.datetime.now(tz=dt.UTC)
    print("my_secret: ", my_secret.id)
    my_secret2 = docker_client.secret.inspect(my_secret.id)

    assert my_secret == my_secret2
    assert my_secret.spec is not None
    assert my_secret.spec.name == "my_secret"
    assert my_secret.spec.labels == {}
    assert my_secret.created_at is not None
    assert my_secret.created_at <= date_after_creation

    assert my_secret2.spec is not None
    assert my_secret2.spec.name == "my_secret"
    assert my_secret2.spec.labels == {}
    assert my_secret2.created_at is not None
    assert my_secret2.created_at <= date_after_creation

    my_secret3 = docker_client.secret.inspect("my_secret")

    assert my_secret == my_secret3
    assert my_secret3.spec is not None
    assert my_secret3.spec.name == "my_secret"
    assert my_secret3.spec.labels == {}
    assert my_secret3.created_at is not None
    assert my_secret3.created_at <= date_after_creation

    docker_client.secret.remove(my_secret.id)
