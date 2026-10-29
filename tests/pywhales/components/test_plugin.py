import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ParsingError
from pywhales.components.plugin.cli_wrapper import Plugin
from pywhales.components.plugin.models import PluginInspectResult
from pywhales.test_utils import get_all_jsons

test_plugin_name = "vieux/sshfs:latest"


def get_all_plugins_jsons() -> list[Path]:
    jsons_directory = Path(__file__).parent / "plugins"
    return sorted(jsons_directory.iterdir())


@pytest.mark.parametrize("json_file", get_all_jsons("plugins"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    PluginInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


def test_install_plugin_disable_enable(docker_client: DockerClient) -> None:
    with docker_client.plugin.install(test_plugin_name) as my_plugin:
        my_plugin.disable()
        my_plugin.enable()
        assert my_plugin in docker_client.plugin.list()
        assert "name='vieux/sshfs:latest'" in repr(my_plugin)


def test_plugin_upgrade(docker_client: DockerClient) -> None:
    with docker_client.plugin.install(test_plugin_name) as my_plugin:
        my_plugin.disable()
        my_plugin.upgrade()


def test_remove_empty_plugin_list(docker_client: DockerClient) -> None:
    with docker_client.plugin.install(test_plugin_name) as my_plugin:
        plugins_set = set(docker_client.plugin.list())
        assert my_plugin in plugins_set
        docker_client.plugin.remove([])
        assert set(docker_client.plugin.list()) == plugins_set


def test_batch_inspect_preserves_plugin_cli_aliases_positionally() -> None:
    client = DockerClient(host="inventory-host")
    client.client_config._client_call_with_path = ["docker-test"]
    references = [
        "registry.example/team/plugin",
        "local-alias",
        "registry.example/team/plugin",
    ]
    payload = [
        {"Id": "a" * 64, "Name": "different:latest"},
        {"Id": "b" * 64, "Name": "different:custom"},
        {"Id": "a" * 64, "Name": "different:latest"},
    ]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess([], 0, json.dumps(payload).encode(), b""),
    ) as command:
        resources = client.plugin.inspect(references)
    assert [resource.id for resource in resources] == ["a" * 64, "b" * 64, "a" * 64]
    assert resources[0] is not resources[2]
    assert all(resource._inspect_result is not None for resource in resources)
    assert all(resource.client_config is client.client_config for resource in resources)
    command.assert_called_once()
    assert command.call_args.args[0] == [
        "docker-test",
        "--host",
        "inventory-host",
        "plugin",
        "inspect",
        *references,
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "null",
        "[1, 2]",
        "[]",
        '[{"Id": "one"}]',
        '[{"Id": "one"}, {"Id": "one"}, {"Id": "one"}]',
        '[{"Id": "one"}, {"Id": 2}]',
        '[{"Id": "one"}, {"Id": ""}]',
        '[{"Id": "one"}, {}]',
        '[{"Id": "one"}, {"Id": "two"}]',
        '[{"Id": "one"}, {"Id": "one", "Enabled": true}]',
    ],
)
def test_batch_inspect_validates_all_plugins_before_hydration(payload: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, payload.encode(), b""),
        ) as command,
        patch.object(Plugin, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        client.plugin.inspect(["alias", "alias"])
    command.assert_called_once()
    hydrate.assert_not_called()


def test_batch_inspect_rejects_plugin_full_id_mismatch_before_hydration() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    payload = json.dumps([{"Id": "a" * 64}, {"Id": "c" * 64}]).encode()
    with (
        patch("subprocess.run", return_value=CompletedProcess([], 0, payload, b"")),
        patch.object(Plugin, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        client.plugin.inspect(["a" * 64, "b" * 64])
    hydrate.assert_not_called()


def test_batch_inspect_empty_plugins_avoids_lookup() -> None:
    with patch("shutil.which") as lookup, patch("subprocess.run") as command:
        assert DockerClient().plugin.inspect([]) == []
    lookup.assert_not_called()
    command.assert_not_called()


@pytest.mark.parametrize("handle", [False, True])
@pytest.mark.parametrize("timeout", [None, 2.5])
def test_plugin_push_forwards_timeout(handle: bool, timeout: float | None) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch("pywhales.components.plugin.cli_wrapper.run") as mock_run:
        mock_run.return_value = ""

        if handle:
            Plugin(client.client_config, test_plugin_name, is_immutable_id=True).push(
                timeout=timeout
            )
        else:
            client.plugin.push(test_plugin_name, timeout=timeout)

    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["timeout"] == timeout


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), True])
def test_plugin_push_invalid_timeout_precedes_launch(timeout: float) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with (
        patch("pywhales.components.plugin.cli_wrapper.run") as mock_run,
        pytest.raises(ValueError),
    ):
        client.plugin.push(test_plugin_name, timeout=timeout)

    mock_run.assert_not_called()
