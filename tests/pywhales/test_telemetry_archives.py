import json
import os
import sys
from collections.abc import Buffer, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from pywhales import DockerClient, _archive_deadline
from pywhales.exceptions import DockerException, DockerTimeout, NoSuchImage
from pywhales.telemetry import CompletionRecord, LaunchRecord, TelemetryRecord


def completion(records: list[TelemetryRecord]) -> CompletionRecord:
    assert len(records) == 2
    launch, completed = records
    assert isinstance(launch, LaunchRecord)
    assert isinstance(completed, CompletionRecord)
    assert launch.attempt_id == completed.attempt_id
    assert launch.family == completed.family
    return completed


@pytest.mark.parametrize("timeout", [None, 3.0])
def test_direct_archive_load_counts_accepted_short_writes(
    timeout: float | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records: list[TelemetryRecord] = []
    code = "import sys; data=sys.stdin.buffer.read(); print('Loaded image: '+str(len(data)))"
    client = DockerClient(
        client_call=[sys.executable, "-c", code],
        default_timeout=timeout,
        telemetry_observer=records.append,
    )
    payload = b"private-archive" * 100
    written: list[int] = []
    write = os.write

    def short_write(fd: int, data: Buffer) -> int:
        count = write(fd, memoryview(data)[:7])
        written.append(count)
        return count

    monkeypatch.setattr(_archive_deadline.os, "write", short_write)

    assert client.image.load(iter((payload[:17], payload[17:]))) == [str(len(payload))]

    completed = completion(records)
    assert (completed.family, completed.outcome, completed.return_code) == (
        "archive_load",
        "success",
        0,
    )
    assert completed.stdin_bytes == sum(written) == len(payload)
    assert (completed.stdout_bytes, completed.stderr_bytes) == (19, None)
    assert "private-archive" not in json.dumps([asdict(record) for record in records])


@pytest.mark.parametrize("timeout", [None, 3.0])
def test_direct_archive_save_is_lazy_and_counts_binary_reads(
    timeout: float | None,
) -> None:
    records: list[TelemetryRecord] = []
    payload = b"\x00\xff\n" * 10000
    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            "import os; os.write(1,b'\\x00\\xff\\n'*10000)",
        ],
        default_timeout=timeout,
        telemetry_observer=records.append,
    )
    with patch.object(client.image, "inspect", return_value=[]):
        archive = client.image.save("private-image")
    assert records == []

    assert b"".join(archive) == payload

    completed = completion(records)
    assert (completed.family, completed.outcome, completed.return_code) == (
        "archive_save",
        "success",
        0,
    )
    assert (completed.stdin_bytes, completed.stdout_bytes, completed.stderr_bytes) == (
        None,
        len(payload),
        0,
    )
    assert "private-image" not in json.dumps([asdict(record) for record in records])


@pytest.mark.parametrize("save", [False, True])
def test_archive_spawn_failure_has_one_unknown_completion(
    save: bool, tmp_path: Path
) -> None:
    records: list[TelemetryRecord] = []
    client = DockerClient(
        client_call=[sys.executable], telemetry_observer=records.append
    )
    client.client_config._client_call_with_path = [str(tmp_path / "missing-cli")]

    with (
        patch.object(client.image, "inspect", return_value=[]),
        pytest.raises(FileNotFoundError),
    ):
        if save:
            list(client.image.save("private-image"))
        else:
            client.image.load(b"private-input")

    completed = completion(records)
    assert completed.family == ("archive_save" if save else "archive_load")
    assert completed.outcome == "failure"
    assert (
        completed.stdin_bytes,
        completed.stdout_bytes,
        completed.stderr_bytes,
        completed.return_code,
    ) == (None, None, None, None)


@pytest.mark.parametrize("save", [False, True])
def test_filename_archives_keep_one_existing_native_telemetry_path(
    save: bool, tmp_path: Path
) -> None:
    # Given a filename operation with its existing captured or stream transport.
    records: list[TelemetryRecord] = []
    client = DockerClient(
        client_call=[sys.executable, "-c", "print('Loaded image: ok')"],
        telemetry_observer=records.append,
    )

    # When that operation completes through the public API.
    with patch.object(client.image, "inspect", return_value=[]):
        if save:
            assert client.image.save("sample", output=tmp_path / "archive.tar") is None
        else:
            assert client.image.load(tmp_path / "archive.tar", quiet=True) == ["ok"]

    # Then there is exactly one pair in the original execution family.
    completed = completion(records)
    assert (completed.family, completed.outcome, completed.return_code) == (
        "captured" if save else "stream",
        "success",
        0,
    )


