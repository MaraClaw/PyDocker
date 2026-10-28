"""Deterministic in-memory driver; never imports a process or network adapter."""

from dataclasses import dataclass
from typing import assert_never

from scripts.fleet_contract.results import Outcome
from scripts.fleet_contract.workload import Boundary

from .models import Plan, Step
from .records import Cleanup, Observation, OwnedResource


class FakeDriver:
    """Mutable fake clock and resource inventory owned by one simulation run."""

    def __init__(self) -> None:
        """Start with an empty inventory and zero calls; contact nothing."""
        self.now = 0.0
        self.calls = 0
        self.commands = 0
        self.ledger: list[OwnedResource] = []
        self.live: list[OwnedResource] = []

    def advance(self, seconds: float, deadline: float) -> float:
        """Consume only remaining simulated time, including exact deadline edges."""
        elapsed = min(seconds, max(0.0, deadline - self.now))
        self.now += elapsed
        return elapsed

    def execute(self, step: Step, context: Launch) -> tuple[Observation, ...]:
        """Simulate a bounded submission and preserve resources after lost responses."""
        start = self.now
        queue = self.advance(step.queue_seconds, context.deadline)
        queue_end = self.now
        launched = self.now < context.deadline
        local = 0.0
        ready: float | None = None
        recovery: float | None = None
        outcome: Outcome = step.outcome
        if launched:
            self.calls += 1
            self.commands += 1
            resource = context.resource
            match step.action:
                case "create" | "pull" | "build":
                    if resource is not None:
                        self.ledger.append(resource)
                        self.live.append(resource)
                case "start" | "stop" | "remove":
                    if resource not in self.live:
                        outcome = "failed"
                case "inspect" | "fault":
                    pass
                case "cleanup":
                    raise AssertionError("reserved_cleanup")
                case unreachable:
                    assert_never(unreachable)
            local = self.advance(step.local_seconds, context.deadline)
            if step.readiness_seconds is not None:
                self.advance(step.readiness_seconds, context.deadline)
                ready = self.now - start
            if step.recovery_seconds is not None:
                self.advance(step.recovery_seconds, context.deadline)
                recovery = self.now - queue_end
            if (
                step.action == "remove"
                and outcome == "succeeded"
                and self.now < context.deadline
                and resource is not None
                and resource in self.live
            ):
                self.live.remove(resource)
        if self.now >= context.deadline:
            outcome = "timed_out"
        reason = (
            "deadline"
            if outcome == "timed_out"
            else "operation_failed"
            if outcome != "succeeded"
            else "observed"
        )
        values: list[tuple[Boundary, float | None, float, float]] = [
            ("queue_wait", queue, start, queue_end)
        ]
        if launched:
            values.extend(
                (
                    ("selected_cli", local, queue_end, queue_end + local),
                    (
                        "readiness",
                        ready,
                        start,
                        queue_end + local if ready is None else start + ready,
                    ),
                )
            )
            if step.action == "fault":
                values.append(
                    ("recovery", recovery, self.now - (recovery or 0.0), self.now)
                )
        return tuple(
            Observation(
                submission=context.submission,
                scenario=step.scenario,
                boundary=boundary,
                outcome=outcome,
                seconds=seconds,
                reason="unavailable" if seconds is None else reason,
                started_at_seconds=begin,
                ended_at_seconds=end,
            )
            for boundary, seconds, begin, end in values
        )

    def cleanup(self, plan: Plan, seconds: float) -> Cleanup:
        """Remove only exact acquired live identities within one cleanup budget."""
        start = self.now
        deadline = start + seconds
        attempted: list[OwnedResource] = []
        removed: list[OwnedResource] = []
        for resource in reversed(self.live.copy()):
            if self.now >= deadline:
                break
            attempted.append(resource)
            self.calls += 1
            spent = self.advance(plan.cleanup_seconds_per_asset, deadline)
            if (
                spent < plan.cleanup_seconds_per_asset
                or resource.slot in plan.cleanup_fail_assets
            ):
                continue
            self.live.remove(resource)
            removed.append(resource)
        return Cleanup(
            attempted=tuple(attempted),
            removed=tuple(removed),
            residual=tuple(self.live),
            elapsed_seconds=self.now - start,
            timed_out=bool(self.live) and self.now >= deadline,
        )


@dataclass(frozen=True, slots=True)
class Launch:
    """Per-submission deadline and exact identity passed to the fake driver."""

    submission: int
    deadline: float
    resource: OwnedResource | None
