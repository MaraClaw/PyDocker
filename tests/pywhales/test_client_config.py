import json
import sys
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from pytest_mock import MockerFixture

from pywhales import Config, DockerClient, Node, Secret, Service, docker
from pywhales._execution_policy import get_execution_policy
from pywhales.client_config import ClientConfig, ParsingError
from pywhales.utils import PROJECT_ROOT


@pytest.mark.parametrize("budget", [True, False, 0, -1, 1.5, "8", b"8"])
def test_general_stream_budget_rejects_invalid_config_before_lookup(
    budget: int | None,
) -> None:
    with patch("shutil.which") as lookup, pytest.raises(ValueError):
        ClientConfig(stream_buffer_bytes=budget)

    lookup.assert_not_called()


@pytest.mark.parametrize("budget", [None, 1, 1024])
def test_general_stream_budget_preserves_independent_image_override(
    budget: int | None,
) -> None:
    config = ClientConfig(stream_buffer_bytes=budget)

    assert config.stream_buffer_bytes == budget
    assert config.image_stream_buffer_bytes is None
    assert config.raw_diagnostics is False


def test_docker_command_policy_does_not_replace_cached_executable_prefix() -> None:
    config = ClientConfig(
        client_call=[sys.executable, "engine"],
        stream_buffer_bytes=17,
        raw_diagnostics=True,
    )
    cached = config.get_client_call_with_path()

    command = config.docker_cmd
    command.append("version")
    config.stream_buffer_bytes = 31

    assert config.get_client_call_with_path() is cached
    assert cached == [Path(sys.executable), "engine"]
    assert isinstance(cached[0], Path)
    assert isinstance(command[0], str)
    assert get_execution_policy(command).stream_buffer_bytes == 31
    assert get_execution_policy(command).raw_diagnostics is True


@pytest.mark.parametrize("budget", [True, False, 0, -1, 1.5, "8", b"8"])
def test_image_stream_budget_rejects_invalid_direct_config_before_lookup(
    budget: int | None,
) -> None:
    with patch("shutil.which") as lookup, pytest.raises(ValueError):
        ClientConfig(image_stream_buffer_bytes=budget)

    lookup.assert_not_called()


@pytest.mark.parametrize("budget", [None, 1, 1024])
def test_image_stream_budget_keeps_valid_direct_config_without_lookup(
    budget: int | None,
) -> None:
    with patch("shutil.which") as lookup:
        config = ClientConfig(image_stream_buffer_bytes=budget)

    assert config.image_stream_buffer_bytes == budget
    lookup.assert_not_called()


@pytest.mark.parametrize("component", ["service", "node", "config", "secret"])
@pytest.mark.parametrize("explicit_reload", [False, True], ids=["expired", "explicit"])
def test_swarm_batch_reload_retains_originating_configuration(
    component: str, explicit_reload: bool
) -> None:
    client = DockerClient(context="inventory-context", host="inventory-host", tls=True)
    client.client_config._client_call_with_path = ["docker-test", "--custom-wrapper"]
    api = {
        "service": client.service,
        "node": client.node,
        "config": client.config,
        "secret": client.secret,
    }[component]
    snapshot = {
        "ID": "a" * 27,
        "Spec": {"Name": "named", "Labels": {"version": "before"}},
        "Description": {"Hostname": "named"},
    }
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, json.dumps([snapshot]).encode(), b""),
        ) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        resource = api.inspect(["named"])[0]
        assert resource.spec is not None
        assert resource.spec.labels == {"version": "before"}
        initial = resource._get_inspect_result()
        assert resource.client_config is client.client_config
        command.assert_called_once()
        snapshot["Spec"]["Labels"] = {"version": "after"}
        command.return_value = CompletedProcess(
            [], 0, json.dumps([snapshot]).encode(), b""
        )

        if explicit_reload:
            resource.reload()
        else:
            resource._last_refreshed_time = datetime.min
        refreshed = resource._get_inspect_result()

        assert refreshed is not initial
        assert resource.spec is not None
        assert resource.spec.labels == {"version": "after"}
        assert command.call_count == 2
        assert command.call_args.args[0] == [
            "docker-test",
            "--custom-wrapper",
            "--context",
            "inventory-context",
            "--host",
            "inventory-host",
            "--tls",
            component,
            "inspect",
            "a" * 27,
        ]


