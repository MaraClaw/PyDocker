"""Bounded service generation capture and fixed-membership waiting."""

import math
import time
from threading import Event
from typing import assert_never

from pydantic import ValidationError

from pywhales._observation import check_observation, observation
from pywhales.client_config import ClientNotFoundError, ParsingError
from pywhales.convergence._service import ServiceObservation, check_service
from pywhales.convergence._types import (
    Cancelled,
    Deadline,
    DeadlineDetail,
    Failure,
    Outcome,
    ServiceTarget,
    StackTarget,
    Success,
)
from pywhales.convergence._wire import (
    fingerprint,
    inspect_service,
    target_from_wire,
)
from pywhales.docker_client import DockerClient
from pywhales.exceptions import (
    DockerException,
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
)
from pywhales.inventory import InspectionPolicy


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
    approved_node_ids: tuple[str, ...] | None = None,
) -> ServiceTarget | Failure | Deadline | Cancelled:
    """Capture an immutable service ID and generation within a bounded observation.

    Args:
        client: Client whose configuration selects the manager.
        service_id: Exact immutable service ID, not a name or abbreviated ID.
        policy: Required observation limits.
        timeout: Positive finite total seconds for capture.
        cancel: Optional cancellation event.
        approved_node_ids: Fixed, nonempty distinct node IDs for a Global service;
            omit for a Replicated service.

    Returns:
        A target or a typed failure, deadline, or cancellation outcome.

    Raises:
        ValueError: If supplied node membership is not a nonempty tuple of distinct,
            nonempty strings, or the timeout or service ID is invalid.
    """
    _positive(timeout)
    if not service_id:
        raise ValueError("Service ID must not be empty")
    if approved_node_ids is not None and (
        type(approved_node_ids) is not tuple
        or not approved_node_ids
        or any(type(node_id) is not str or not node_id for node_id in approved_node_ids)
        or len(set(approved_node_ids)) != len(approved_node_ids)
    ):
        raise ValueError("Approved node IDs must be a nonempty tuple of distinct IDs")
    deadline = time.monotonic() + timeout
    try:
        with observation(policy, deadline=deadline, cancel=cancel):
            return target_from_wire(
                client.client_config,
                inspect_service(client.client_config, service_id),
                approved_node_ids,
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
        Deadline detail retains only the latest complete cycle after all applicable
        generation rechecks, never a partial cycle or a fresh inspection at expiry.
    """
    _positive(timeout)
    _positive(poll_interval)
    deadline = time.monotonic() + timeout
    targets = (target,) if isinstance(target, ServiceTarget) else target.services
    current: str | None = None
    last_complete: DeadlineDetail | None = None
    try:
        with observation(policy, deadline=deadline, cancel=cancel) as state:
            while True:
                check_observation(state)
                observations: list[ServiceObservation] = []
                for service in targets:
                    check_observation(state)
                    current = service.service_id
                    match check_service(service):
                        case Failure() as failure:
                            return failure
                        case ServiceObservation() as checked:
                            observations.append(checked)
                        case unreachable:
                            assert_never(unreachable)
                ready = all(item.ready for item in observations)
                rechecked_updates = 0
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
                            rechecked_updates += 1
                check_observation(state)
                last_complete = DeadlineDetail(
                    captured_services=len(targets),
                    missing_ready_slots=sum(
                        item.missing_ready_slots for item in observations
                    ),
                    duplicate_slots=sum(item.duplicate_slots for item in observations),
                    stale_templates=sum(item.stale_templates for item in observations),
                    non_running_tasks=sum(
                        item.non_running_tasks for item in observations
                    ),
                    invalid_task_shape=sum(
                        item.invalid_task_shape for item in observations
                    ),
                    ongoing_updates=sum(item.ongoing_updates for item in observations)
                    + rechecked_updates,
                    missing_ready_nodes=sum(
                        item.missing_ready_nodes for item in observations
                    ),
                    duplicate_node_assignments=sum(
                        item.duplicate_node_assignments for item in observations
                    ),
                    missing_node_assignments=sum(
                        item.missing_node_assignments for item in observations
                    ),
                    outside_approved_nodes=sum(
                        item.outside_approved_nodes for item in observations
                    ),
                )
                if ready:
                    return Success()
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
        return Deadline(last_complete)
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
