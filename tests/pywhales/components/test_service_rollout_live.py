"""Explicitly authorized disposable-manager service rollout scenario.

Never run this module without an operator-selected disposable Swarm manager.
"""

import json
import os
from uuid import uuid4

import pytest

from pywhales import DockerClient
from pywhales.client_config import Command
from pywhales.utils import run


def test_live_service_rollout_and_rollback() -> None:
    if os.environ.get("PYWHALES_LIVE_SERVICE_ROLLOUT") != "1":
        pytest.skip("explicit live rollout authorization required")
    manager = os.environ.get("PYWHALES_DISPOSABLE_SWARM_MANAGER")
    if manager is None or manager == "":
        pytest.skip("explicit disposable manager selection required")
    client = DockerClient(host=manager)
    label = f"pywhales-rollout-{uuid4().hex}"
    service = client.service.create(
        "busybox",
        ["sleep", "infinity"],
        detach=True,
        labels={"pywhales-test-owner": label},
    )
    try:
        client.service.update(
            service,
            detach=True,
            replicas=0,
            update_parallelism=1,
            update_delay=2,
            update_failure_action="pause",
            update_monitor=3,
            update_max_failure_ratio=0.25,
            update_order="stop-first",
            rollback_parallelism=1,
            rollback_delay=4,
            rollback_failure_action="continue",
            rollback_monitor=5,
            rollback_max_failure_ratio=0.5,
            rollback_order="start-first",
        )
        command = Command(
            [*client.client_config.docker_cmd, "service", "inspect", service]
        )
        updated = json.loads(run(command))[0]
        update = updated["Spec"]["UpdateConfig"]
        rollback = updated["Spec"]["RollbackConfig"]
        assert update == {
            "Parallelism": 1,
            "Delay": 2000000000,
            "FailureAction": "pause",
            "Monitor": 3000000000,
            "MaxFailureRatio": 0.25,
            "Order": "stop-first",
        }
        assert rollback == {
            "Parallelism": 1,
            "Delay": 4000000000,
            "FailureAction": "continue",
            "Monitor": 5000000000,
            "MaxFailureRatio": 0.5,
            "Order": "start-first",
        }
        previous_replicas = updated["PreviousSpec"]["Mode"]["Replicated"]["Replicas"]
        assert updated["Spec"]["Mode"]["Replicated"]["Replicas"] == 0
        assert previous_replicas != 0
        client.service.rollback(service, detach=True)
        restored = json.loads(run(command))[0]
        assert restored["Spec"] == updated["PreviousSpec"]
    finally:
        service.remove()