@pytest.mark.parametrize("component", ["service", "node", "config", "secret"])
@pytest.mark.parametrize(
    ("references", "payload"),
    [
        (["shared", "other"], [{"ID": "shared-one"}, {"ID": "shared-two"}]),
        (
            ["named", "named"],
            [
                {
                    "ID": "one",
                    "Spec": {"Name": "named"},
                    "Description": {"Hostname": "named"},
                },
                {
                    "ID": "two",
                    "Spec": {"Name": "named"},
                    "Description": {"Hostname": "named"},
                },
            ],
        ),
        (["one", "one"], [{"ID": "one"}, {"ID": "two"}]),
        (["one", "two", "two"], [{"ID": "one"}, {"ID": "one"}, {"ID": "two"}]),
        (
            ["one", "one"],
            [
                {"ID": "one", "Spec": {"Name": "before"}},
                {"ID": "one", "Spec": {"Name": "after"}},
            ],
        ),
        (["", "two"], [{"ID": "one"}, {"ID": "two"}]),
    ],
    ids=[
        "ambiguous-prefix",
        "ambiguous-duplicate-name",
        "unrequested-identity",
        "wrong-multiplicity",
        "conflicting-snapshot",
        "empty-reference",
    ],
)
def test_swarm_batch_rejects_ambiguous_or_mismatched_identities_before_hydration(
    component: str,
    references: list[str],
    payload: list[dict[str, str | dict[str, str]]],
) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    api = {
        "service": client.service,
        "node": client.node,
        "config": client.config,
        "secret": client.secret,
    }[component]
    resource_type = {
        "service": Service,
        "node": Node,
        "config": Config,
        "secret": Secret,
    }[component]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, json.dumps(payload).encode(), b""),
        ) as command,
        patch.object(resource_type, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        api.inspect(references)
    command.assert_called_once()
    hydrate.assert_not_called()


@pytest.mark.parametrize("component", ["service", "node", "config", "secret"])
@pytest.mark.parametrize("batch", [False, True], ids=["scalar", "reordered-batch"])
@pytest.mark.parametrize(
    "collision",
    [
        ("a" * 8, "a" * 8, "other"),
        ("b" * 27, "selected", "b" * 27),
    ],
    ids=["name-over-prefix", "full-id-over-name"],
)
def test_swarm_inspect_uses_docker_identity_precedence(
    component: str, batch: bool, collision: tuple[str, str, str]
) -> None:
    reference, selected_name, other_name = collision
    client = DockerClient(host="inventory-host")
    client.client_config._client_call_with_path = ["docker-test"]
    api = {
        "service": client.service,
        "node": client.node,
        "config": client.config,
        "secret": client.secret,
    }[component]
    selected = {
        "ID": "b" * 27,
        "Spec": {"Name": selected_name, "Labels": {"match": "selected"}},
        "Description": {"Hostname": selected_name},
    }
    other = {
        "ID": "a" * 27,
        "Spec": {"Name": other_name, "Labels": {"match": "other"}},
        "Description": {"Hostname": other_name},
    }
    references = [reference, "a" * 27, reference] if batch else [reference]
    payload = [other, selected, selected] if batch else [selected]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, json.dumps(payload).encode(), b""),
        ) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        resources = api.inspect(references) if batch else [api.inspect(reference)]

        assert [resource.id for resource in resources] == (
            ["b" * 27, "a" * 27, "b" * 27] if batch else ["b" * 27]
        )
        assert [
            resource.spec.labels for resource in resources if resource.spec is not None
        ] == (
            [{"match": "selected"}, {"match": "other"}, {"match": "selected"}]
            if batch
            else [{"match": "selected"}]
        )
        assert all(
            resource.client_config is client.client_config for resource in resources
        )
        if batch:
            assert resources[0] is not resources[2]
        command.assert_called_once()
        assert command.call_args.args[0] == [
            "docker-test",
            "--host",
            "inventory-host",
            component,
            "inspect",
            *references,
        ]


fake_json_message = {
    "CreatedAt": "2020-10-08T18:32:55Z",
    "Driver": ["dummy", "fake", ["driver"]],
    "Labels": {"com.docker.stack.namespace": "dodo"},
    "Mountpoint": "/var/lib/docker/volumes/dodo_traefik-data/_data",
    "Name": "dodo_traefik-data",
    "Options": None,
    "Scope": "local",
}


def test_pretty_exception_message_and_report(mocker: MockerFixture) -> None:
    mocker.patch(
        "pywhales.components.volume.cli_wrapper.Volume._fetch_inspect_result_json",
        lambda x, y: fake_json_message,
    )
    with pytest.raises(ParsingError) as err:
        docker.volume.inspect("random_volume")
    error_message = str(err.value)

    assert "This is a bug with pywhales itself" in error_message

    # we get the path of the json file with the dump of the response.
    for word in error_message.split():
        if ".json" in word:
            break
    else:
        raise IndexError

    assert Path(word).read_text() == json.dumps(fake_json_message, indent=2)


def test_compose_env_file() -> None:
    """Test that the deprecated `compose_env_file` gives a warning, and adds the `--env-file` argument to the compose command"""
    with pytest.warns(UserWarning):
        example_env_file_name = "example.env"
        client_config = ClientConfig(compose_env_file=example_env_file_name)
        assert client_config.docker_compose_cmd.count("--env-file") == 1
        # Since we are operating of a list of commands, we first find the index of the `--env-file` argument, then we check that the next argument is the file name.
        index = client_config.docker_compose_cmd.index("--env-file")
        assert client_config.docker_compose_cmd[index + 1] == example_env_file_name


