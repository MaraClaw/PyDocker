from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Barrier, Event

import pytest

from pywhales.admission import (
    AdmissionOwner,
    ClassLimit,
    DemandSnapshot,
    EndpointIdentity,
    PressureClass,
)


@pytest.mark.parametrize("_repetition", range(20))
def test_concurrent_reservations_compete_atomically_for_last_demand(
    _repetition: int,
) -> None:
    # Given
    pressure = PressureClass("read")
    owner = AdmissionOwner(
        EndpointIdentity("local"), {pressure: ClassLimit(2, 0, demand_capacity=3)}
    )
    requests = [owner.request(pressure) for _ in range(2)]
    streams = [request.stream(lambda: nullcontext(iter(()))) for request in requests]
    race = Barrier(2)

    def resize(index: int) -> bool:
        race.wait(3)
        return requests[index].try_resize(2)

    try:
        # When
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(resize, (0, 1)))
        # Then
        assert sorted(results) == [False, True]
        snapshot = owner.snapshot().classes[0]
        assert snapshot.active == 2
        assert snapshot.demand == DemandSnapshot(3, 0, 3, 0)
    finally:
        for stream in streams:
            stream.close()
    assert owner.snapshot().classes[0].demand == DemandSnapshot(0, 0, 3, 0)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("_repetition", range(10))
def test_request_and_stream_resizers_race_cleanup_without_crossing_its_barrier(
    failure: bool,
    _repetition: int,
) -> None:
    # Given
    pressure = PressureClass("stream")
    owner = AdmissionOwner(
        EndpointIdentity("local"), {pressure: ClassLimit(1, 0, demand_capacity=6)}
    )
    exiting, finish_exit = Event(), Event()
    original, cleanup = KeyboardInterrupt(), ValueError("cleanup")
    race = Barrier(3)
    exits: list[int] = []

    @contextmanager
    def source() -> Generator[Iterator[int]]:
        try:
            yield iter(())
        finally:
            exits.append(1)
            exiting.set()
            assert finish_exit.wait(3)
            if failure:
                raise cleanup

    request = owner.request(pressure, demand=2)
    stream = request.stream(source)

    def resize_request() -> bool:
        race.wait(3)
        return request.try_resize(4)

    def resize_stream() -> bool:
        race.wait(3)
        return stream.try_resize(3)

    def close() -> None:
        race.wait(3)
        if failure:
            stream.__exit__(type(original), original, original.__traceback__)
        else:
            stream.close()

    with ThreadPoolExecutor(3) as executor:
        try:
            # When
            first, second = (
                executor.submit(resize_request),
                executor.submit(resize_stream),
            )
            closed = executor.submit(close)
            assert exiting.wait(3)
            succeeded: list[int] = []
            for result, demand in ((first, 4), (second, 3)):
                error = result.exception(3)
                if error is None:
                    assert result.result(3)
                    succeeded.append(demand)
                else:
                    assert type(error) is RuntimeError
            # Then
            snapshot = owner.snapshot().classes[0]
            held = snapshot.demand.active
            assert snapshot.active == 1
            assert held in (succeeded or [2])
            for resize in (request.try_resize, stream.try_resize):
                with pytest.raises(RuntimeError):
                    resize(5)
            assert not owner.wait_drained(timeout=0.001)
        finally:
            finish_exit.set()
        if failure:
            with pytest.raises(BaseExceptionGroup) as raised:
                closed.result(3)
            assert raised.value.exceptions == (original, cleanup)
        else:
            closed.result(3)
    stream.close()
    snapshot = owner.snapshot().classes[0]
    assert snapshot.demand.active == snapshot.demand.pending == 0
    assert snapshot.active == snapshot.pending == 0
    assert snapshot.outcomes.failed == int(failure)
    assert snapshot.outcomes.stream_closed == int(not failure)
    assert exits == [1]
