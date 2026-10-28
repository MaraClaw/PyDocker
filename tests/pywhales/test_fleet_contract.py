"""Offline manifest validation and runtime invariants, never Docker contact."""

import json
import socket
import subprocess
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from scripts.fleet_contract.common import Pending
from scripts.fleet_contract.manifest import Manifest
from scripts.fleet_contract.platform import Platform
from scripts.fleet_contract.safety import Budgets
from scripts.fleet_contract.support import SupportMatrix
from scripts.fleet_contract.workload import Workload

FIXTURES: Final = (
    Path(__file__).resolve().parents[2] / "scripts/fleet_contract/fixtures"
)
JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])


def changed(path: str, value: JsonValue) -> str:
    """Change one JSON input field without bypassing the validation boundary."""
    data = JSON_OBJECT.validate_json((FIXTURES / "synthetic-runnable.json").read_text())
    current: JsonValue = data
    parts = path.split(".")
    for part in parts[:-1]:
        match current:
            case dict():
                current = current[part]
            case list():
                current = current[int(part)]
            case _:
                raise AssertionError(path)
    assert isinstance(current, dict)
    current[parts[-1]] = value
    return json.dumps(data)


def test_complete_manifest_roundtrip_when_offline() -> None:
    raw = (FIXTURES / "synthetic-runnable.json").read_text()
    with (
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("process contact")
        ),
        patch.object(subprocess, "run", side_effect=AssertionError("process contact")),
        patch.object(socket, "socket", side_effect=AssertionError("socket contact")),
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS contact")),
    ):
        manifest = Manifest.model_validate_json(raw)
        roundtrip = Manifest.model_validate_json(manifest.model_dump_json())

    assert roundtrip == manifest
    assert isinstance(manifest.workload, Workload)
    assert (
        manifest.workload.resident_containers,
        manifest.workload.service_replicas,
        manifest.workload.inflight_operations,
    ) == (2, 0, 2)
    assert isinstance(manifest.platform, Platform)
    assert {alias.effective_endpoint for alias in manifest.platform.aliases} == {
        "example-daemon"
    }
    assert manifest.workload.output_bytes.unit == "bytes"
    assert manifest.workload.images[0].digest == "sha256:" + "c" * 64


def test_draft_blockers_when_real_operator_inputs_are_unresolved() -> None:
    raw = (FIXTURES / "draft.json").read_text()

    draft = Manifest.model_validate_json(raw)

    assert isinstance(draft.budgets, Pending)
    assert {decision.field for decision in draft.decisions} == {
        "support_row",
        "workload",
        "platform",
        "topology",
        "ownership",
        "measurements",
        "budgets",
        "authorization",
    }
    assert all(
        decision.owner and decision.rationale and decision.blocks
        for decision in draft.decisions
    )
    assert Manifest.model_validate_json(draft.model_dump_json()) == draft


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("workload.inflight_operations", True),
        ("workload.inflight_operations", "2"),
        ("workload.inflight_operations", 0),
        ("workload.concurrent_submissions", 3),
        ("workload.service_replicas", 1),
        ("workload.endpoint_aliases", ["not-in-inventory"]),
        (
            "workload.operation_mix",
            [{"action": "remove", "fraction": 1.0, "concurrency": 1}],
        ),
        ("workload.arrivals.minimum", 5.0),
        ("workload.output_bytes.unit", "seconds"),
        ("workload.images.0.digest", "mutable:latest"),
        ("workload.duration_seconds", float("nan")),
        ("workload.duration_seconds", float("inf")),
        ("workload.duration_seconds", -1.0),
        ("workload.termination_operations", 1),
        ("platform.mode", "stack"),
        ("platform.node_count", 2),
        ("platform.aliases.0.effective_endpoint", "unknown"),
        ("topology.network", "overlay"),
        ("topology.ipv6", True),
        ("topology.ipv4_pools", ["192.0.2.0/24", "192.0.2.0/25"]),
        ("ownership.assets.0.ownership", "borrowed"),
        ("budgets", {"decision_id": "budgets"}),
        ("budgets.maximum_error_fraction", 1.1),
        ("budgets.polling_interval_seconds", 100.0),
        ("budgets.sustained_duration_seconds", 31.0),
        ("budgets.resources.0.early_stop", 3.0),
        ("measurements.latency.0.population", "launched_cli"),
        ("authorization", None),
        ("authorization.disposable", False),
        ("authorization.disposable", 1),
        ("authorization.effective_endpoint_allowlist", ["unknown"]),
        ("authorization.allowed_actions", ["inspect"]),
        ("authorization.maximum_run_seconds", 1.0),
        ("authorization.expires_after_seconds", 1.0),
        ("authorization.run_namespace", "secret://endpoint"),
        ("identity.unknown-secret-key", "private-credential-sentinel"),
    ],
)
def test_rejection_before_contact_when_manifest_is_unsafe(
    path: str, value: JsonValue
) -> None:
    raw = changed(path, value)
    with (
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("process contact")
        ),
        patch.object(subprocess, "run", side_effect=AssertionError("process contact")),
        patch.object(socket, "socket", side_effect=AssertionError("socket contact")),
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS contact")),
        pytest.raises(ValidationError),
    ):
        Manifest.model_validate_json(raw)


