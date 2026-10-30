"""Static Compose convergence contracts, checked by ty and not collected by pytest."""

from threading import Event
from typing import assert_type

from pywhales import DockerClient
from pywhales.convergence import (
    Cancelled,
    ComposeService,
    ComposeTarget,
    Deadline,
    Failure,
    Outcome,
    capture_compose,
    wait,
)
from pywhales.inventory import InspectionPolicy


def _check_compose_contract(
    client: DockerClient, policy: InspectionPolicy, cancel: Event
) -> None:
    captured = capture_compose(
        client, ("web", "db"), policy=policy, timeout=1.0, cancel=cancel
    )
    assert_type(captured, ComposeTarget | Failure | Deadline | Cancelled)
    if isinstance(captured, ComposeTarget):
        assert_type(captured.services, tuple[ComposeService, ...])
        assert_type(captured.services[0].container_ids, tuple[str, ...])
        assert_type(
            wait(captured, policy=policy, timeout=1.0, poll_interval=0.1), Outcome
        )
