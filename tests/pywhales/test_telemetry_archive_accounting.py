import os
import sys
import time
from collections.abc import Buffer
from contextlib import suppress
from subprocess import PIPE, Popen
from time import monotonic

import pytest

from pywhales import DockerClient, _archive_deadline
from pywhales._archive_deadline import ArchiveDeadline
from pywhales.exceptions import DockerTimeout


def test_default_archive_load_waits_for_backpressure_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given enough input to require pipe backpressure and a real draining child.
    payload = b"x" * (1024 * 1024)
    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            "import sys; print('Loaded image: '+str(len(sys.stdin.buffer.read())))",
        ]
    )

    naps: list[float] = []
    monkeypatch.setattr(time, "sleep", naps.append)

    # When loading without a selected deadline.
    result = client.image.load(payload)

    # Then writes wait for readiness, not fixed-interval EAGAIN polling.
    assert result == [str(len(payload))]
    assert naps == []


@pytest.mark.parametrize("timeout", [None, 3.0])
def test_archive_accounts_successful_short_writes_and_reads(
    timeout: float | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    write = os.write
    written: list[int] = []

    def short_write(fd: int, data: Buffer) -> int:
        count = write(fd, memoryview(data)[:13])
        written.append(count)
        return count

    monkeypatch.setattr(_archive_deadline.os, "write", short_write)
    code = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)"
    command = [sys.executable, "-c", code]
    payload = b"archive" * 100
    with Popen(command, stdin=PIPE, stdout=PIPE, bufsize=0) as process:
        owner = ArchiveDeadline(
            process,
            command,
            None if timeout is None else monotonic() + timeout,
            raw=False,
        )
        try:
            assert process.stdin is not None and process.stdout is not None
            owner.write(process.stdin, payload)
            process.stdin.close()
            chunks = []
            while chunk := owner.read(process.stdout, 17):
                chunks.append(chunk)
            process.wait(timeout=3)
            owner.thread.join(timeout=3)

            assert b"".join(chunks) == payload
            assert owner.stdin_bytes == sum(written) == len(payload)
            assert (owner.stdout_bytes, owner.stderr_bytes) == (len(payload), None)
            assert not owner.thread.is_alive()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            owner.thread.join(timeout=3)


def test_watchdog_counts_only_unread_pipe_bytes_after_consumer_reads() -> None:
    code = (
        "import os,threading; os.write(1,b'first'); os.read(0,1); "
        "os.write(1,b'remaining'); os.write(2,b'error'); threading.Event().wait()"
    )
    command = [sys.executable, "-c", code]
    with Popen(command, stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=0) as process:
        owner = ArchiveDeadline(process, command, monotonic() + 0.5, raw=False)
        try:
            assert (
                process.stdin is not None
                and process.stdout is not None
                and process.stderr is not None
            )
            assert owner.read(process.stdout, 5) == b"first"
            owner.write(process.stdin, b"go")
            assert owner.read(process.stderr, 1) == b"e"

            assert owner.released.wait(timeout=3)
            owner.thread.join(timeout=3)

            assert (owner.stdin_bytes, owner.stdout_bytes, owner.stderr_bytes) == (
                2,
                14,
                5,
            )
            assert not owner.thread.is_alive() and process.returncode is not None
            for _ in range(2):
                with pytest.raises(DockerTimeout) as raised:
                    owner.check()
                assert (raised.value.stdout, raised.value.stderr) == (
                    "remaining",
                    "rror",
                )
                assert (owner.stdout_bytes, owner.stderr_bytes) == (14, 5)
            assert all(
                pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)
            )
        finally:
            with suppress(ProcessLookupError):
                process.kill()
            process.wait()
            owner.thread.join(timeout=3)