@pytest.mark.parametrize("field", ["authorization", "budgets", "topology", "workload"])
def test_missing_required_group_when_runnable(field: str) -> None:
    data = JSON_OBJECT.validate_json((FIXTURES / "synthetic-runnable.json").read_text())
    del data[field]

    with pytest.raises(ValidationError):
        Manifest.model_validate_json(json.dumps(data))


def test_runnable_rejected_when_draft_decisions_are_preserved() -> None:
    data = JSON_OBJECT.validate_json((FIXTURES / "draft.json").read_text())
    data["state"] = "runnable"

    with pytest.raises(ValidationError):
        Manifest.model_validate_json(json.dumps(data))


def test_pending_reference_rejected_when_decision_owner_is_missing() -> None:
    data = JSON_OBJECT.validate_json((FIXTURES / "draft.json").read_text())
    data["decisions"] = []

    with pytest.raises(ValidationError):
        Manifest.model_validate_json(json.dumps(data))


def test_matrix_when_ci_is_only_configured() -> None:
    matrix = SupportMatrix.model_validate_json(
        (FIXTURES / "support-matrix.json").read_text()
    )

    assert {row.status for row in matrix.rows} == {"untested"}
    assert {decision.blocks for decision in matrix.decisions} == {"qualification"}
    assert isinstance(matrix.legacy06.compatibility_window, str)
    assert isinstance(matrix.legacy06.removal_decision, str)
    assert isinstance(matrix.rows[0].versions.docker_engine, Pending)
    assert matrix.rows[0].versions.compose == "5.6.1"


@pytest.mark.parametrize("status", ["supported", "experimental"])
def test_support_rejected_when_configured_ci_is_promoted_without_review(
    status: str,
) -> None:
    data = JSON_OBJECT.validate_json((FIXTURES / "support-matrix.json").read_text())
    rows = data["rows"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    rows[0]["status"] = status

    with pytest.raises(ValidationError):
        SupportMatrix.model_validate_json(json.dumps(data))


def test_budget_scope_when_contract_is_parsed() -> None:
    manifest = Manifest.model_validate_json(
        (FIXTURES / "synthetic-runnable.json").read_text()
    )

    assert isinstance(manifest.budgets, Budgets)
    assert {budget.boundary for budget in manifest.budgets.latency} == {
        "queue_wait",
        "selected_cli",
        "readiness",
        "recovery",
    }


def test_runnable_budget_when_unresolved_marker_has_an_owned_decision() -> None:
    data = JSON_OBJECT.validate_json((FIXTURES / "synthetic-runnable.json").read_text())
    data["budgets"] = {"decision_id": "operator-budget"}
    data["decisions"] = [
        {
            "id": "operator-budget",
            "field": "budgets",
            "owner": "operator",
            "rationale": "Real limits have not been supplied",
            "blocks": "live_run",
        }
    ]

    with pytest.raises(ValidationError) as error:
        Manifest.model_validate_json(json.dumps(data))

    assert error.value.errors(include_input=False)[0]["type"] == "runnable_unresolved"
