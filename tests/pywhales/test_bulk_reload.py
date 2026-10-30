import json
from datetime import datetime
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import (
    Builder,
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
    ReloadableObject,
    ReloadableObjectFromJson,
    bulk_reload,
)
from pywhales.exceptions import DockerException

RESOURCE_TYPES = [
    Builder,
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
    Builder
    | Container
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
    output = json.dumps([snapshot]).encode()
    expected = [*prefix, *suffix, "identity"]
    if resource_type is Builder:
        output = json.dumps(snapshot).encode() + b"\n"
        expected = [*prefix, "buildx", "ls", "--format", "{{json . }}"]
    with patch(
        "subprocess.run", return_value=CompletedProcess([], 0, output, b"")
    ) as command:
        bulk_reload([resource])
    assert resource._inspect_result is not None
    assert resource._last_refreshed_time > datetime.min
    command.assert_called_once()
    assert command.call_args.args[0] == expected


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
@pytest.mark.parametrize(
    "resource_type", [kind for kind in RESOURCE_TYPES if kind is not Builder]
)
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


@pytest.mark.parametrize("valid_type", [Context, Builder])
def test_bulk_reload_unsupported_kind_rejects_all_input_before_lookup(
    valid_type: type[Context | Builder],
) -> None:
    config = ClientConfig()
    resource = UnsupportedResource(config, "id", "unsupported", is_immutable_id=True)
    with (
        patch("shutil.which") as lookup,
        patch("subprocess.run") as command,
        pytest.raises(TypeError),
    ):
        bulk_reload([valid_type(config, "valid", is_immutable_id=True), resource])
    lookup.assert_not_called()
    command.assert_not_called()


def _builder_line(name: str | None, **fields: object) -> bytes:
    return json.dumps({"Name": name, "Driver": "docker", **fields}).encode() + b"\n"


def test_bulk_reload_builders_publish_once_with_one_listing_per_config() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    other_config = ClientConfig(_client_call_with_path=["docker-test"])
    first = Builder(config, "same", is_immutable_id=True)
    sibling = Builder(config, "same", is_immutable_id=True)
    other = Builder(other_config, "other", is_immutable_id=True)
    node_rows = _builder_line("same", Nodes=[{"Name": "same0"}]) * 2
    with (
        patch(
            "subprocess.run",
            side_effect=[
                CompletedProcess([], 0, node_rows + _builder_line("extra"), b""),
                CompletedProcess([], 0, _builder_line("other"), b""),
            ],
        ) as command,
        patch.object(
            first, "_set_inspect_result", wraps=first._set_inspect_result
        ) as publish_first,
        patch.object(
            sibling, "_set_inspect_result", wraps=sibling._set_inspect_result
        ) as publish_sibling,
        patch.object(
            ReloadableObject,
            "_get_inspect_result",
            side_effect=AssertionError("freshness accessor"),
        ),
    ):
        bulk_reload([first, other, first, sibling])
    assert command.call_count == 2
    assert [call.args[0][1:] for call in command.call_args_list] == [
        ["buildx", "ls", "--format", "{{json . }}"]
    ] * 2
    publish_first.assert_called_once()
    publish_sibling.assert_called_once()
    assert first._inspect_result == sibling._inspect_result
    assert first._inspect_result is not sibling._inspect_result
    assert first._inspect_result is not None
    assert first._inspect_result.nodes is not None
    assert other._inspect_result is not None


@pytest.mark.parametrize(
    "listing",
    [
        _builder_line("other"),
        _builder_line("same") + _builder_line("same", Dynamic=True),
        _builder_line("same") + _builder_line(None),
        _builder_line("same") + b"{not json\n",
        b"",
    ],
)
@pytest.mark.parametrize("builder_first", [True, False])
def test_bulk_reload_builder_failure_preserves_mixed_kind_caches(
    listing: bytes, builder_first: bool
) -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    other_config = ClientConfig(_client_call_with_path=["docker-test"])
    builder = Builder(config, "same", is_immutable_id=True)
    context = Context(other_config, "ctx", is_immutable_id=True)
    context._set_inspect_result(context._parse_inspect_result({"Name": "ctx"}))
    before = [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in (builder, context)
    ]
    ok = CompletedProcess([], 0, b'[{"Name": "ctx", "Metadata": {"v": 1}}]', b"")
    bad = CompletedProcess([], 0, listing, b"")
    resources = [builder, context] if builder_first else [context, builder]
    responses = [bad, ok] if builder_first else [ok, bad]
    with (
        patch("subprocess.run", side_effect=responses),
        pytest.raises(ParsingError),
    ):
        bulk_reload(resources)
    assert [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in (builder, context)
    ] == before


@pytest.mark.parametrize("raw", [False, True])
def test_bulk_reload_builder_errors_follow_diagnostic_policy(raw: bool) -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"], raw_diagnostics=raw)
    builder = Builder(config, "secret-builder", is_immutable_id=True)
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, _builder_line("other"), b""),
        ),
        pytest.raises(ParsingError) as error,
    ):
        bulk_reload([builder])
    assert ("secret-builder" in str(error.value)) is raw
