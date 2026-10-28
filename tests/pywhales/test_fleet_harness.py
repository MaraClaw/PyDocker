"""Offline execution, declaration gates, exact ownership and stop behavior."""

import json
import socket
import subprocess
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from scripts.fleet_harness.fake import FakeDriver
from scripts.fleet_harness.models import Plan, RunnableManifest
from scripts.fleet_harness.runner import run

FIXTURES: Final = Path(__file__).resolve().parents[2] / "scripts/fleet_harness/fixtures"


def manifest() -> RunnableManifest:
    return RunnableManifest.model_validate_json(
        (FIXTURES / "manifest.json").read_text()
    )


def plan() -> Plan:
    return Plan.model_validate_json((FIXTURES / "plan.json").read_text())


def test_authorized_simulation_when_external_contact_is_denied() -> None:
    with (
        patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
        patch.object(socket, "socket", side_effect=AssertionError("socket")),
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("dns")),
    ):
        report = run(manifest(), plan())

    assert report.status == "completed" and report.submitted == 3
    assert report.command_count == 3 and report.connections == 0
    assert report.cleanup.removed == report.ledger and not report.cleanup.residual
    assert report.ledger[0].endpoint == "example-daemon"
    assert report.ledger[0].namespace == "synthetic-offline-run"
    assert report.ledger[0].marker == "synthetic-owner-marker"
    assert len(report.samples) == 10
    assert report.simulation and report.qualification == "unqualified"
    assert report.environment.python and report.environment.library
    assert report.environment.docker_engine is None
    assert report.environment.platform.endpoints[0].connection_ref == "withheld"
    assert {metric.name for metric in report.metrics if metric.value is None} == {
        "network_bytes",
        "daemon_cpu_cores",
        "daemon_memory_bytes",
    }


@pytest.mark.parametrize("change", ["opt_in", "provider", "endpoint", "fault", "asset"])
def test_rejection_when_authority_is_missing_precedes_driver_creation(
    change: str,
) -> None:
    recipe = plan()
    match change:
        case "opt_in":
            recipe = recipe.model_copy(update={"opt_in": False})
        case "provider":
            recipe = recipe.model_copy(update={"provider": "real"})
        case "endpoint":
            recipe = recipe.model_copy(
                update={
                    "steps": (
                        recipe.steps[0].model_copy(update={"endpoint": "not-allowed"}),
                    )
                }
            )
        case "fault":
            recipe = recipe.model_copy(
                update={
                    "steps": (
                        recipe.steps[2].model_copy(
                            update={"fault": "unauthorized-fault"}
                        ),
                    )
                }
            )
        case "asset":
            recipe = recipe.model_copy(
                update={
                    "steps": (
                        recipe.steps[0].model_copy(
                            update={"asset": "example-rollback"}
                        ),
                    )
                }
            )
    with patch(
        "scripts.fleet_harness.runner.FakeDriver",
        side_effect=AssertionError("preflight called adapter"),
    ):
        report = run(manifest(), recipe)

    assert report.status == "rejected" and report.reasons
    assert report.provider_calls == report.command_count == report.submitted == 0
    assert report.cleanup.attempted == report.ledger == ()


@pytest.mark.parametrize(
    "outcome", ["failed", "timed_out", "unknown", "cancelled", "rejected"]
)
def test_failed_samples_when_load_stops_are_not_dropped(outcome: str) -> None:
    raw = json.loads((FIXTURES / "plan.json").read_text())
    raw["steps"][0]["outcome"] = outcome
    recipe = Plan.model_validate_json(json.dumps(raw))

    report = run(manifest(), recipe)

    assert report.status == "stopped" and report.submitted == 1
    assert {sample.outcome for sample in report.samples} == {outcome}
    assert report.distributions[1].values_seconds == (0.1,)
    assert report.cleanup.removed == report.ledger


