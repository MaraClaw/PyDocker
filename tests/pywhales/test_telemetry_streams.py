import json
import sys
from dataclasses import asdict
from threading import Event, Thread
from unittest.mock import patch

import pytest

from pywhales.client_config import ClientConfig
from pywhales.exceptions import DockerException, DockerTimeout
from pywhales.telemetry import CompletionRecord, LaunchRecord, TelemetryRecord
from pywhales.utils import MappedProcessStream, stream_stdout_and_stderr


def command(records: list[TelemetryRecord], code: str) -> list[object]:
    config = ClientConfig(
        client_call=[sys.executable], telemetry_observer=records.append
    )
    return [*config.docker_cmd, "-c", code]


def completion(records: list[TelemetryRecord]) -> CompletionRecord:
    assert isinstance(records[0], LaunchRecord)
    assert records[0].family == "stream"
    assert len(records) == 2
    completed = records[1]
    assert isinstance(completed, CompletionRecord)
    assert completed.attempt_id == records[0].attempt_id
    assert completed.stdin_bytes is None
    return completed


def test_stream_completes_on_exhaustion_not_when_child_exits() -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(
        command(records, "import os; os.write(1,b'\\xff\\n'); os.write(2,b'err')")
    )
    try:
        assert stream._released.wait(timeout=3)
        assert len(records) == 1 and isinstance(records[0], LaunchRecord)

        output = list(stream)

        assert sorted(output) == [("stderr", b"err"), ("stdout", b"\xff\n")]
        completed = completion(records)
        assert completed.return_code == stream._process.returncode == 0
        assert (completed.outcome, completed.stdout_bytes, completed.stderr_bytes) == (
            "success",
            2,
            3,
        )
    finally:
        stream.close()
    assert completion(records) is completed


@pytest.mark.parametrize("consume", [False, True])
def test_explicit_close_completes_idle_stream_once(consume: bool) -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(
        command(records, "print('ready',flush=True); input()"), pipe_stdin=True
    )
    try:
        if consume:
            assert next(stream) == ("stdout", b"ready\n")

        stream.close()
        stream.close()

        assert list(stream) == []
        assert completion(records).outcome == "closed"
        assert completion(records).return_code == stream._process.returncode
        assert stream._process.poll() is not None
        assert not stream._watchdog.is_alive()
        assert all(not thread.is_alive() for thread in stream._threads)
    finally:
        stream.close()


def test_natural_nonzero_exit_reports_failure_after_drain() -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(
        command(records, "print('output'); raise SystemExit(7)")
    )

    with pytest.raises(DockerException) as raised:
        list(stream)
    stream.close()

    assert raised.value.return_code == 7
    assert completion(records).outcome == "failure"
    assert completion(records).return_code == stream._process.returncode == 7


def test_close_keeps_recorded_timeout_outcome_and_exception() -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(
        command(records, "input()"), pipe_stdin=True, timeout=0.1
    )
    assert stream._released.wait(timeout=3)

    stream.close()

    with pytest.raises(DockerTimeout):
        next(stream)
    assert completion(records).outcome == "timeout"
    assert completion(records).return_code == stream._process.returncode
    assert list(stream) == []


def test_close_keeps_recorded_buffer_failure_and_measured_read_count() -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(
        command(records, "print('oversized',flush=True); input()"),
        pipe_stdin=True,
        buffer_bytes=3,
    )
    assert stream._released.wait(timeout=3)

    stream.close()

    with pytest.raises(BufferError):
        next(stream)
    completed = completion(records)
    assert (completed.outcome, completed.stdout_bytes) == ("failure", 10)
    assert completed.return_code == stream._process.returncode


def test_throwing_observer_never_changes_original_stream_failure_or_confidentiality() -> (
    None
):
    records: list[TelemetryRecord] = []
    secret = "private-stream-sentinel"

    def throwing(record: TelemetryRecord) -> None:
        records.append(record)
        raise RuntimeError(secret)

    config = ClientConfig(
        client_call=[sys.executable], telemetry_observer=throwing, raw_diagnostics=True
    )
    stream = stream_stdout_and_stderr(
        [*config.docker_cmd, "-c", f"print({secret!r}); raise SystemExit(7)"],
        env={"PRIVATE": secret},
    )

    with pytest.raises(DockerException) as raised, stream:
        assert next(stream) == ("stdout", (secret + "\n").encode())
        next(stream)

    assert raised.value.return_code == 7
    assert completion(records).outcome == "failure"
    assert completion(records).return_code == stream._process.returncode == 7
    assert secret not in json.dumps([asdict(record) for record in records])


