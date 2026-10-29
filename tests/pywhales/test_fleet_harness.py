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
from scripts.fleet_harness.records import RunReport
from scripts.fleet_harness.runner import run

FIXTURES: Final = Path(__file__).resolve().parents[2] / "scripts/fleet_harness/fixtures"


def manifest() -> RunnableManifest:
    return RunnableManifest.model_validate_json(
        (FIXTURES / "manifest.json").read_text()
    )


def plan() -> Plan:
    return Plan.model_validate_json((FIXTURES / "plan.json").read_text())


def duration_manifest() -> RunnableManifest:
    """Return a validated duration variant with the fixture's 20-second bound."""
    raw = json.loads(manifest().model_dump_json())
    raw["workload"].update(termination="duration", termination_operations=None)
    return RunnableManifest.model_validate_json(json.dumps(raw))


def duration_plan(local_seconds: float = 10.0, repetitions: int = 3) -> Plan:
    """Return validated slow inspections whose operation deadline exceeds duration."""
    raw = json.loads(plan().model_dump_json())
    raw["steps"] = [raw["steps"][1]]
    raw["steps"][0].update(local_seconds=local_seconds, readiness_seconds=None)
    raw.update(operation_timeout_seconds=30.0, repetitions=repetitions)
    return Plan.model_validate_json(json.dumps(raw))


def test_authorized_simulation_when_external_contact_is_denied() -> None:
    with (
        patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
        patch.object(socket, "socket", side_effect=AssertionError("socket")),
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("dns")),
    ):
        report = run(manifest(), plan())

    assert report.status == "completed" and report.submitted == 3
    assert report.command_count == 3 and report.connections == 0
    assert (
        report.provider_calls
        == 4
        == report.command_count + len(report.cleanup.attempted)
    )
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
    assert report.provider_calls == 0 and report.cleanup.attempted == ()


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
    assert (
        report.provider_calls
        == 4
        == report.command_count + len(report.cleanup.attempted)
    )
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


@pytest.mark.parametrize("local_seconds", [10.0, 12.0])
@pytest.mark.parametrize("repetitions", [2, 3])
def test_duration_completes_when_final_submission_reaches_boundary(
    local_seconds: float, repetitions: int
) -> None:
    # Given exact/clipped final work and a failure fraction of 1/2 at the boundary.
    recipe = duration_plan(local_seconds, repetitions)

    # When the full declared duration elapses.
    report = run(duration_manifest(), recipe)

    # Then completion retains the timeout without adding a failure/duration reason.
    assert report.status == "completed" and report.reasons == ()
    assert report.load_elapsed_seconds == 20.0
    assert report.submitted == report.command_count == report.provider_calls == 2
    assert report.outcomes.succeeded == report.outcomes.timed_out == 1
    assert report.distributions[1].values_seconds == tuple(
        sorted((local_seconds, 20.0 - local_seconds))
    )
    assert {sample.outcome for sample in report.samples[3:]} == {"timed_out"}


def test_duration_stops_when_plan_exhausts_before_cleanup_reaches_boundary() -> None:
    # Given a creation that ends just before the duration and cleanup that fills it.
    recipe = plan()
    recipe = recipe.model_copy(
        update={
            "operation_timeout_seconds": 30.0,
            "steps": (
                recipe.steps[0].model_copy(
                    update={"local_seconds": 19.9, "readiness_seconds": None}
                ),
            ),
        }
    )

    # When the short plan is run.
    report = run(duration_manifest(), recipe)

    # Then cleanup time cannot make an exhausted load plan complete.
    assert report.status == "stopped" and report.reasons == ("plan_exhausted",)
    assert report.load_elapsed_seconds == pytest.approx(19.9)
    assert report.elapsed_seconds == pytest.approx(20.0)
    assert report.cleanup.removed == report.ledger