@pytest.mark.parametrize("save", [False, True])
def test_archive_natural_failure_keeps_original_error_and_counts(save: bool) -> None:
    records: list[TelemetryRecord] = []
    payload = b"private-payload\n"
    code = (
        "import sys; sys.stdin.buffer.read(); sys.exit(7)"
        if not save
        else f"import os; os.write(1,{payload!r}); os.write(2,b'No such image: private'); os._exit(7)"
    )
    client = DockerClient(
        client_call=[sys.executable, "-c", code], telemetry_observer=records.append
    )

    with (
        patch.object(client.image, "inspect", return_value=[]),
        pytest.raises(NoSuchImage if save else DockerException) as raised,
    ):
        if save:
            list(client.image.save("private-image"))
        else:
            client.image.load(payload)

    completed = completion(records)
    assert (completed.outcome, completed.return_code) == ("failure", 7)
    assert (completed.stdin_bytes, completed.stdout_bytes, completed.stderr_bytes) == (
        (None, len(payload), 22) if save else (len(payload), 0, None)
    )
    assert raised.value.stdout is None
    assert raised.value.stderr == ("No such image: private" if save else None)


def test_archive_load_timeout_counts_partial_input() -> None:
    records: list[TelemetryRecord] = []
    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            "import threading; threading.Event().wait()",
        ],
        default_timeout=0.3,
        telemetry_observer=records.append,
    )
    payload = b"x" * (2 * 1024 * 1024)

    with pytest.raises(DockerTimeout):
        client.image.load(payload)

    completed = completion(records)
    assert completed.stdin_bytes is not None and 0 < completed.stdin_bytes < len(
        payload
    )
    assert completed.outcome == "timeout" and completed.return_code is not None


@pytest.mark.parametrize("timeout", [None, 3.0])
def test_archive_load_early_exit_counts_only_successful_pipe_writes(
    timeout: float | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a child that exits after consuming only one byte of a large archive.
    records: list[TelemetryRecord] = []
    client = DockerClient(
        client_call=[sys.executable, "-c", "import os; os.read(0,1); os._exit(7)"],
        default_timeout=timeout,
        telemetry_observer=records.append,
    )
    payload = b"x" * (2 * 1024 * 1024)
    written: list[int] = []
    write = os.write

    def measure(fd: int, data: Buffer) -> int:
        count = write(fd, data)
        written.append(count)
        return count

    monkeypatch.setattr(_archive_deadline.os, "write", measure)

    # When the archive's remaining writes encounter the closed pipe.
    with pytest.raises(BrokenPipeError):
        client.image.load(payload)

    # Then only accepted bytes are reported, after the child has been reaped.
    completed = completion(records)
    assert completed.stdin_bytes == sum(written)
    assert 0 < sum(written) < len(payload)
    assert (completed.outcome, completed.return_code) == ("failure", 7)


def test_archive_load_producer_failure_keeps_progress_and_original_error() -> None:
    records: list[TelemetryRecord] = []
    client = DockerClient(
        client_call=[sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        telemetry_observer=records.append,
    )
    original = ValueError("private-producer-error")

    def chunks() -> Generator[bytes]:
        yield b"accepted"
        raise original

    with pytest.raises(ValueError) as raised:
        client.image.load(chunks())

    completed = completion(records)
    assert raised.value is original
    assert (completed.stdin_bytes, completed.outcome) == (8, "failure")
    assert completed.return_code is not None


def test_archive_observers_are_isolated_and_snapshotted_at_launch() -> None:
    records: list[list[TelemetryRecord]] = [[], []]
    later: list[TelemetryRecord] = []
    clients = [
        DockerClient(
            client_call=[
                sys.executable,
                "-c",
                "import sys; sys.stdin.buffer.read(); print('Loaded image: ok')",
            ],
            telemetry_observer=observer.append,
        )
        for observer in records
    ]

    def load(client: DockerClient) -> list[str]:
        def chunks() -> Generator[bytes]:
            client.client_config.telemetry_observer = later.append
            yield b"archive"

        return client.image.load(chunks())

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(load, clients)) == [["ok"], ["ok"]]

    assert later == []
    first, second = (completion(observer) for observer in records)
    assert first.attempt_id != second.attempt_id
    assert first.stdin_bytes == second.stdin_bytes == 7