def test_mapper_failure_and_body_failure_are_preserved_when_stream_closes() -> None:
    records: list[TelemetryRecord] = []
    child = stream_stdout_and_stderr(
        command(records, "print('ready',flush=True); input()"), pipe_stdin=True
    )
    original = ValueError("consumer-private")

    def fail(record: tuple[str, bytes]) -> bytes:
        raise original

    mapped = MappedProcessStream(child, map(fail, child))

    with pytest.raises(ValueError) as raised, mapped:
        next(mapped)

    assert raised.value is original
    assert completion(records).outcome == "closed"


def test_concurrent_close_emits_one_completion() -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(command(records, "input()"), pipe_stdin=True)
    closers = [Thread(target=stream.close) for _ in range(4)]

    for closer in closers:
        closer.start()
    for closer in closers:
        closer.join(timeout=3)
        assert not closer.is_alive()

    assert completion(records).outcome == "closed"
    assert completion(records).return_code == stream._process.returncode
    assert stream._process.returncode is not None


def test_close_and_read_race_after_watchdog_timeout_delivers_error_once() -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(
        command(records, "input()"), pipe_stdin=True, timeout=0.1
    )
    assert stream._released.wait(timeout=3)
    ready = Event()
    errors: list[DockerTimeout] = []

    def consume() -> None:
        ready.wait()
        try:
            next(stream)
        except DockerTimeout as error:
            errors.append(error)

    def close() -> None:
        ready.wait()
        stream.close()

    workers = [Thread(target=consume), Thread(target=close)]
    for worker in workers:
        worker.start()
    ready.set()
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive()
    assert len(errors) == 1
    assert list(stream) == []
    assert completion(records).outcome == "timeout"
    assert completion(records).return_code == stream._process.returncode


def test_overlapping_streams_correlate_reverse_completion_with_reaped_status() -> None:
    records: list[TelemetryRecord] = []
    first = stream_stdout_and_stderr(
        command(records, "input(); raise SystemExit(7)"), pipe_stdin=True
    )
    second = stream_stdout_and_stderr(
        command(records, "input(); raise SystemExit(3)"), pipe_stdin=True
    )
    try:
        assert first.stdin is not None and second.stdin is not None
        second.stdin.write(b"go\n")
        second.stdin.flush()
        with pytest.raises(DockerException):
            list(second)
        first.stdin.write(b"go\n")
        first.stdin.flush()
        with pytest.raises(DockerException):
            list(first)
        launches = [record for record in records if isinstance(record, LaunchRecord)]
        completed = [
            record for record in records if isinstance(record, CompletionRecord)
        ]
        assert len(launches) == len(completed) == 2
        assert launches[0].attempt_id != launches[1].attempt_id
        assert [record.attempt_id for record in completed] == [
            launches[1].attempt_id,
            launches[0].attempt_id,
        ]
        assert (
            [record.return_code for record in completed]
            == [
                second._process.returncode,
                first._process.returncode,
            ]
            == [3, 7]
        )
    finally:
        first.close()
        second.close()


def test_close_wins_pending_nonzero_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    records: list[TelemetryRecord] = []
    stream = stream_stdout_and_stderr(command(records, "raise SystemExit(7)"))
    reaped, resume = Event(), Event()
    original_raise = stream._raise_error
    errors: list[DockerException] = []

    def paused() -> None:
        original_raise()
        reaped.set()
        assert resume.wait(timeout=3)

    def consume() -> None:
        try:
            list(stream)
        except DockerException as error:
            errors.append(error)

    monkeypatch.setattr(stream, "_raise_error", paused)
    consumer = Thread(target=consume)
    consumer.start()
    try:
        assert reaped.wait(timeout=3)
        stream.close()
        resume.set()
        consumer.join(timeout=3)
        assert not consumer.is_alive()
        assert errors == []
        assert completion(records).outcome == "closed"
        assert completion(records).return_code == stream._process.returncode == 7
    finally:
        resume.set()
        consumer.join(timeout=3)
        stream.close()


def test_stream_spawn_failure_preserves_original_error() -> None:
    records: list[TelemetryRecord] = []
    original = OSError("private-executable")
    with (
        patch("pywhales.utils.Popen", side_effect=original),
        pytest.raises(OSError) as raised,
    ):
        stream_stdout_and_stderr(command(records, "pass"))

    assert raised.value is original
    completed = completion(records)
    assert completed.return_code is None
    assert (completed.outcome, completed.stdout_bytes, completed.stderr_bytes) == (
        "failure",
        None,
        None,
    )


def test_policy_snapshot_survives_mutation_during_stream_lifetime() -> None:
    original: list[TelemetryRecord] = []
    fresh: list[TelemetryRecord] = []
    config = ClientConfig(
        client_call=[sys.executable], telemetry_observer=original.append
    )
    stream = stream_stdout_and_stderr([*config.docker_cmd, "-c", "pass"])
    config.telemetry_observer = fresh.append

    with stream:
        assert list(stream) == []

    assert completion(original).outcome == "success"
    assert fresh == []