def test_deadline_when_creation_response_is_lost_retains_owned_identity() -> None:
    recipe = plan()
    recipe = recipe.model_copy(
        update={"steps": (recipe.steps[0].model_copy(update={"local_seconds": 100.0}),)}
    )

    report = run(manifest(), recipe)

    assert report.submitted == 1 and report.command_count == 1
    assert {sample.outcome for sample in report.samples} == {"timed_out"}
    assert report.distributions[1].values_seconds == (4.0,)
    assert report.elapsed_seconds == pytest.approx(4.1)
    assert report.cleanup.removed == report.ledger


def test_queue_timeout_when_never_launched_has_no_local_population() -> None:
    recipe = plan()
    recipe = recipe.model_copy(
        update={"steps": (recipe.steps[0].model_copy(update={"queue_seconds": 100.0}),)}
    )

    report = run(manifest(), recipe)

    assert report.command_count == 0 and len(report.samples) == 1
    assert report.samples[0].boundary == "queue_wait"
    assert report.samples[0].outcome == "timed_out"
    assert report.ledger == ()


@pytest.mark.parametrize("timeout", [False, True])
def test_cleanup_failure_when_stop_occurs_reports_exact_residual(timeout: bool) -> None:
    recipe = plan().model_copy(
        update={"cleanup_seconds_per_asset": 100.0}
        if timeout
        else {"cleanup_fail_assets": ("example-container",)}
    )

    report = run(manifest(), recipe)

    assert report.cleanup.residual == report.ledger
    assert report.cleanup.attempted == report.ledger
    assert report.cleanup.removed == ()
    assert report.cleanup.timed_out is timeout
    assert report.cleanup.elapsed_seconds <= manifest().budgets.cleanup_timeout_seconds
    assert "cleanup_residual" in report.reasons
    assert all(item.slot != "example-rollback" for item in report.cleanup.attempted)


@pytest.mark.parametrize("missing", [False, True])
def test_resource_stop_when_threshold_or_collection_failure_occurs(
    missing: bool,
) -> None:
    recipe = plan()
    metrics = tuple(
        metric.model_copy(update={"value": None if missing else 2.0})
        if metric.name == "cpu_cores"
        else metric
        for metric in recipe.metrics
    )

    report = run(manifest(), recipe.model_copy(update={"metrics": metrics}))

    assert report.submitted == 1
    assert (
        "resource_metrics_unavailable" if missing else "resource_limit"
    ) in report.reasons
    assert report.cleanup.removed == report.ledger


def test_asset_cap_when_repetitions_create_more_than_authorized() -> None:
    recipe = plan()
    recipe = recipe.model_copy(update={"steps": (recipe.steps[0],), "repetitions": 3})

    report = run(manifest(), recipe)

    assert report.submitted == 2 and "asset_limit" in report.reasons
    assert len({item.identity for item in report.ledger}) == 2
    assert report.cleanup.attempted == tuple(reversed(report.ledger))


@pytest.mark.parametrize("field", ["authorization", "budgets", "ownership", "topology"])
def test_manifest_rejects_missing_safety_groups(field: str) -> None:
    raw = json.loads((FIXTURES / "manifest.json").read_text())
    del raw[field]

    with pytest.raises(ValidationError):
        RunnableManifest.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 0.0, -1.0, True])
def test_plan_rejects_nonfinite_or_invalid_deadline(value: float | bool) -> None:
    raw = json.loads((FIXTURES / "plan.json").read_text())
    raw["operation_timeout_seconds"] = value

    with pytest.raises(ValidationError):
        Plan.model_validate_json(json.dumps(raw))


def test_unavailable_readiness_when_collection_is_omitted_is_not_zero() -> None:
    recipe = plan()
    recipe = recipe.model_copy(
        update={
            "steps": (recipe.steps[0].model_copy(update={"readiness_seconds": None}),)
        }
    )

    report = run(manifest(), recipe)

    assert report.distributions[2].recorded == report.distributions[2].unavailable == 1
    assert report.distributions[2].values_seconds == ()
    assert report.distributions[2].p95_seconds is None


def test_fake_clock_bounds_when_cleanup_has_multiple_owned_resources() -> None:
    driver = FakeDriver()

    elapsed = driver.advance(100.0, 2.0)

    assert elapsed == driver.now == 2.0
