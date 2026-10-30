"""Preflight-gated offline execution with bounded load and exact cleanup."""

import hashlib
import json
import math
import platform
from importlib.metadata import version
from typing import Literal, assert_never

from scripts.fleet_contract.workload import Boundary

from .fake import FakeDriver, Launch
from .models import METRIC_NAMES, Metric, Plan, RunnableManifest, authorize
from .records import (
    Cleanup,
    Distribution,
    Environment,
    Observation,
    OwnedResource,
    RunReport,
    terminal_outcomes,
)


def summarize(samples: tuple[Observation, ...]) -> tuple[Distribution, ...]:
    """Summarize each boundary including failure timings and unavailable counts."""
    boundaries: tuple[Boundary, ...] = (
        "queue_wait",
        "selected_cli",
        "readiness",
        "recovery",
    )
    summaries: list[Distribution] = []
    for boundary in boundaries:
        matching = [sample for sample in samples if sample.boundary == boundary]
        values = tuple(
            sorted(sample.seconds for sample in matching if sample.seconds is not None)
        )
        summaries.append(
            Distribution(
                boundary=boundary,
                recorded=len(matching),
                unavailable=sum(sample.seconds is None for sample in matching),
                values_seconds=values,
                p50_seconds=values[math.ceil(len(values) * 0.5) - 1]
                if values
                else None,
                p95_seconds=values[math.ceil(len(values) * 0.95) - 1]
                if values
                else None,
                maximum_seconds=values[-1] if values else None,
            )
        )
    return tuple(summaries)


def run(manifest: RunnableManifest, plan: Plan) -> RunReport:
    """Run only the built-in fake after all declaration gates, then clean ownership.

    Declarations are not security authentication. This function cannot bind or
    contact a real daemon. Fake deadlines prove simulator logic only.
    """
    reasons = list(authorize(manifest, plan))
    status: Literal["completed", "stopped", "rejected"] = (
        "rejected" if reasons else "completed"
    )
    driver: FakeDriver | None = None
    samples: list[Observation] = []
    submitted = 0
    metrics = tuple(
        Metric(
            name=name,
            value=next(
                (metric.value for metric in plan.metrics if metric.name == name), None
            ),
        )
        for name in METRIC_NAMES
    )
    cleanup = Cleanup(
        attempted=(), removed=(), residual=(), elapsed_seconds=0.0, timed_out=False
    )
    if not reasons:
        driver = FakeDriver()
        auth = manifest.authorization
        budgets = manifest.budgets
        end = min(
            auth.maximum_run_seconds,
            auth.expires_after_seconds,
            budgets.early_stop_duration_seconds,
            manifest.workload.duration_seconds,
        )
        assets = {asset.id: asset for asset in manifest.ownership.assets}
        endpoints = {
            alias.name: alias.effective_endpoint for alias in manifest.platform.aliases
        }
        failures = unknown = 0
        try:
            for index in range(len(plan.steps) * plan.repetitions):
                step = plan.steps[index % len(plan.steps)]
                if driver.now >= end:
                    match manifest.workload.termination:
                        case "duration":
                            pass
                        case "operation_count":
                            reasons.append("duration_limit")
                        case unreachable:
                            assert_never(unreachable)
                    break
                resource = None
                if step.asset is not None:
                    slot = assets[step.asset]
                    resource = next(
                        (
                            item
                            for item in reversed(driver.live)
                            if item.slot == step.asset
                            and item.endpoint == endpoints[step.endpoint]
                        ),
                        None,
                    )
                    if step.action in {"create", "pull", "build"}:
                        if len(driver.ledger) >= min(
                            auth.maximum_created_assets,
                            manifest.ownership.maximum_created_assets,
                        ):
                            reasons.append("asset_limit")
                            break
                        resource = OwnedResource(
                            endpoint=endpoints[step.endpoint],
                            kind=slot.kind,
                            identity=f"fake-{index + 1}",
                            slot=slot.id,
                            namespace=auth.run_namespace,
                            marker=auth.ownership_marker,
                        )
                submitted += 1
                observed = driver.execute(
                    step,
                    Launch(
                        submission=submitted,
                        deadline=min(end, driver.now + plan.operation_timeout_seconds),
                        resource=resource,
                    ),
                )
                samples.extend(observed)
                outcome = observed[0].outcome
                failures += outcome != "succeeded"
                unknown += outcome == "unknown"
                for budget in budgets.resources:
                    value = next(
                        metric.value
                        for metric in metrics
                        if metric.name == budget.resource
                    )
                    if value is None:
                        reasons.append("resource_metrics_unavailable")
                    elif value >= budget.early_stop:
                        reasons.append("resource_limit")
                if unknown and unknown >= budgets.early_stop_unknown_count:
                    reasons.append("unknown_limit")
                if driver.now >= end:
                    match manifest.workload.termination:
                        case "duration":
                            pass
                        case "operation_count":
                            reasons.append("duration_limit")
                        case unreachable:
                            assert_never(unreachable)
                    break
                if (
                    failures
                    and failures / submitted >= budgets.early_stop_error_fraction
                ):
                    reasons.append("failure_limit")
                if reasons:
                    break
                if manifest.workload.termination == "operation_count" and submitted >= (
                    manifest.workload.termination_operations or 0
                ):
                    break
            if (
                manifest.workload.termination == "duration"
                and driver.now < manifest.workload.duration_seconds
                and not reasons
            ):
                reasons.append("plan_exhausted")
            if (
                manifest.workload.termination == "operation_count"
                and submitted < (manifest.workload.termination_operations or 0)
                and not reasons
            ):
                reasons.append("plan_exhausted")
        finally:
            cleanup = driver.cleanup(plan, budgets.cleanup_timeout_seconds)
        if cleanup.residual:
            reasons.append("cleanup_residual")
        status = "stopped" if reasons else "completed"
    operations = [
        step.model_dump(include={"scenario", "endpoint", "action", "asset", "fault"})
        for step in plan.steps
    ]
    operation_digest = (
        "sha256:"
        + hashlib.sha256(json.dumps(operations, sort_keys=True).encode()).hexdigest()
    )
    return RunReport(
        status=status,
        reasons=tuple(sorted(set(reasons))),
        manifest_revision=manifest.identity.revision,
        workload_digest=manifest.identity.workload_digest,
        operation_digest=operation_digest,
        workload=manifest.workload,
        repetitions=plan.repetitions,
        steps=plan.steps,
        operation_timeout_seconds=plan.operation_timeout_seconds,
        environment=Environment(
            python=platform.python_version(),
            library=version("pywhales"),
            platform=manifest.platform,
            topology=manifest.topology,
            images=manifest.workload.images,
        ),
        artifacts=manifest.identity.artifacts,
        provider_calls=driver.calls if driver else 0,
        command_count=driver.commands if driver else 0,
        submitted=submitted,
        outcomes=terminal_outcomes(tuple(samples)),
        samples=tuple(samples),
        distributions=summarize(tuple(samples)),
        metrics=metrics,
        ledger=tuple(driver.ledger) if driver else (),
        cleanup=cleanup,
        elapsed_seconds=driver.now if driver else 0.0,
        load_elapsed_seconds=(driver.now - cleanup.elapsed_seconds) if driver else 0.0,
    )
