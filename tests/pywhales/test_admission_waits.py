from concurrent.futures import ThreadPoolExecutor
from threading import TIMEOUT_MAX, Event
from unittest.mock import patch

import pytest

from pywhales.admission import (
    AdmissionOwner,
    AdmissionQueueTimeout,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
)


def test_interrupted_pending_wait_is_terminal_and_records_once() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 1)})
    bucket = owner._buckets[pressure]
    bucket.active = 1
    request = owner.request(pressure)
    error = KeyboardInterrupt()
    with (
        patch.object(owner._condition, "wait", side_effect=error),
        pytest.raises(KeyboardInterrupt) as raised,
    ):
        request.call(lambda: None)
    assert raised.value is error
    assert not request.cancel()
    snapshot = owner.snapshot().classes[0]
    assert snapshot.pending == 0
    assert (snapshot.outcomes.failed, snapshot.outcomes.cancelled) == (1, 0)


def test_large_finite_queue_timeout_respects_platform_wait_limit() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 1)})
    owner._buckets[pressure].active = 1
    request = owner.request(pressure, queue_timeout=1e100)
    waits: list[float | None] = []

    def interrupt(timeout: float | None = None) -> bool:
        waits.append(timeout)
        assert timeout is not None and timeout <= TIMEOUT_MAX
        raise KeyboardInterrupt

    with (
        patch.object(owner._condition, "wait", side_effect=interrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        request.call(lambda: None)
    assert len(waits) == 1


def test_queue_deadline_covers_time_awaiting_admission_lock() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    clock_started = Event()
    now = [100.0]
    calls: list[int] = []

    def clock() -> float:
        value = now[0]
        clock_started.set()
        return value

    with (
        ThreadPoolExecutor(1) as executor,
        patch("pywhales.admission._owner.monotonic", side_effect=clock),
    ):
        with owner._condition:
            result = executor.submit(
                owner.request(pressure, 1).call, lambda: calls.append(1)
            )
            assert clock_started.wait(3)
            now[0] = 102.0
        with pytest.raises(AdmissionQueueTimeout):
            result.result(3)
    assert calls == []
    snapshot = owner.snapshot().classes[0]
    assert (snapshot.active, snapshot.pending, snapshot.outcomes.queue_timed_out) == (
        0,
        0,
        1,
    )
