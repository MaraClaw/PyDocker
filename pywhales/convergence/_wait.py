"""Bounded service generation capture and fixed-membership waiting."""

import math
import time
from threading import Event

from pydantic import ValidationError

from pywhales._observation import check_observation, observation
from pywhales.client_config import ClientNotFoundError, ParsingError
from pywhales.convergence._types import (
    Cancelled,
    Deadline,
    Failure,
    Outcome,
    ServiceTarget,
    StackTarget,
    Success,
)
from pywhales.convergence._wire import (
    canonical,
    fingerprint,
    inspect_service,
    inspect_task,
    target_from_wire,
)
from pywhales.docker_client import DockerClient
from pywhales.enums import TaskState
from pywhales.exceptions import (
    DockerException,
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
)
from pywhales.inventory import InspectionPolicy
from pywhales.utils import run


def _positive(value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("Duration must be positive and finite")


def capture_service(
    client: DockerClient,
    service_id: str,
    *,
    policy: InspectionPolicy,
    timeout: float,
    cancel: Event | None = None,
) -> ServiceTarget | Failure | Deadline | Cancelled:
    """Capture an immutable service ID and generation within a bounded observation.

    Args:
        client: Client whose configuration selects the manager.
        service_id: Exact immutable service ID, not a name or abbreviated ID.
        policy: Required observation limits.
        timeout: Positive finite total seconds for capture.
        cancel: Optional cancellation event.

    Returns:
        A target or a typed failure, deadline, or cancellation outcome.
    """
    _positive(timeout)
    if not service_id:
        raise ValueError("Service ID must not be empty")
    deadline = time.monotonic() + timeout
    try:
        with observation(policy, deadline=deadline, cancel=cancel):
            return target_from_wire(
                client.client_config, inspect_service(client.client_config, service_id)
            )
    except ObservationCancelled:
        return Cancelled()
    except ObservationTimeout:
        return Deadline()
    except (
        DockerException,
        ObservationLimitError,
        OSError,
        ClientNotFoundError,
        ParsingError,
        ValidationError,
        ValueError,
    ) as error:
        return Failure(reason="observation", service_id=service_id, cause=error)


def _check_service(target: ServiceTarget) -> tuple[bool, Failure | None]:
    service = inspect_service(target.client_config, target.service_id)
    if (
        service.version.index != target.version
        or fingerprint(service) != target.fingerprint
    ):
        return False, Failure(reason="superseded", service_id=target.service_id)
    state = service.update.state if service.update is not None else None
    if state in {
        "paused",
        "rollback_started",
        "rollback_paused",
        "rollback_completed",
        "failed",
    }:
        return False, Failure(
            reason="update_failed", service_id=target.service_id, state=state
        )
    if state not in {None, "updating", "completed"}:
        return False, Failure(reason="malformed", service_id=target.service_id)
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
    desired_running = 0
    ambiguous = False
    for task_id in ids:
        check_observation()
        task = inspect_task(target.client_config, task_id)
        if task.service_id != target.service_id:
            raise ParsingError("Task service identity mismatch")
        if task.desired not in TaskState or task.status.state not in TaskState:
            raise ParsingError("Unknown task state")
        if task.desired != "running":
            continue
        desired_running += 1
        if (
            task.slot is None
            or task.slot < 1
            or task.slot > target.replicas
            or task.spec is None
        ):
            ambiguous = True
            continue
        if task.slot in seen_slots:
            ambiguous = True
        seen_slots.add(task.slot)
        if (
            task.status.state == "running"
            and canonical(task.spec, template=True) == target.template
        ):
            slots.add(task.slot)
    ready = (
        not ambiguous
        and desired_running == target.replicas
        and len(slots) == target.replicas
        and state != "updating"
    )
    return ready, None


def wait(
    target: ServiceTarget | StackTarget,
    *,
    policy: InspectionPolicy,
    timeout: float,
    poll_interval: float,
    cancel: Event | None = None,
) -> Outcome:
    """Wait for the captured service or fixed stack membership without mutation.

    Args:
        target: Captured service generation or explicitly fixed stack membership.
        policy: Required per-launch observation bounds.
        timeout: Positive finite total seconds from entry.
        poll_interval: Positive finite seconds between observations.
        cancel: Optional cancellation event.

    Returns:
        Success, terminal failure, deadline, or cancellation. Failure causes and
        captured templates may contain confidential wire data. Cleanup can outlast
        the deadline and stops only local CLI processes, not daemon-side work.
    """
    _positive(timeout)
    _positive(poll_interval)
    deadline = time.monotonic() + timeout
    targets = (target,) if isinstance(target, ServiceTarget) else target.services
    current: str | None = None
    try:
        with observation(policy, deadline=deadline, cancel=cancel) as state:
            while True:
                check_observation(state)
                ready = True
                for service in targets:
                    check_observation(state)
                    current = service.service_id
                    satisfied, failure = _check_service(service)
                    if failure is not None:
                        return failure
                    ready = ready and satisfied
                if ready:
                    for service in targets:
                        current = service.service_id
                        latest = inspect_service(
                            service.client_config, service.service_id
                        )
                        if (
                            latest.version.index != service.version
                            or fingerprint(latest) != service.fingerprint
                        ):
                            return Failure(
                                reason="superseded", service_id=service.service_id
                            )
                        update = (
                            latest.update.state if latest.update is not None else None
                        )
                        if update in {
                            "paused",
                            "rollback_started",
                            "rollback_paused",
                            "rollback_completed",
                            "failed",
                        }:
                            return Failure(
                                reason="update_failed",
                                service_id=service.service_id,
                                state=update,
                            )
                        if update not in {None, "updating", "completed"}:
                            return Failure(
                                reason="malformed",
                                service_id=service.service_id,
                            )
                        if update == "updating":
                            ready = False
                    if ready:
                        return Success()
                check_observation(state)
                next_poll = time.monotonic() + poll_interval
                while time.monotonic() < next_poll:
                    check_observation(state)
                    remaining = min(
                        next_poll - time.monotonic(), deadline - time.monotonic(), 0.05
                    )
                    if cancel is None:
                        time.sleep(max(0, remaining))
                    else:
                        cancel.wait(max(0, remaining))
    except ObservationCancelled:
        return Cancelled()
    except ObservationTimeout:
        return Deadline()
    except (
        DockerException,
        ObservationLimitError,
        OSError,
        ClientNotFoundError,
        ParsingError,
        ValidationError,
        ValueError,
    ) as error:
        return Failure(reason="observation", service_id=current, cause=error)