@pytest.mark.parametrize(
    "compose_env_files",
    [
        ["example1.env", "example2.env"],
        ["example1.env"],
    ],
    ids=["multiple_files", "single_file"],
)
def test_compose_env_files(compose_env_files: Sequence[str]) -> None:
    """Test that using only `compose_env_files` adds all the files to the command line arguments"""
    client_config = ClientConfig(compose_env_files=compose_env_files)
    # Since we are operating of a list of commands, we first find the indices of the `--env-file` argument, then we check that the next argument is the file name.
    indices = [
        index
        for index, item in enumerate(client_config.docker_compose_cmd)
        if item == "--env-file"
    ]
    assert len(indices) == len(compose_env_files)
    for index, env_file in zip(indices, compose_env_files, strict=True):
        assert client_config.docker_compose_cmd[index + 1] == env_file


def test_compose_env_files_and_env_file() -> None:
    """Test that using both `compose_env_files` and `compose_env_file` gives a warning, and only uses `compose_env_files`"""
    env_files_input = "example1.env"
    env_file_input = "example2.env"

    with pytest.warns(UserWarning):
        client_config = ClientConfig(
            compose_env_files=[env_files_input], compose_env_file=env_file_input
        )
        assert client_config.docker_compose_cmd.count("--env-file") == len(
            [env_files_input]
        )
        # Since we are operating of a list of commands, we first find the index of the `--env-file` argument, then we check that the next argument is the file name.
        index = client_config.docker_compose_cmd.index("--env-file")
        assert client_config.docker_compose_cmd[index + 1] == env_files_input


@contextmanager
def _create_temp_env_files() -> Generator[tuple[Path, Path]]:
    """Creates two temporary env files and yields their paths. The files are deleted after the context manager is exited."""
    path = PROJECT_ROOT / "tests/pywhales/components"
    env_file_1 = path / "example1.env"
    with env_file_1.open("w") as f:
        f.write("A=1\n")
        f.write("B=1\n")
    env_file_2 = path / "example2.env"
    with env_file_2.open("w") as f:
        f.write("B=2\n")
    yield env_file_1, env_file_2
    env_file_1.unlink()
    env_file_2.unlink()


def test_run_compose_command_with_env_file() -> None:
    """Test that checks the configuration for the compose stack and checks that the env files are loaded correctly

    The docker compose service, `service_using_env_variables`, is defined in `dummy_compose.yml` and uses the env variables `A` and `B`.
    If no env files are provided, the values of `A` and `B` are set to a default value of `0` in the service.
    We check if the values of `A` and `B` are set to the values in the env files.

    The cases tested are:
    * Single env file using the old `compose_env_file` argument
    * Single env file using the new `compose_env_files` argument
    * Multiple env files using the new `compose_env_files` argument
        * Later env files should override the values of earlier env files
        * The order of the env files should matter
    """
    with _create_temp_env_files() as (env_file_1, env_file_2):
        COMPOSE_FILE = PROJECT_ROOT / "tests/pywhales/components/dummy_compose.yml"
        SERVICE = "service_using_env_variables"

        # Test with no env-file to check the default values, should be `0`, as is default in the service definition.
        client = DockerClient(compose_files=[COMPOSE_FILE])
        config = client.compose.config()
        assert config.services is not None
        environment = config.services[SERVICE].environment
        assert isinstance(environment, dict)
        assert "A" in environment and environment["A"] == "0"
        assert "B" in environment and environment["B"] == "0"

        # Test with single env-file using the old `compose_env_file` argument
        client = DockerClient(
            compose_files=[COMPOSE_FILE],
            compose_env_file=env_file_1,
        )
        config = client.compose.config()
        assert config.services is not None
        environment = config.services[SERVICE].environment
        assert isinstance(environment, dict)
        assert "A" in environment and environment["A"] == "1"
        assert "B" in environment and environment["B"] == "1"

        # Test with single env-file using the new `compose_env_files` argument
        client = DockerClient(
            compose_files=[COMPOSE_FILE],
            compose_env_files=[env_file_1],
        )
        config = client.compose.config()
        assert config.services is not None
        environment = config.services[SERVICE].environment
        assert isinstance(environment, dict)
        assert "A" in environment and environment["A"] == "1"
        assert "B" in environment and environment["B"] == "1"

        # Test with multiple env-files using the new `compose_env_files` argument.
        # Since `env_file2` is loaded after `env_file1`, the value of `B` should be overridden by `env_file_2`.
        client = DockerClient(
            compose_files=[COMPOSE_FILE],
            compose_env_files=[env_file_1, env_file_2],
        )
        config = client.compose.config()
        assert config.services is not None
        environment = config.services[SERVICE].environment
        assert isinstance(environment, dict)
        assert "A" in environment and environment["A"] == "1"
        assert "B" in environment and environment["B"] == "2"

        # Test with multiple env-files using the new `compose_env_files` argument.
        # Since `env_file1` is loaded after `env_file2`, the value of `B` should be overridden by `env_file_1`.
        client = DockerClient(
            compose_files=[COMPOSE_FILE],
            compose_env_files=[env_file_2, env_file_1],
        )
        config = client.compose.config()
        assert config.services is not None
        environment = config.services[SERVICE].environment
        assert isinstance(environment, dict)
        assert "A" in environment and environment["A"] == "1"
        assert "B" in environment and environment["B"] == "1"
