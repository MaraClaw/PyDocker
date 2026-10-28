from collections.abc import Callable, Iterator
from itertools import count
from unittest.mock import patch

import pytest

from pywhales.admission import (
    AdmissionOwner,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
)
from pywhales.admission._state import UINT64_MAX, Outcome


def test_iter_calls_pulls_one_at_a_time_from_infinite_producer() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    produced: list[int] = []

    def inputs() -> Iterator[Callable[[], int]]:
        for number in count():
            produced.append(number)

            def operation(value: int = number) -> int:
                return value

            yield operation

    results = owner.iter_calls(pressure, inputs())
    assert produced == []
    assert next(results) == 0
    assert produced == [0]
    assert next(results) == 1
    assert produced == [0, 1]
    assert owner.snapshot().classes[0].outcomes.completed == 2


@pytest.mark.parametrize("producer_failure", [False, True])
def test_iter_calls_propagates_error_without_advancing_again(
    producer_failure: bool,
) -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    error = ValueError("original")
    advanced: list[int] = []

    def fail() -> int:
        raise error

    def inputs() -> Iterator[Callable[[], int]]:
        if producer_failure:
            raise error
        yield fail
        advanced.append(1)
        yield lambda: 2

    results = owner.iter_calls(pressure, inputs())
    with pytest.raises(ValueError) as raised:
        next(results)
    assert raised.value is error
    assert advanced == []
    assert owner.snapshot().classes[0].active == 0


def test_queue_clock_does_not_start_on_request_construction() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    with patch("pywhales.admission._owner.monotonic", return_value=1234) as clock:
        request = owner.request(pressure, 1)
        clock.assert_not_called()
        assert request.call(lambda: 42) == 42
        clock.assert_called()


def test_outcomes_saturate_at_uint64_and_do_not_wrap() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    owner._buckets[pressure].counts[Outcome.COMPLETED] = UINT64_MAX - 1
    owner.request(pressure).call(lambda: None)
    owner.request(pressure).call(lambda: None)
    outcomes = owner.snapshot().classes[0].outcomes
    assert outcomes.completed == UINT64_MAX
    assert outcomes.saturated
