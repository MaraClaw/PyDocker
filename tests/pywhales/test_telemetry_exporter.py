from threading import Event, Thread

import pytest

from pywhales.telemetry import BoundedExporter, LaunchRecord, TelemetryRecord


def test_full_exporter_drops_newest_without_calling_sink() -> None:
    exported: list[TelemetryRecord] = []
    exporter = BoundedExporter(exported.append, capacity=1, drop_policy="drop_newest")
    first, second = LaunchRecord("captured"), LaunchRecord("stream")

    exporter(first)
    exporter(second)

    assert (exporter.queued, exporter.dropped, exported) == (1, 1, [])
    assert exporter.flush() == 1
    assert exported == [first]
    assert exporter.shutdown() == 0


def test_flush_is_finite_while_sink_blocks_and_new_records_arrive() -> None:
    entered, resume = Event(), Event()
    exported: list[TelemetryRecord] = []

    def sink(record: TelemetryRecord) -> None:
        entered.set()
        assert resume.wait(timeout=3)
        exported.append(record)

    exporter = BoundedExporter(sink, capacity=1, drop_policy="drop_newest")
    first, second = LaunchRecord("captured"), LaunchRecord("stream")
    exporter(first)
    flusher = Thread(target=exporter.flush)
    flusher.start()
    try:
        assert entered.wait(timeout=3)

        exporter(second)
        exporter(first)

        assert (exporter.queued, exporter.dropped) == (1, 1)
    finally:
        resume.set()
        flusher.join(timeout=3)
        assert not flusher.is_alive()
    assert exported == [first]
    assert exporter.shutdown() == 1
    assert exported == [first, second]


def test_sink_errors_are_counted_and_never_retried_or_raised() -> None:
    calls: list[TelemetryRecord] = []

    def failing(record: TelemetryRecord) -> None:
        calls.append(record)
        raise RuntimeError("private-export-error")

    exporter = BoundedExporter(failing, capacity=2, drop_policy="drop_newest")
    exporter(LaunchRecord("captured"))

    assert exporter.flush() == 0

    assert exporter.export_errors == 1
    assert exporter.queued == 0
    assert exporter.flush() == exporter.shutdown() == 0
    assert len(calls) == 1


@pytest.mark.parametrize("flush", [False, True])
def test_shutdown_has_explicit_flush_and_drop_ownership(flush: bool) -> None:
    exported: list[TelemetryRecord] = []
    exporter = BoundedExporter(exported.append, capacity=2, drop_policy="drop_newest")
    exporter(LaunchRecord("stream"))

    assert exporter.shutdown(flush=flush) == int(flush)
    exporter(LaunchRecord("captured"))

    assert exported == ([LaunchRecord("stream")] if flush else [])
    assert exporter.dropped == (1 if flush else 2)
    assert exporter.queued == 0
    assert exporter.shutdown() == exporter.flush() == 0


@pytest.mark.parametrize("capacity", [True, False, 0, -1])
def test_exporter_requires_finite_positive_capacity(capacity: int) -> None:
    with pytest.raises(ValueError):
        BoundedExporter(
            lambda record: None, capacity=capacity, drop_policy="drop_newest"
        )
