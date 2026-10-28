import os
import sys
from types import GeneratorType
from unittest.mock import patch

import pytest

from pywhales import DockerClient, _archive_deadline
from pywhales.telemetry import CompletionRecord, TelemetryRecord


def test_default_save_reads_long_lines_in_bulk_without_changing_framing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given long binary lines that must not be read one syscall per byte.
    records: list[TelemetryRecord] = []
    payload = b"x" * (128 * 1024) + b"\n\x00\xff\nlast"
    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            r"import sys; sys.stdout.buffer.write(b'x'*(128*1024)+b'\n\x00\xff\nlast')",
        ],
        telemetry_observer=records.append,
    )
    read = os.read
    sizes: list[int] = []

    def measure(fd: int, size: int) -> bytes:
        chunk = read(fd, size)
        sizes.append(len(chunk))
        return chunk

    monkeypatch.setattr(_archive_deadline.os, "read", measure)

    # When consuming the lazy save through its public API.
    with patch.object(client.image, "inspect", return_value=[]):
        result = list(client.image.save("sample"))

    # Then raw reads are bulk-sized and counted exactly once, preserving lines.
    assert result == [b"x" * (128 * 1024) + b"\n", b"\x00\xff\n", b"last"]
    assert sum(sizes) == len(payload)
    assert len(sizes) < len(payload) // 100
    completed = records[-1]
    assert isinstance(completed, CompletionRecord)
    assert completed.stdout_bytes == len(payload)


def test_save_close_counts_buffered_read_ahead_not_only_yielded_bytes() -> None:
    # Given several lines written atomically before the first pipe read.
    records: list[TelemetryRecord] = []
    payload = b"first\nremaining\n"
    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            f"import os,threading; os.write(1,{payload!r}); threading.Event().wait()",
        ],
        telemetry_observer=records.append,
    )
    with patch.object(client.image, "inspect", return_value=[]):
        archive = client.image.save("sample")
    assert isinstance(archive, GeneratorType)
    try:
        # When closing after yielding only the first buffered line.
        assert next(archive) == b"first\n"
    finally:
        archive.close()

    # Then telemetry includes every byte actually read, including read-ahead.
    completed = records[-1]
    assert isinstance(completed, CompletionRecord)
    assert (completed.outcome, completed.stdout_bytes) == ("closed", len(payload))
