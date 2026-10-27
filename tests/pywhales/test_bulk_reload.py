import json
from datetime import datetime
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import (
    Config,
    Container,
    Context,
    Image,
    Network,
    Node,
    Plugin,
    Secret,
    Service,
    Task,
    Volume,
)
from pywhales.client_config import (
    ClientConfig,
    ParsingError,
    ReloadableObjectFromJson,
    bulk_reload,
)
from pywhales.exceptions import DockerException

RESOURCE_TYPES = [
    Container,
    Image,
    Network,
    Volume,
    Node,
    Service,
    Config,
    Secret,
    Task,
    Plugin,
    Context,
]
type SupportedResource = (
    Container
    | Image
    | Network
    | Volume
    | Node
    | Service
    | Config
    | Secret
    | Task
    | Plugin
    | Context
)


@pytest.mark.parametrize("resource_type", RESOURCE_TYPES)
def test_bulk_reload_dispatches_each_supported_kind(
    resource_type: type[SupportedResource],
) -> None:
    config = ClientConfig(
        _client_call_with_path=["docker-test", "--wrapper"], host="inventory-host"
    )
    resource = resource_type(config, "identity", is_immutable_id=True)
    snapshot = {"Id": "identity", "ID": "identity", "Name": "identity"}
    kind = resource_type.__name__.lower()
    suffix = ["inspect", "--type", "task"] if kind == "task" else [kind, "inspect"]
    prefix = ["docker-test", "--wrapper", "--host", "inventory-host"]
    with patch(
        "subprocess.run",
        return_value=CompletedProcess([], 0, json.dumps([snapshot]).encode(), b""),
    ) as command:
        bulk_reload([resource])
    assert resource._inspect_result is not None
    assert resource._last_refreshed_time > datetime.min
    command.assert_called_once()
    assert command.call_args.args[0] == [*prefix, *suffix, "identity"]


def test_bulk_reload_groups_by_exact_config_identity_and_kind() -> None:
    first_config = ClientConfig(_client_call_with_path=["docker-test"])
    equal_config = ClientConfig(_client_call_with_path=["docker-test"])
    assert first_config == equal_config
    first = Context(first_config, "first", is_immutable_id=True)
    second = Context(equal_config, "second", is_immutable_id=True)
    plugin = Plugin(first_config, "plugin", is_immutable_id=True)
    with patch(
        "subprocess.run",
        side_effect=[
            CompletedProcess([], 0, b'[{"Name": "first"}]', b""),
            CompletedProcess([], 0, b'[{"Name": "second"}]', b""),
            CompletedProcess([], 0, b'[{"Id": "plugin"}]', b""),
        ],
    ) as command:
        bulk_reload([first, second, plugin])
    assert command.call_count == 3
    assert [call.args[0][1:] for call in command.call_args_list] == [
        ["context", "inspect", "first"],
        ["context", "inspect", "second"],
        ["plugin", "inspect", "plugin"],
    ]
    assert all(
        resource._inspect_result is not None for resource in [first, second, plugin]
    )


@pytest.mark.parametrize("failure", ["cli", "shape", "count", "model", "identity"])
@pytest.mark.parametrize("resource_type", RESOURCE_TYPES)
def test_bulk_reload_later_group_failure_preserves_every_snapshot_and_timestamp(
    failure: str, resource_type: type[SupportedResource]
) -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    other_config = ClientConfig(_client_call_with_path=["docker-test"])
    first = Context(config, "first", is_immutable_id=True)
    seed = json.dumps([{"Id": "second", "ID": "second", "Name": "second"}]).encode()
    with patch("subprocess.run", return_value=CompletedProcess([], 0, seed, b"")):
        second = resource_type(other_config, "second")
    first._set_inspect_result(
        first._parse_inspect_result({"Name": "first", "Metadata": {"version": "old"}})
    )
    before = [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in [first, second]
    ]
    responses = {
        "cli": CompletedProcess([], 1, b"[]", b"failure"),
        "shape": CompletedProcess([], 0, b"{}", b""),
        "count": CompletedProcess([], 0, b"[]", b""),
        "model": CompletedProcess([], 0, b'[{"Id": [], "ID": [], "Name": []}]', b""),
        "identity": CompletedProcess(
            [], 0, b'[{"Id": "different", "ID": "different", "Name": "different"}]', b""
        ),
    }
    with (
        patch(
            "subprocess.run",
            side_effect=[
                CompletedProcess(
                    [], 0, b'[{"Name": "first", "Metadata": {"version": "new"}}]', b""
                ),
                responses[failure],
            ],
        ) as command,
        patch.object(
            first, "_set_inspect_result", wraps=first._set_inspect_result
        ) as publish_first,
        patch.object(
            second, "_set_inspect_result", wraps=second._set_inspect_result
        ) as publish_second,
        pytest.raises(DockerException if failure == "cli" else ParsingError),
    ):
        bulk_reload([first, second])
    assert command.call_count == 2
    publish_first.assert_not_called()
    publish_second.assert_not_called()
    for resource, (snapshot, timestamp) in zip([first, second], before, strict=True):
        assert resource._inspect_result is snapshot
        assert resource._last_refreshed_time == timestamp


