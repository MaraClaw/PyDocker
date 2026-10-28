"""Static service rollout and rollback return contracts, checked by ty."""

from datetime import timedelta
from typing import assert_type

from pywhales import DockerClient, Service
from pywhales.enums import FailureAction, UpdateOrder


def _check_service_rollout(client: DockerClient, service: Service) -> None:
    assert_type(
        client.service.update("name", False, True, "image", False, True, 1), None
    )
    assert_type(service.update(False, True, "image", False, True, 1), None)
    assert_type(
        client.service.update(
            service,
            update_parallelism=1,
            update_delay=timedelta(microseconds=1),
            update_failure_action=FailureAction.ROLLBACK,
            update_monitor=1,
            update_max_failure_ratio=0.5,
            update_order=UpdateOrder.START_FIRST,
            rollback_parallelism=0,
            rollback_delay=1,
            rollback_failure_action=FailureAction.PAUSE,
            rollback_monitor=timedelta(),
            rollback_max_failure_ratio=0.0,
            rollback_order="stop-first",
        ),
        None,
    )
    assert_type(
        service.update(
            update_parallelism=1,
            update_delay=1,
            update_failure_action="continue",
            update_monitor=1,
            update_max_failure_ratio=0.5,
            update_order="start-first",
            rollback_parallelism=0,
            rollback_delay=1,
            rollback_failure_action="pause",
            rollback_monitor=1,
            rollback_max_failure_ratio=0.0,
            rollback_order=UpdateOrder.STOP_FIRST,
        ),
        None,
    )
    assert_type(client.service.rollback("name"), None)
    assert_type(client.service.rollback(service, detach=True, quiet=True), None)
