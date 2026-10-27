from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from threading import Event, current_thread
from types import TracebackType
from typing import override
from unittest.mock import patch

import pytest

from pywhales.admission import (
    AdmissionOwner,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
)
from pywhales.admission._state import Outcome


class ClosingSource(AbstractContextManager[Iterator[int]], Iterator[int]):
    def __init__(self) -> None:
        self.reading, self.wake, self.finish_read = Event(), Event(), Event()
        self.reader_ended = Event()
        self.exit_started, self.finish_exit = Event(), Event()
        self.hold_exit = False
        self.read_error: BaseException | None = None
        self.cleanup_error: BaseException | None = None

    @override
    def __enter__(self) -> Iterator[int]:
        return self

    @override
    def __iter__(self) -> Iterator[int]:
        return self

    @override
    def __next__(self) -> int:
        self.reading.set()
        assert self.wake.wait(3) and self.finish_read.wait(3)
        if self.read_error is not None:
            raise self.read_error
        raise StopIteration

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.wake.set()
        self.exit_started.set()
        if self.hold_exit:
            assert self.finish_exit.wait(3)
        if self.cleanup_error is not None:
            assert self.reader_ended.wait(3)
            raise self.cleanup_error
        return False


def test_reader_gets_original_first_group_when_closer_finishes_first() -> None:
    pressure = PressureClass("stream")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    source = ClosingSource()
    original, cleanup = ValueError("read"), RuntimeError("cleanup")
    source.read_error, source.cleanup_error = original, cleanup
    source.finish_read.set()
    stream = owner.request(pressure).stream(lambda: source)
    closer_done = Event()
    finish = stream._finish

    def delayed_finish(error: BaseException | None, outcome: Outcome) -> None:
        if error is original:
            source.reader_ended.set()
            assert closer_done.wait(3)
        try:
            finish(error, outcome)
        finally:
            if error is None:
                closer_done.set()

    with (
        ThreadPoolExecutor(2) as executor,
        patch.object(stream, "_finish", side_effect=delayed_finish),
    ):
        reader = executor.submit(next, stream)
        assert source.reading.wait(3)
        closer = executor.submit(stream.close)
        try:
            with pytest.raises(BaseExceptionGroup) as closed:
                closer.result(3)
            assert closed.value.exceptions == (original, cleanup)
            with pytest.raises(BaseExceptionGroup) as read:
                reader.result(3)
            assert read.value.exceptions == (original, cleanup)
        finally:
            closer_done.set()
            source.wake.set()
            source.finish_read.set()
    assert owner.snapshot().classes[0].active == 0
    assert owner.snapshot().classes[0].outcomes.failed == 1


@pytest.mark.parametrize("reader_failure", [False, True])
def test_interrupted_cleanup_waiter_waits_and_preserves_original(
    reader_failure: bool,
) -> None:
    pressure = PressureClass("stream")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    source = ClosingSource()
    source.hold_exit = True
    source.finish_read.set()
    original, interruption = ValueError("original"), KeyboardInterrupt()
    if reader_failure:
        source.read_error = original
    stream = owner.request(pressure).stream(lambda: source)
    interrupted, waiting = Event(), Event()
    wait = stream._condition.wait

    def interrupt_waiter(timeout: float | None = None) -> bool:
        if current_thread().name.startswith("waiter"):
            if not interrupted.is_set():
                interrupted.set()
                raise interruption
            waiting.set()
        return wait(timeout)

    with (
        ThreadPoolExecutor(1, "owner") as closers,
        ThreadPoolExecutor(1, "waiter") as waiters,
        patch.object(stream._condition, "wait", side_effect=interrupt_waiter),
    ):
        result = waiters.submit(next, stream) if reader_failure else None
        try:
            if reader_failure:
                assert source.reading.wait(3)
            closer = closers.submit(stream.close)
            assert source.exit_started.wait(3)
            if result is None:
                result = waiters.submit(stream.close)
            assert interrupted.wait(3) and waiting.wait(3)
            assert not result.done()
            assert owner.snapshot().classes[0].active == 1
        finally:
            source.wake.set()
            source.finish_exit.set()
        closer.result(3)
        if reader_failure:
            with pytest.raises(BaseExceptionGroup) as raised:
                result.result(3)
            assert raised.value.exceptions == (original, interruption)
        else:
            with pytest.raises(KeyboardInterrupt) as raised:
                result.result(3)
            assert raised.value is interruption
    assert owner.snapshot().classes[0].active == 0


def test_interrupted_cleanup_wait_still_waits_then_releases_permit() -> None:
    pressure = PressureClass("stream")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    source = ClosingSource()
    stream = owner.request(pressure).stream(lambda: source)
    interrupted = Event()
    error = KeyboardInterrupt()
    wait = stream._condition.wait

    def interrupt_once(timeout: float | None = None) -> bool:
        if current_thread().name.startswith("closer") and not interrupted.is_set():
            interrupted.set()
            raise error
        return wait(timeout)

    with (
        ThreadPoolExecutor(1, "reader") as executor,
        ThreadPoolExecutor(1, "closer") as closers,
        patch.object(stream._condition, "wait", side_effect=interrupt_once),
    ):
        reader = executor.submit(next, stream)
        try:
            assert source.reading.wait(3)
            closer = closers.submit(stream.close)
            assert interrupted.wait(3)
            assert owner.snapshot().classes[0].active == 1
        finally:
            source.finish_read.set()
            source.wake.set()
        with pytest.raises(KeyboardInterrupt) as raised:
            closer.result(3)
        assert raised.value is error
        with pytest.raises(StopIteration):
            reader.result(3)
    stream.close()
    assert owner.snapshot().classes[0].active == 0
    assert owner.snapshot().classes[0].outcomes.failed == 1