def test_bulk_reload_publishes_once_without_freshness_accessors() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    first = Context(config, "same", is_immutable_id=True)
    other_handle = Context(config, "same", is_immutable_id=True)
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess(
                [], 0, b'[{"Name": "same"}, {"Name": "same"}]', b""
            ),
        ) as command,
        patch.object(
            first, "_set_inspect_result", wraps=first._set_inspect_result
        ) as publish_first,
        patch.object(
            other_handle, "_set_inspect_result", wraps=other_handle._set_inspect_result
        ) as publish_other,
        patch.object(
            ReloadableObjectFromJson,
            "_get_inspect_result",
            side_effect=AssertionError("freshness accessor"),
        ),
    ):
        bulk_reload([first, first, other_handle])
    publish_first.assert_called_once()
    publish_other.assert_called_once()
    command.assert_called_once()
    assert first._inspect_result == other_handle._inspect_result


@pytest.mark.parametrize(
    "payload",
    [
        '[{"Name": "same"}, {"Name": "other"}]',
        '[{"Name": "same"}, {"Name": "same", "Metadata": {"changed": true}}]',
    ],
)
def test_bulk_reload_rejects_conflicting_duplicate_snapshots(payload: str) -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    resources = [Context(config, "same", is_immutable_id=True) for _ in range(2)]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, payload.encode(), b""),
        ),
        pytest.raises(ParsingError),
    ):
        bulk_reload(resources)
    assert all(resource._inspect_result is None for resource in resources)
    assert all(resource._last_refreshed_time == datetime.min for resource in resources)


@pytest.mark.parametrize(
    "resource_type",
    [Container, Image, Network, Node, Service, Config, Secret, Task, Plugin],
)
def test_bulk_reload_rejects_prefix_for_immutable_identity(
    resource_type: type[SupportedResource],
) -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    resource = resource_type(config, "abc", is_immutable_id=True)
    payload = json.dumps([{"Id": "abcdef", "ID": "abcdef", "Name": "abc"}]).encode()
    with (
        patch("subprocess.run", return_value=CompletedProcess([], 0, payload, b"")),
        pytest.raises(ParsingError),
    ):
        bulk_reload([resource])
    assert resource._inspect_result is None
    assert resource._last_refreshed_time == datetime.min


def test_bulk_reload_empty_input_avoids_lookup() -> None:
    with patch("shutil.which") as lookup, patch("subprocess.run") as command:
        bulk_reload([])
    lookup.assert_not_called()
    command.assert_not_called()


@pytest.mark.parametrize("identity", [None, ""])
def test_bulk_reload_missing_identity_rejects_all_input_before_lookup(
    identity: str | None,
) -> None:
    config = ClientConfig()
    resources = [
        Context(config, "valid", is_immutable_id=True),
        Context(config, identity, is_immutable_id=True),
    ]
    with (
        patch("shutil.which") as lookup,
        patch("subprocess.run") as command,
        pytest.raises(ParsingError),
    ):
        bulk_reload(resources)
    lookup.assert_not_called()
    command.assert_not_called()


class UnsupportedResource(ReloadableObjectFromJson[object]):
    pass


def test_bulk_reload_unsupported_kind_rejects_all_input_before_lookup() -> None:
    config = ClientConfig()
    resource = UnsupportedResource(config, "id", "unsupported", is_immutable_id=True)
    with (
        patch("shutil.which") as lookup,
        patch("subprocess.run") as command,
        pytest.raises(TypeError),
    ):
        bulk_reload([Context(config, "valid", is_immutable_id=True), resource])
    lookup.assert_not_called()
    command.assert_not_called()
