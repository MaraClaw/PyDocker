import json
from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from pywhales._inventory_dispatch import make_resource
from pywhales._inventory_staging import stage_resources
from pywhales.client_config import ClientConfig, ParsingError, ReloadableObjectFromJson
from pywhales.components.context.cli_wrapper import Context
from pywhales.components.context.models import ContextInspectResult
from pywhales.exceptions import DockerException, ObservationLimitError
from pywhales.inventory import (
    InspectionPolicy,
    InventoryKind,
    InventoryRequest,
    capture_inventory,
)

KINDS: tuple[InventoryKind, ...] = (
    "container",
    "image",
    "network",
    "volume",
    "node",
    "service",
    "config",
    "secret",
    "task",
    "plugin",
    "context",
)
POLICY = InspectionPolicy(
    max_references=2, max_argv_bytes=10000, max_response_bytes=10000
)


def request(
    kind: InventoryKind, config: ClientConfig, *references: str
) -> InventoryRequest:
    return InventoryRequest(kind=kind, references=references, client_config=config)


@pytest.mark.parametrize("kind", KINDS)
def test_capture_each_kind_uses_plural_inspector(kind: InventoryKind) -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    handle = make_resource(kind, config, "identity")
    result = json.dumps([{"Id": "identity", "ID": "identity", "Name": "identity"}])
    with (
        patch(
            f"pywhales.components.{kind}.cli_wrapper.run", return_value=result
        ) as run,
        patch.object(
            ReloadableObjectFromJson, "_get_inspect_result", side_effect=AssertionError
        ),
    ):
        snapshot = capture_inventory([handle], policy=POLICY)
    assert snapshot.records[0].kind == kind
    assert snapshot.records[0].id == "identity"
    assert handle._inspect_result is None
    assert handle._last_refreshed_time == datetime.min
    assert (
        snapshot.started_at.tzinfo == UTC
        and snapshot.started_at <= snapshot.finished_at
    )
    suffix = ["inspect", "--type", "task"] if kind == "task" else [kind, "inspect"]
    assert run.call_args.args[0] == ["mock-docker", *suffix, "identity"]


def test_chunks_restore_order_duplicates_and_config_identity() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    equal_config = ClientConfig(_client_call_with_path=["mock-docker"])
    calls: list[list[str]] = []

    def run(args: Sequence[str]) -> str:
        references = list(args[3:])
        calls.append(references)
        return json.dumps([{"Name": reference} for reference in references])

    with patch("pywhales.components.context.cli_wrapper.run", side_effect=run):
        snapshot = capture_inventory(
            [
                request("context", config, "a", "b"),
                request("context", equal_config, "c"),
                request("context", config, "a", "d"),
            ],
            policy=POLICY,
        )
    assert [record.id for record in snapshot.records] == ["a", "b", "c", "a", "d"]
    assert calls == [["a", "b"], ["a", "d"], ["c"]]
    assert snapshot.records[0].payload is not snapshot.records[3].payload


def test_staging_and_records_are_detached_in_both_directions() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    original = Context(config, "same", is_immutable_id=True)
    original._set_inspect_result(
        ContextInspectResult(name="same", metadata={"value": 0})
    )
    before = original._last_refreshed_time
    response = json.dumps([{"Name": "same", "Metadata": {"value": 1}}] * 2)
    with patch("pywhales.components.context.cli_wrapper.run", return_value=response):
        staged, sibling = stage_resources([original, original], POLICY)
        snapshot = capture_inventory([original, original], policy=POLICY)
    assert staged is not original and staged.client_config is config
    assert original._last_refreshed_time == before
    assert isinstance(staged, Context) and isinstance(sibling, Context)
    first = snapshot.records[0].payload
    second = snapshot.records[1].payload
    assert isinstance(first, ContextInspectResult) and isinstance(
        second, ContextInspectResult
    )
    assert (
        original._inspect_result is not None
        and original._inspect_result.metadata is not None
    )
    assert (
        staged._inspect_result is not None
        and staged._inspect_result.metadata is not None
    )
    assert (
        sibling._inspect_result is not None
        and sibling._inspect_result.metadata is not None
    )
    assert first.metadata is not None and second.metadata is not None
    first.metadata["value"] = 2
    staged._inspect_result.metadata["value"] = 3
    original._inspect_result.metadata["value"] = 4
    assert second.metadata["value"] == 1
    assert sibling._inspect_result.metadata["value"] == 1
    assert first.metadata["value"] == 2
    assert staged._inspect_result.metadata["value"] == 3


