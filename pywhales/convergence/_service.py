"""Evaluate one captured service without retaining inspection payloads."""

from dataclasses import dataclass

from pywhales._observation import check_observation
from pywhales.client_config import ParsingError
from pywhales.convergence._types import Failure, ServiceTarget
from pywhales.convergence._wire import (
    canonical,
    fingerprint,
    inspect_service,
    inspect_task,
)
from pywhales.enums import TaskState
from pywhales.utils import run


@dataclass(frozen=True, slots=True)
class ServiceObservation:
    """Independent blocker flags for a single service in a provisional cycle."""

    missing_ready_slots: bool
    duplicate_slots: bool
    stale_templates: bool
    non_running_tasks: bool
    invalid_task_shape: bool
    ongoing_updates: bool

    @property
    def ready(self) -> bool:
        """Return whether every observed convergence condition was satisfied."""
        return not any(
            (
                self.missing_ready_slots,
                self.duplicate_slots,
                self.stale_templates,
                self.non_running_tasks,
                self.invalid_task_shape,
                self.ongoing_updates,
            )
        )


def check_service(target: ServiceTarget) -> ServiceObservation | Failure:
    """Inspect a captured generation and count blockers only on desired-running tasks."""
    service = inspect_service(target.client_config, target.service_id)
    if (
        service.version.index != target.version
        or fingerprint(service) != target.fingerprint
    ):
        return Failure(reason="superseded", service_id=target.service_id)
    state = service.update.state if service.update is not None else None
    if state in {
        "paused",
        "rollback_started",
        "rollback_paused",
        "rollback_completed",
        "failed",
    }:
        return Failure(
            reason="update_failed", service_id=target.service_id, state=state
        )
    if state not in {None, "updating", "completed"}:
        return Failure(reason="malformed", service_id=target.service_id)
    ids = run(
        [
            *target.client_config.docker_cmd,
            "service",
            "ps",
            "--quiet",
            "--no-trunc",
            target.service_id,
        ]
    ).splitlines()
    if any(not task_id for task_id in ids) or len(ids) != len(set(ids)):
        raise ParsingError("Invalid task listing")
    slots: set[int] = set()
    seen_slots: set[int] = set()
    duplicate = stale = non_running = invalid = False
    for task_id in ids:
        check_observation()
        task = inspect_task(target.client_config, task_id)
        if task.service_id != target.service_id:
            raise ParsingError("Task service identity mismatch")
        if task.desired not in TaskState or task.status.state not in TaskState:
            raise ParsingError("Unknown task state")
        if task.desired != "running":
            continue
        non_running |= task.status.state != "running"
        if task.slot is not None:
            duplicate |= task.slot in seen_slots
            seen_slots.add(task.slot)
        matching = (
            task.spec is not None
            and canonical(task.spec, template=True) == target.template
        )
        stale |= task.spec is not None and not matching
        if (
            task.slot is None
            or task.slot < 1
            or task.slot > target.replicas
            or task.spec is None
        ):
            invalid = True
            continue
        if task.status.state == "running" and matching:
            slots.add(task.slot)
    return ServiceObservation(
        missing_ready_slots=len(slots) < target.replicas,
        duplicate_slots=duplicate,
        stale_templates=stale,
        non_running_tasks=non_running,
        invalid_task_shape=invalid,
        ongoing_updates=state == "updating",
    )
