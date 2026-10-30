import json
from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from pywhales._inventory_dispatch import make_resource
from pywhales._inventory_staging import stage_resources
from pywhales.client_config import ClientConfig, ParsingError, ReloadableObjectFromJson
from pywhales.components.buildx.models import BuilderInspectResult
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
    "builder",
)
LISTING_ARGV = ["mock-docker", "buildx", "ls", "--format", "{{json . }}"]
POLICY = InspectionPolicy(
    max_references=2, max_argv_bytes=10000, max_response_bytes=10000
)


def request(
    kind: InventoryKind, config: ClientConfig, *references: str
) -> InventoryRequest:
    return InventoryRequest(kind=kind, references=references, client_config=config)


def builder_line(name: str | None, **fields: object) -> str:
    return json.dumps({"Name": name, "Driver": "docker-container", **fields})


def listing(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("kind", KINDS)
def test_capture_each_kind_uses_plural_inspector(kind: InventoryKind) -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    handle = make_resource(kind, config, "identity")
    if kind == "builder":
        module, result = "buildx", listing(builder_line("identity"))
    else:
        module = kind
        result = json.dumps([{"Id": "identity", "ID": "identity", "Name": "identity"}])
    with (
        patch(
            f"pywhales.components.{module}.cli_wrapper.run", return_value=result
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
    if kind == "builder":
        assert run.call_args.args[0] == LISTING_ARGV
        return
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


def run_builders(output: str):  # noqa: ANN201
    return patch("pywhales.components.buildx.cli_wrapper.run", return_value=output)


def test_builder_capture_lists_once_per_config_and_keeps_order_and_duplicates() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    equal_config = ClientConfig(_client_call_with_path=["mock-docker"])
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )
    output = listing(builder_line("a"), builder_line("b"), builder_line("c"))
    with run_builders(output) as run:
        snapshot = capture_inventory(
            [
                request("builder", config, "b", "a"),
                request("builder", equal_config, "c"),
                request("builder", config, "b"),
            ],
            policy=policy,
        )
    assert [record.id for record in snapshot.records] == ["b", "a", "c", "b"]
    assert [call.args[0] for call in run.call_args_list] == [LISTING_ARGV] * 2
    assert snapshot.records[0].payload is not snapshot.records[3].payload
    assert snapshot.records[0].payload == snapshot.records[3].payload


def test_builder_names_never_reach_argv_or_argv_limit() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    name = "n" * 2000
    handle = make_resource("builder", config, name)
    policy = InspectionPolicy(
        max_references=5, max_argv_bytes=200, max_response_bytes=10000
    )
    with run_builders(listing(builder_line(name))) as run:
        snapshot = capture_inventory([handle], policy=policy)
    assert snapshot.records[0].id == name
    assert all(name not in part for part in run.call_args.args[0])


def test_builder_listing_accepts_real_buildx_field_shapes() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    line = builder_line(
        "default",
        LastActivity="0001-01-01T00:00:00Z",
        Dynamic=False,
        Nodes=[
            {
                "Name": "default",
                "Endpoint": "default",
                "Flags": None,
                "Status": "running",
                "Version": "v0.12.1",
                "IDs": ["abc"],
                "Platforms": ["linux/amd64"],
                "Labels": {"k": "v"},
            }
        ],
    )
    with run_builders(listing(line)):
        snapshot = capture_inventory(
            [request("builder", config, "default")], policy=POLICY
        )
    payload = snapshot.records[0].payload
    assert isinstance(payload, BuilderInspectResult)
    assert payload.dynamic is False
    assert payload.nodes is not None and payload.nodes[0].ids == ["abc"]


def test_builder_repeated_byte_identical_lines_collapse() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    line = builder_line("same", Nodes=[{"Name": "same0"}])
    with run_builders(listing(line, line, builder_line("other"), line)):
        snapshot = capture_inventory(
            [request("builder", config, "same", "same")], policy=POLICY
        )
    assert [record.id for record in snapshot.records] == ["same", "same"]


@pytest.mark.parametrize(
    "output",
    [
        listing(builder_line("same"), builder_line("same", Dynamic=True)),
        listing(builder_line("same"), builder_line("same", Driver="other")),
        listing(builder_line("same"), '{"Driver": "docker"}'),
        listing(builder_line("same"), builder_line(None)),
        listing(builder_line("same"), builder_line("")),
        listing(builder_line("same"), "{not json"),
        listing(builder_line("same"), "[]"),
        listing(builder_line("same"), ""),
        listing(builder_line("same"), '{"Name": ["same"]}'),
        listing(builder_line("same", Dynamic="true")),
        listing(builder_line("same", Dynamic=1)),
        listing(builder_line("same", LastActivity=5)),
        listing(builder_line("same", Nodes={"Name": "same0"})),
        listing(builder_line("same", Nodes=[{"Name": "same0", "Flags": "--x"}])),
        listing(builder_line("same", Nodes=[{"Name": "same0", "Labels": {"k": 1}}])),
        listing(builder_line("other")),
        listing(builder_line("Same")),
        listing(builder_line("same-longer")),
        "",
    ],
)
def test_builder_listing_failure_fails_whole_capture_and_preserves_handles(
    output: str,
) -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    handle = make_resource("builder", config, "same")
    with run_builders(output), pytest.raises(ParsingError):
        capture_inventory([handle, request("builder", config, "same")], policy=POLICY)
    assert handle._inspect_result is None
    assert handle._last_refreshed_time == datetime.min


@pytest.mark.parametrize(
    "output", [listing("{not json"), listing(builder_line(""), builder_line("x"))]
)
@pytest.mark.parametrize("raw", [False, True])
def test_builder_listing_errors_are_safe_unless_raw_is_opted_in(
    output: str, raw: bool
) -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"], raw_diagnostics=raw)
    with run_builders(output), pytest.raises(ParsingError) as error:
        capture_inventory([request("builder", config, "secret-builder")], policy=POLICY)
    assert "secret-builder" not in str(error.value)
    if not raw:
        assert error.value.__cause__ is None
        assert error.value.__context__ is None or error.value.__suppress_context__
    elif "not json" in output:
        assert error.value.__cause__ is not None


@pytest.mark.parametrize("raw", [False, True])
def test_builder_missing_name_hides_name_unless_raw(raw: bool) -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"], raw_diagnostics=raw)
    with run_builders(listing(builder_line("other"))), pytest.raises(ParsingError) as e:
        capture_inventory([request("builder", config, "secret-builder")], policy=POLICY)
    assert ("secret-builder" in str(e.value)) is raw


def test_builder_conflict_hides_name_unless_raw() -> None:
    output = listing(
        builder_line("secret-builder"), builder_line("secret-builder", A=1)
    )
    safe = ClientConfig(_client_call_with_path=["mock-docker"])
    raw = ClientConfig(_client_call_with_path=["mock-docker"], raw_diagnostics=True)
    with run_builders(output):
        with pytest.raises(ParsingError) as safe_error:
            capture_inventory(
                [request("builder", safe, "secret-builder")], policy=POLICY
            )
        with pytest.raises(ParsingError) as raw_error:
            capture_inventory(
                [request("builder", raw, "secret-builder")], policy=POLICY
            )
    assert "secret-builder" not in str(safe_error.value)
    assert "secret-builder" in str(raw_error.value)


def test_builder_failure_after_earlier_kind_preserves_every_handle() -> None:
    config = ClientConfig(_client_call_with_path=["mock-docker"])
    context = Context(config, "first", is_immutable_id=True)
    builder = make_resource("builder", config, "gone")
    with (
        patch(
            "pywhales.components.context.cli_wrapper.run",
            return_value='[{"Name":"first"}]',
        ),
        run_builders(listing(builder_line("other"))),
        pytest.raises(ParsingError),
    ):
        capture_inventory([context, builder], policy=POLICY)
    assert context._inspect_result is None and builder._inspect_result is None
