"""Capture and evaluate a Compose project's selected services each cycle.

A cycle re-lists the project's containers, inspects each one, and keeps only
counts, never container payloads. A one-shot service that exited 0 stays a
blocker, since readiness requires ``running``: select only long-running services.
"""

import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager

from pydantic import ValidationError

from pywhales._observation import ObservationState, check_observation
from pywhales.client_config import ClientConfig, ClientNotFoundError, ParsingError
from pywhales.convergence._types import (
    Cancelled,
    ComposeService,
    ComposeTarget,
    Deadline,
    DeadlineDetail,
    Failure,
    Outcome,
    Success,
)
from pywhales.convergence._wire import (
    ContainerWire,
    inspect_container,
    list_compose_ids,
)
from pywhales.exceptions import (
    DockerException,
    NoSuchContainer,
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
)

SLEEP_SLICE = 0.05

_INTERRUPTIONS = (
    ObservationCancelled,
    ObservationTimeout,
    ValidationError,
    ParsingError,
    DockerException,
    ObservationLimitError,
    OSError,
    ClientNotFoundError,
    ValueError,
)


def _outcome(
    error: Exception, last: DeadlineDetail | None
) -> Cancelled | Deadline | Failure:
    """Map an interruption to its outcome without keeping malformed input."""
    match error:
        case ObservationCancelled():
            return Cancelled()
        case ObservationTimeout():
            return Deadline(last)
        case ValidationError() | ParsingError():
            return Failure(reason="malformed")
        case _:
            return Failure(reason="observation", cause=error)


def validate_selection(config: ClientConfig, services: tuple[str, ...]) -> None:
    """Reject an invalid selection or a dry-run client before any launch."""
    if (
        type(services) is not tuple
        or not services
        or any(type(name) is not str or not name for name in services)
        or len(set(services)) != len(services)
    ):
        raise ValueError("Services must be a nonempty tuple of distinct names")
    if config.compose_dry_run:
        raise ValueError("A dry-run client cannot be captured")


def list_members(
    config: ClientConfig, services: tuple[str, ...]
) -> list[ContainerWire]:
    """List and inspect the selected services' containers, leaving out one-offs."""
    members: list[ContainerWire] = []
    for identity in list_compose_ids(config, services):
        check_observation()
        container = inspect_container(config, identity)
        if container.config.labels.oneoff != "True":
            members.append(container)
    return members


def snapshot_target(
    config: ClientConfig, services: tuple[str, ...]
) -> ComposeTarget | Failure:
    """Capture each selected service's current containers and single config hash."""
    members = list_members(config, services)
    if {member.config.labels.service for member in members} - set(services):
        raise ParsingError("Container outside the selected services")
    captured: list[ComposeService] = []
    for name in services:
        group = [member for member in members if member.config.labels.service == name]
        hashes = {member.config.labels.config_hash for member in group}
        if len(hashes) != 1:
            return Failure(reason="unsupported")
        config_hash = hashes.pop()
        if config_hash is None or not config_hash:
            raise ParsingError("Missing Compose config hash")
        captured.append(
            ComposeService(
                name=name,
                config_hash=config_hash,
                container_ids=tuple(member.id for member in group),
            )
        )
    return ComposeTarget(client_config=config, services=tuple(captured))


def capture_project(
    config: ClientConfig,
    services: tuple[str, ...],
    scope: AbstractContextManager[ObservationState],
) -> ComposeTarget | Failure | Deadline | Cancelled:
    """Capture the selected services inside an already bounded observation scope."""
    try:
        with scope:
            return snapshot_target(config, services)
    except _INTERRUPTIONS as error:
        return _outcome(error, None)


def _affected(
    groups: Iterable[list[ContainerWire]], blocked: Callable[[ContainerWire], bool]
) -> int:
    return sum(any(map(blocked, group)) for group in groups)


def check_compose(target: ComposeTarget) -> DeadlineDetail | Failure:
    """Re-list the project, then count each blocker once per affected service.

    Membership is the non-one-off containers of the selected services. Any added or
    vanished container, or a changed service label or config hash, is superseded.
    """
    config = target.client_config
    if config.compose_dry_run:
        return Failure(reason="unsupported")
    try:
        members = list_members(
            config, tuple(service.name for service in target.services)
        )
    except NoSuchContainer:
        return Failure(reason="superseded")
    current = {member.id: member for member in members}
    if {
        identity: (member.config.labels.service, member.config.labels.config_hash)
        for identity, member in current.items()
    } != {
        identity: (service.name, service.config_hash)
        for service in target.services
        for identity in service.container_ids
    }:
        return Failure(reason="superseded")
    groups = [
        [current[identity] for identity in service.container_ids]
        for service in target.services
    ]
    return DeadlineDetail(
        captured_services=len(groups),
        non_running_containers=_affected(
            groups, lambda container: container.state.status != "running"
        ),
        restarting_containers=_affected(
            groups, lambda container: container.state.restarting
        ),
        paused_containers=_affected(groups, lambda container: container.state.paused),
        unhealthy_containers=_affected(
            groups,
            lambda container: (
                (container.config.healthcheck and container.state.health is None)
                or (
                    container.state.health is not None
                    and container.state.health.status != "healthy"
                )
            ),
        ),
    )


def _settled(cycle: DeadlineDetail | Failure) -> bool:
    return isinstance(cycle, DeadlineDetail) and cycle == DeadlineDetail(
        cycle.captured_services
    )


def _pause(state: ObservationState, interval: float) -> None:
    """Sleep in short slices so cancellation and expiry are noticed promptly."""
    end = time.monotonic() + interval
    if state.deadline is not None:
        end = min(end, state.deadline)
    while (now := time.monotonic()) < end:
        check_observation(state)
        time.sleep(min(end - now, SLEEP_SLICE))


def wait_compose(
    target: ComposeTarget,
    scope: AbstractContextManager[ObservationState],
    poll_interval: float,
) -> Outcome:
    """Poll the captured services under an already bounded observation scope.

    Every cycle re-lists membership. Once all containers are ready the project is
    evaluated again, and success needs that final pass to be ready and unchanged.
    The scope is entered here, so a deadline reached as it exits still yields the
    latest complete cycle's counts.
    """
    last_complete: DeadlineDetail | None = None
    try:
        with scope as state:
            while True:
                check_observation(state)
                cycle = check_compose(target)
                if _settled(cycle):
                    cycle = check_compose(target)
                if isinstance(cycle, Failure):
                    return cycle
                check_observation(state)
                last_complete = cycle
                if _settled(cycle):
                    return Success()
                _pause(state, poll_interval)
    except _INTERRUPTIONS as error:
        return _outcome(error, last_complete)
