from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, current_thread
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.admission import (
    AdmissionCancelled,
    AdmissionOwner,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
)


def test_concurrent_closers_wait_for_one_exit_and_inflight_next() -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    reading, exiting, finish_exit, finish_read = Event(), Event(), Event(), Event()
    reader_woken, first_waiting, second_waiting = Event(), Event(), Event()
    exits: list[int] = []

    def records() -> Iterator[int]:
        reading.set()
        assert exiting.wait(3)
        reader_woken.set()
        assert finish_read.wait(3)
        yield 7

    @contextmanager
    def source() -> Generator[Iterator[int]]:
        try:
            yield records()
        finally:
            exits.append(1)
            exiting.set()
            assert finish_exit.wait(3)

    stream = owner.request(pressure).stream(source)
    wait = stream._condition.wait

    def signal_wait(timeout: float | None = None) -> bool:
        if current_thread().name.startswith("first"):
            first_waiting.set()
        if current_thread().name.startswith("second"):
            second_waiting.set()
        return wait(timeout)

    with (
        ThreadPoolExecutor(1, "reader") as reader,
        ThreadPoolExecutor(1, "first") as first,
        ThreadPoolExecutor(1, "second") as second,
        patch.object(stream._condition, "wait", side_effect=signal_wait),
    ):
        read = reader.submit(next, stream)
        try:
            assert reading.wait(3)
            close = first.submit(stream.close)
            assert exiting.wait(3) and reader_woken.wait(3)
            duplicate = second.submit(stream.close)
            assert second_waiting.wait(3)
            assert owner.snapshot().classes[0].active == 1
            finish_exit.set()
            assert first_waiting.wait(3)
            assert owner.snapshot().classes[0].active == 1
            assert not close.done() and not duplicate.done()
        finally:
            finish_exit.set()
            finish_read.set()
        close.result(3)
        duplicate.result(3)
        with pytest.raises(StopIteration):
            read.result(3)
    assert exits == [1]
    assert owner.snapshot().classes[0].active == 0
    assert owner.snapshot().classes[0].outcomes.stream_closed == 1


@pytest.mark.parametrize("_repetition", range(20))
def test_cancel_races_grant_atomically_without_starting_cancelled_preflight(
    _repetition: int,
) -> None:
    pressure = PressureClass("create")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 1)})
    held, release, queued = Event(), Event(), Event()
    race = Barrier(2)
    calls: list[int] = []
    request = owner.request(pressure)

    def active() -> None:
        held.set()
        assert release.wait(3)
        race.wait(3)

    wait = owner._condition.wait

    def signal_wait(timeout: float | None = None) -> bool:
        queued.set()
        return wait(timeout)

    def cancel() -> bool:
        race.wait(3)
        return request.cancel()

    with ThreadPoolExecutor(3) as executor:
        first = executor.submit(owner.request(pressure).call, active)
        try:
            assert held.wait(3)
            with patch.object(owner._condition, "wait", side_effect=signal_wait):
                second = executor.submit(request.call, lambda: calls.append(1))
                assert queued.wait(3)
                cancelled = executor.submit(cancel)
                release.set()
                won = cancelled.result(3)
                if won:
                    with pytest.raises(AdmissionCancelled):
                        second.result(3)
                    assert calls == []
                else:
                    second.result(3)
                    assert calls == [1]
        finally:
            release.set()
        first.result(3)
    assert owner.snapshot().classes[0].active == 0


def test_cancelled_real_create_never_performs_pull_preflight_or_launch() -> None:
    pressure = PressureClass("create")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 1)})
    request = owner.request(pressure)
    client = DockerClient()
    assert request.cancel()
    with (
        patch(
            "pywhales.components.image.cli_wrapper.ImageCLI._pull_if_necessary"
        ) as preflight,
        patch("pywhales.components.container.cli_wrapper.run") as launch,
    ):
        with pytest.raises(AdmissionCancelled):
            request.call(lambda: client.create("private-image"))
        preflight.assert_not_called()
        launch.assert_not_called()
