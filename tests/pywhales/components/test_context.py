import json
from collections.abc import Callable, Iterable
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ParsingError
from pywhales.components.context.cli_wrapper import Context
from pywhales.components.context.models import ContextInspectResult
from pywhales.test_utils import get_all_jsons


@pytest.mark.parametrize("json_file", get_all_jsons("contexts"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    ContextInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


def test_create_context(docker_client: DockerClient) -> None:
    testname = "testpow"
    host = "ssh://test@test.domain"
    description = "PyWhales testing context"

    all_contexts_before = set(docker_client.context.list())
    with docker_client.context.create(
        testname, docker={"host": host}, description=description
    ) as new_context:
        assert new_context.name == testname
        assert new_context.endpoints is not None
        assert new_context.endpoints["docker"].host == host
        assert new_context.metadata is not None
        assert new_context.metadata["Description"] == description

        assert new_context not in all_contexts_before

        assert new_context in docker_client.context.list()


def test_inpect(docker_client: DockerClient) -> None:
    default_context = docker_client.context.inspect()
    assert default_context.name == "default"
    assert default_context == docker_client.context.inspect("default")
    a, b = docker_client.context.inspect(["default", "default"])
    assert a == b


def test_list_contexts(docker_client: DockerClient) -> None:
    assert docker_client.context.list() == [docker_client.context.inspect("default")]


def test_use_context(docker_client: DockerClient) -> None:
    docker_client.context.use("default")


def test_use_context_returns(docker_client: DockerClient) -> None:
    assert docker_client.context.use("default") == docker_client.context.inspect(
        "default"
    )


def test_remove_empty_context_list(docker_client: DockerClient) -> None:
    all_contexts = set(docker_client.context.list())
    docker_client.context.remove([])
    assert all_contexts == set(docker_client.context.list())


def test_batch_inspect_preserves_context_order_and_duplicate_handles() -> None:
    client = DockerClient(host="inventory-host")
    client.client_config._client_call_with_path = ["docker-test"]
    references = ["second", "first", "second"]
    payload = [{"Name": reference} for reference in references]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess([], 0, json.dumps(payload).encode(), b""),
    ) as command:
        resources = client.context.inspect(references)

    assert [resource.name for resource in resources] == references
    assert resources[0] is not resources[2]
    assert all(resource._inspect_result is not None for resource in resources)
    assert all(resource.client_config is client.client_config for resource in resources)
    command.assert_called_once()
    assert command.call_args.args[0] == [
        "docker-test",
        "--host",
        "inventory-host",
        "context",
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
        '[{"Name": "first"}]',
        '[{"Name": "first"}, {"Name": "first"}, {"Name": "first"}]',
        '[{"Name": "first"}, {"Name": 2}]',
        '[{"Name": "first"}, {"Name": ""}]',
        '[{"Name": "first"}, {}]',
        '[{"Name": "first"}, {"Name": "first-longer"}]',
        '[{"Name": "first"}, {"Name": "first", "Metadata": {"changed": true}}]',
    ],
)
def test_batch_inspect_validates_all_contexts_before_hydration(payload: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, payload.encode(), b""),
        ) as command,
        patch.object(Context, "_set_inspect_result") as hydrate,
        pytest.raises(ParsingError),
    ):
        client.context.inspect(["first", "first"])
    command.assert_called_once()
    hydrate.assert_not_called()


def test_batch_inspect_empty_contexts_avoids_lookup() -> None:
    with patch("shutil.which") as lookup, patch("subprocess.run") as command:
        assert DockerClient().context.inspect([]) == []
    lookup.assert_not_called()
    command.assert_not_called()


def test_scalar_inspect_context_none_keeps_current_context_lookup() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess([], 0, b'[{"Name": "current"}]', b""),
    ) as command:
        resource = client.context.inspect(None)
    assert resource.name == "current"
    command.assert_called_once()
    assert command.call_args.args[0] == ["docker-test", "context", "inspect"]


EMPTY_SELECTIONS = [
    pytest.param(list, id="list"),
    pytest.param(tuple, id="tuple"),
    pytest.param(lambda: (item for item in ()), id="generator"),
]
SINGLE_SELECTIONS = [
    pytest.param(lambda: "alpha", id="str"),
    pytest.param(lambda: ["alpha"], id="list"),
    pytest.param(lambda: (item for item in ["alpha"]), id="generator"),
]


@pytest.mark.parametrize("make_empty", EMPTY_SELECTIONS)
def test_context_remove_empty_selection_skips_cli(
    make_empty: Callable[[], Iterable[str]],
) -> None:
    client = DockerClient(client_call=["not-installed"])
    with (
        patch("shutil.which", side_effect=AssertionError("executable lookup")),
        patch("subprocess.run", side_effect=AssertionError("subprocess")),
    ):
        client.context.remove(make_empty(), force=True)


@pytest.mark.parametrize("make_selection", SINGLE_SELECTIONS)
def test_context_remove_single_selection_builds_argv_once(
    make_selection: Callable[[], str | Iterable[str]],
) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch(
        "subprocess.run", return_value=CompletedProcess([], 0, b"", b"")
    ) as command:
        client.context.remove(make_selection(), force=True)
    command.assert_called_once()
    assert command.call_args.args[0] == [
        "docker-test",
        "context",
        "remove",
        "--force",
        "alpha",
    ]