def test_global_swarm_ambiguity_is_rejected_across_chunks() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )
    responses = [json.dumps([{"ID": "abc-one"}]), json.dumps([{"ID": "abc-two"}])]
    with (
        patch("pywhales.components.config.cli_wrapper.run", side_effect=responses),
        pytest.raises(ParsingError),
    ):
        capture_inventory([request("config", config, "ab", "abc-two")], policy=policy)


def test_repeated_alias_drift_is_rejected_across_chunks() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )
    responses = [json.dumps([{"Id": "one"}]), json.dumps([{"Id": "two"}])]
    with (
        patch("pywhales.components.plugin.cli_wrapper.run", side_effect=responses),
        pytest.raises(ParsingError),
    ):
        capture_inventory([request("plugin", config, "alias", "alias")], policy=policy)


def test_inconsistent_same_identity_across_chunks_is_rejected() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )
    responses = [
        json.dumps([{"Name": "same", "Metadata": {"version": 1}}]),
        json.dumps([{"Name": "same", "Metadata": {"version": 2}}]),
    ]
    with (
        patch("pywhales.components.context.cli_wrapper.run", side_effect=responses),
        pytest.raises(ParsingError),
    ):
        capture_inventory([request("context", config, "same", "same")], policy=policy)


def test_late_oversized_reference_fails_before_any_inspection() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=60, max_response_bytes=10000
    )
    with (
        patch("pywhales.components.context.cli_wrapper.run") as run,
        pytest.raises(ObservationLimitError),
    ):
        capture_inventory([request("context", config, "a", "x" * 100)], policy=policy)
    run.assert_not_called()


def test_empty_and_invalid_inputs_never_look_up_executable() -> None:
    config = ClientConfig()
    invalid = make_resource("context", config, "")
    with patch("shutil.which") as lookup:
        assert capture_inventory([], policy=POLICY).records == ()
        with pytest.raises(ParsingError):
            capture_inventory(
                [request("context", config, "valid"), invalid], policy=POLICY
            )
    lookup.assert_not_called()


def test_handle_identity_is_exact_even_for_positional_aliases() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    handle = make_resource("plugin", config, "abc")
    with (
        patch(
            "pywhales.components.plugin.cli_wrapper.run",
            return_value='[{"Id":"abcdef"}]',
        ),
        pytest.raises(ParsingError),
    ):
        capture_inventory([handle], policy=POLICY)
    assert handle._inspect_result is None


@pytest.mark.parametrize("response", ["{}", "[]", '[{"Name": []}]'])
def test_invalid_batch_response_preserves_original(response: str) -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    handle = make_resource("context", config, "name")
    with (
        patch("pywhales.components.context.cli_wrapper.run", return_value=response),
        pytest.raises(ParsingError),
    ):
        capture_inventory([handle], policy=POLICY)
    assert handle._inspect_result is None
    assert handle._last_refreshed_time == datetime.min


def test_later_cli_failure_preserves_earlier_handle() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    handle = Context(config, "first", is_immutable_id=True)
    error = DockerException(["mock-docker"], 1)
    with (
        patch(
            "pywhales.components.context.cli_wrapper.run",
            side_effect=['[{"Name":"first"}]', error],
        ),
        pytest.raises(DockerException),
    ):
        capture_inventory(
            [handle, request("context", config, "second")],
            policy=InspectionPolicy(
                max_references=1, max_argv_bytes=10000, max_response_bytes=10000
            ),
        )
    assert handle._inspect_result is None
    assert handle._last_refreshed_time == datetime.min