@pytest.mark.parametrize("target", [2, 3])
def test_operation_count_stops_when_deadline_precedes_or_matches_target(
    target: int,
) -> None:
    # Given a validated operation target on or beyond the second submission.
    raw = json.loads(manifest().model_dump_json())
    raw["workload"]["termination_operations"] = target
    declaration = RunnableManifest.model_validate_json(json.dumps(raw))

    # When the second submission reaches the duration limit.
    report = run(declaration, duration_plan())

    # Then the deadline wins over both failure fraction and target completion.
    assert report.status == "stopped" and report.reasons == ("duration_limit",)
    assert report.submitted == report.command_count == report.provider_calls == 2
    assert report.load_elapsed_seconds == 20.0 and report.outcomes.timed_out == 1


def test_duration_stops_when_operation_times_out_before_workload_deadline() -> None:
    # Given an operation deadline shorter than the declared workload duration.
    recipe = duration_plan().model_copy(update={"operation_timeout_seconds": 4.0})

    # When the first operation times out before the run deadline.
    report = run(duration_manifest(), recipe)

    # Then the original failure threshold still stops the workload early.
    assert report.status == "stopped" and report.reasons == ("failure_limit",)
    assert report.load_elapsed_seconds == 4.0 and report.outcomes.timed_out == 1


@pytest.mark.parametrize("value", [2.0, None])
def test_duration_keeps_resource_stop_when_submission_reaches_boundary(
    value: float | None,
) -> None:
    # Given a resource threshold violation or unavailable measurement.
    recipe = duration_plan(local_seconds=20.0)
    recipe = recipe.model_copy(
        update={
            "metrics": tuple(
                metric.model_copy(update={"value": value})
                if metric.name == "cpu_cores"
                else metric
                for metric in recipe.metrics
            )
        }
    )

    # When load reaches the declared duration with independent safety evidence.
    report = run(duration_manifest(), recipe)

    # Then duration completion does not erase that safety reason.
    reason = "resource_metrics_unavailable" if value is None else "resource_limit"
    assert report.status == "stopped" and report.reasons == (reason,)
    assert report.load_elapsed_seconds == 20.0


def test_duration_keeps_cleanup_residual_when_load_reaches_boundary() -> None:
    # Given an owned creation clipped at duration and a failing cleanup attempt.
    recipe = plan()
    recipe = recipe.model_copy(
        update={
            "operation_timeout_seconds": 30.0,
            "steps": (recipe.steps[0].model_copy(update={"local_seconds": 30.0}),),
            "cleanup_fail_assets": ("example-container",),
        }
    )

    # When the load completes its duration but cleanup fails.
    report = run(duration_manifest(), recipe)

    # Then only cleanup stops the report, and its failed attempt counts once.
    assert report.status == "stopped" and report.reasons == ("cleanup_residual",)
    assert (
        report.provider_calls
        == 2
        == report.command_count + len(report.cleanup.attempted)
    )
    assert report.cleanup.residual == report.ledger


@pytest.mark.parametrize("status", ["completed", "stopped", "rejected"])
@pytest.mark.parametrize("provider_calls", [3, 5])
def test_report_rejects_when_provider_calls_disagree_with_attempts(
    status: str, provider_calls: int
) -> None:
    # Given internally consistent populations and status but under/overcounted calls.
    raw = json.loads(run(manifest(), plan()).model_dump_json())
    raw.update(
        status=status,
        reasons=[] if status == "completed" else ["test_stop"],
        provider_calls=provider_calls,
    )

    # When a report crosses the validation boundary.
    with pytest.raises(ValidationError) as caught:
        RunReport.model_validate_json(json.dumps(raw))

    # Then the fixed accounting error identifies the contradiction.
    assert {error["type"] for error in caught.value.errors()} == {
        "provider_call_accounting"
    }


@pytest.mark.parametrize("status", ["completed", "stopped", "rejected"])
def test_report_rejects_when_status_disagrees_with_reasons(status: str) -> None:
    # Given exact call accounting but a contradictory status/reason pair.
    raw = json.loads(run(manifest(), plan()).model_dump_json())
    raw.update(
        provider_calls=4,
        status=status,
        reasons=["test_stop"] if status == "completed" else [],
    )

    # When a report crosses the validation boundary.
    with pytest.raises(ValidationError) as caught:
        RunReport.model_validate_json(json.dumps(raw))

    # Then the fixed status error identifies the contradiction.
    assert {error["type"] for error in caught.value.errors()} == {"status_reasons"}
