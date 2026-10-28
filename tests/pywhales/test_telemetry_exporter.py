import sys
from threading import Event, Thread

import pytest

from pywhales.client_config import ClientConfig
from pywhales.exceptions import DockerException
from pywhales.telemetry import (
    BoundedExporter,
    CompletionRecord,
    LaunchRecord,
    TelemetryRecord,
)
from pywhales.utils import stream_stdout_and_stderr


def test_full_exporter_drops_newest_without_calling_sink() -> None:
    exported: list[TelemetryRecord] = []
    exporter = BoundedExporter(exported.append, capacity=1, drop_policy="drop_newest")
    first, second = (
        LaunchRecord("captured", attempt_id="first"),
        LaunchRecord("stream", attempt_id="second"),
    )

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
    first, second = (
        LaunchRecord("captured", attempt_id="first"),
        LaunchRecord("stream", attempt_id="second"),
    )
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
    exporter(LaunchRecord("captured", attempt_id="first"))

    assert exporter.flush() == 0

    assert exporter.export_errors == 1
    assert exporter.queued == 0
    assert exporter.flush() == exporter.shutdown() == 0
    assert len(calls) == 1


@pytest.mark.parametrize("flush", [False, True])
def test_shutdown_has_explicit_flush_and_drop_ownership(flush: bool) -> None:
    exported: list[TelemetryRecord] = []
    exporter = BoundedExporter(exported.append, capacity=2, drop_policy="drop_newest")
    exporter(LaunchRecord("stream", attempt_id="first"))

    assert exporter.shutdown(flush=flush) == int(flush)
    exporter(LaunchRecord("captured", attempt_id="second"))

    assert exported == ([LaunchRecord("stream", attempt_id="first")] if flush else [])
    assert exporter.dropped == (1 if flush else 2)
    assert exporter.queued == 0
    assert exporter.shutdown() == exporter.flush() == 0


@pytest.mark.parametrize("capacity", [True, False, 0, -1])
def test_exporter_requires_finite_positive_capacity(capacity: int) -> None:
    with pytest.raises(ValueError):
        BoundedExporter(
            lambda record: None, capacity=capacity, drop_policy="drop_newest"
        )


def test_closed_exporter_drops_stream_completion_without_affecting_execution() -> None:
    exported: list[TelemetryRecord] = []
    exporter = BoundedExporter(exported.append, capacity=2, drop_policy="drop_newest")
    config = ClientConfig(client_call=[sys.executable], telemetry_observer=exporter)
    stream = stream_stdout_and_stderr([*config.docker_cmd, "-c", "print('ok')"])
    try:
        assert exporter.shutdown() == 1
        assert list(stream) == [("stdout", b"ok\n")]
        assert exporter.dropped == 1
        assert len(exported) == 1 and isinstance(exported[0], LaunchRecord)
        assert stream._process.returncode == 0
    finally:
        stream.close()


def test_exporter_correlates_overlapping_streams_completed_in_reverse() -> None:
    exported: list[TelemetryRecord] = []
    exporter = BoundedExporter(exported.append, capacity=4, drop_policy="drop_newest")
    config = ClientConfig(client_call=[sys.executable], telemetry_observer=exporter)
    with (
        stream_stdout_and_stderr(
            [*config.docker_cmd, "-c", "input(); raise SystemExit(7)"], pipe_stdin=True
        ) as first,
        stream_stdout_and_stderr(
            [*config.docker_cmd, "-c", "input(); raise SystemExit(3)"], pipe_stdin=True
        ) as second,
    ):
        assert first.stdin is not None and second.stdin is not None
        second.stdin.write(b"go\n")
        second.stdin.flush()
        with pytest.raises(DockerException):
            list(second)
        first.stdin.write(b"go\n")
        first.stdin.flush()
        with pytest.raises(DockerException):
            list(first)
    assert exporter.shutdown() == 4
    assert exporter.dropped == 0
    launches = [record for record in exported if isinstance(record, LaunchRecord)]
    completions = [
        record for record in exported if isinstance(record, CompletionRecord)
    ]
    assert len(launches) == len(completions) == 2
    assert launches[0].attempt_id != launches[1].attempt_id
    assert [record.attempt_id for record in completions] == [
        launches[1].attempt_id,
        launches[0].attempt_id,
    ]
    assert (
        [record.return_code for record in completions]
        == [second._process.returncode, first._process.returncode]
        == [3, 7]
    )
    assert not first._watchdog.is_alive() and not second._watchdog.is_alive()
    assert all(
        not thread.is_alive()
        for stream in (first, second)
        for thread in stream._threads
    )
