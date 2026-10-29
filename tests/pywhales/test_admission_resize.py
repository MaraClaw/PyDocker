from contextlib import nullcontext
from weakref import ref

import pytest

from pywhales.admission import (
    AdmissionOwner,
    AdmissionRejected,
    ClassLimit,
    DemandSnapshot,
    EndpointIdentity,
    PressureClass,
    SaturationPolicy,
)


@pytest.mark.parametrize("capacity", [None, 1])
def test_default_demand_preserves_one_request_one_reservation(
    capacity: int | None,
) -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(
        EndpointIdentity("local"),
        {pressure: ClassLimit(1, 0, demand_capacity=capacity)},
    )
    request = owner.request(pressure, None)
    # When
    observed = request.call(lambda: owner.snapshot().classes[0])
    # Then
    assert (observed.active, observed.pending, observed.active_high_water) == (1, 0, 1)
    assert observed.demand == DemandSnapshot(1, 0, 1, 0)
    assert owner.snapshot().classes[0].demand == DemandSnapshot(0, 0, 1, 0)


@pytest.mark.parametrize("failure", [False, True])
def test_resized_call_releases_its_latest_demand_on_success_or_failure(
    failure: bool,
) -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    request = owner.request(pressure, demand=10**30)
    original = KeyboardInterrupt()

    def operation() -> int:
        assert request.try_resize(10**40)
        assert owner.snapshot().classes[0].demand.active == 10**40
        if failure:
            raise original
        return 7

    # When / Then
    if failure:
        with pytest.raises(KeyboardInterrupt) as raised:
            request.call(operation)
        assert raised.value is original
    else:
        assert request.call(operation) == 7
    snapshot = owner.snapshot().classes[0]
    assert (snapshot.active, snapshot.pending) == (0, 0)
    assert snapshot.demand == DemandSnapshot(0, 0, 10**40, 0)
    assert (snapshot.outcomes.completed, snapshot.outcomes.failed) == (
        int(not failure),
        int(failure),
    )
    with pytest.raises(RuntimeError):
        request.try_resize(1)


def test_growth_refusal_is_atomic_and_request_and_stream_share_reservation() -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(
        EndpointIdentity("local"), {pressure: ClassLimit(3, 0, demand_capacity=5)}
    )
    request = owner.request(pressure, demand=3)
    with request.stream(lambda: nullcontext(iter(()))) as stream:
        with owner.request(pressure, demand=2).stream(lambda: nullcontext(iter(()))):
            before = owner.snapshot()
            # When
            assert not request.try_resize(4)
            assert not stream.try_resize(5)
            # Then
            assert owner.snapshot() == before
        assert stream.try_resize(5)
        assert request.try_resize(1)
        assert owner.snapshot().classes[0].demand == DemandSnapshot(1, 0, 5, 0)
    assert owner.snapshot().classes[0].demand == DemandSnapshot(0, 0, 5, 0)
    with pytest.raises(RuntimeError):
        stream.try_resize(1)


@pytest.mark.parametrize("demand", [0, -1, True, 1.5, "2", None, 6])
def test_invalid_resize_preserves_the_reservation(demand: int) -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(
        EndpointIdentity("local"), {pressure: ClassLimit(1, 0, demand_capacity=5)}
    )
    request = owner.request(pressure, demand=3)
    with request.stream(lambda: nullcontext(iter(()))) as stream:
        before = owner.snapshot()
        # When / Then
        for resize in (request.try_resize, stream.try_resize):
            with pytest.raises(ValueError):
                resize(demand)
        assert owner.snapshot() == before


@pytest.mark.parametrize("cancelled", [False, True])
def test_inert_or_cancelled_requests_cannot_resize(cancelled: bool) -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    request = owner.request(pressure)
    if cancelled:
        assert request.cancel()
    # When / Then
    with pytest.raises(RuntimeError):
        request.try_resize(2)
    assert owner.snapshot().classes[0].demand == DemandSnapshot(0, 0, 0, 0)


def test_rejected_request_cannot_resize_and_capacity_rejects_before_callback() -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(
        EndpointIdentity("local"),
        {pressure: ClassLimit(2, 1, SaturationPolicy.REJECT, demand_capacity=3)},
    )
    request = owner.request(pressure)
    with owner.request(pressure, demand=3).stream(lambda: nullcontext(iter(()))):
        # When / Then
        with pytest.raises(AdmissionRejected):
            request.call(lambda: pytest.fail("over-capacity work ran"))
        with pytest.raises(RuntimeError):
            request.try_resize(1)
    assert owner.snapshot().classes[0].demand == DemandSnapshot(0, 0, 3, 0)


def test_completed_request_does_not_retain_user_callable() -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    request = owner.request(pressure, demand=2)

    def operation() -> int:
        return 3

    reference = ref(operation)
    # When
    assert request.call(operation) == 3
    del operation
    # Then
    assert reference() is None
    assert owner.snapshot().classes[0].demand.active == 0


@pytest.mark.parametrize("demand", [0, True, 6])
def test_invalid_demand_takes_precedence_over_inactive_lifecycle(demand: int) -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(
        EndpointIdentity("local"), {pressure: ClassLimit(1, 0, demand_capacity=5)}
    )
    request = owner.request(pressure)
    request.call(lambda: None)
    # When / Then
    with pytest.raises(ValueError):
        request.try_resize(demand)
    assert owner.snapshot().classes[0].demand == DemandSnapshot(0, 0, 1, 0)


def test_demand_capacity_is_independent_per_pressure_class() -> None:
    # Given
    first, second = PressureClass("first"), PressureClass("second")
    owner = AdmissionOwner(
        EndpointIdentity("local"),
        {
            first: ClassLimit(1, 0, demand_capacity=3),
            second: ClassLimit(1, 0, demand_capacity=4),
        },
    )
    # When
    with (
        owner.request(first, demand=3).stream(lambda: nullcontext(iter(()))),
        owner.request(second, demand=3).stream(lambda: nullcontext(iter(()))) as stream,
    ):
        assert stream.try_resize(4)
        # Then
        assert [state.demand.active for state in owner.snapshot().classes] == [3, 4]
    assert [state.demand.active for state in owner.snapshot().classes] == [0, 0]
