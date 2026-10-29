import os
import select
import signal
import sys
import threading
from collections import Counter
from collections.abc import Buffer
from subprocess import PIPE, Popen
from threading import Thread
from time import monotonic, sleep
from typing import override

import pytest

from pywhales import _archive_deadline
from pywhales._archive_deadline import ArchiveDeadline
from pywhales.exceptions import DockerTimeout

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX readiness waits")

_HOLDER = (
    "import os,sys\n"
    "release,status=map(int,sys.argv[1:3])\n"
    "if os.fork()==0:\n"
    " os.write(status,b'R'); os.read(release,1); os._exit(0)\n"
)


def test_deadline_read_waits_for_the_remaining_time_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a silent child and a deadline for the owner.
    attempts: Counter[int] = Counter()
    read = os.read

    def counted(fd: int, size: int) -> bytes:
        attempts[fd] += 1
        return read(fd, size)

    monkeypatch.setattr(os, "read", counted)
    command = [sys.executable, "-c", "import threading; threading.Event().wait()"]
    before = set(threading.enumerate())
    with Popen(command, stdout=PIPE, bufsize=0) as process:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        owner = ArchiveDeadline(process, command, monotonic() + 0.8, raw=False)
        started = monotonic()

        # When a read waits for output that never comes.
        with pytest.raises(DockerTimeout):
            owner.read(process.stdout)

        # Then it waited out the deadline in one wait and the owner cleaned up.
        assert 0.6 < monotonic() - started < 3
        assert attempts[fd] <= 3
        owner.thread.join(timeout=3)
        assert owner.released.is_set() and process.returncode is not None
        assert process.stdout.closed
    assert set(threading.enumerate()) == before


def test_deadline_write_waits_for_the_remaining_time_with_exact_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked: Counter[int] = Counter()
    accepted: Counter[int] = Counter()
    write = os.write

    def counted(fd: int, data: Buffer) -> int:
        try:
            count = write(fd, data)
        except BlockingIOError:
            blocked[fd] += 1
            raise
        accepted[fd] += count
        return count

    monkeypatch.setattr(os, "write", counted)
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    with Popen(command, stdin=PIPE, bufsize=0) as process:
        assert process.stdin is not None
        fd = process.stdin.fileno()
        owner = ArchiveDeadline(process, command, monotonic() + 0.8, raw=False)
        started = monotonic()

        with pytest.raises(DockerTimeout):
            owner.write(process.stdin, b"x" * (8 * 1024 * 1024))

        assert 0.6 < monotonic() - started < 3
        assert 0 < blocked[fd] <= 3
        assert owner.stdin_bytes == accepted[fd] > 0
        owner.thread.join(timeout=3)
        assert process.returncode == -signal.SIGKILL and process.stdin.closed


def test_deadline_stops_a_read_while_a_descendant_holds_the_pipe() -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    command = [
        sys.executable,
        "-c",
        _HOLDER + "import time; time.sleep(60)\n",
        str(release_read),
        str(status_write),
    ]
    try:
        with Popen(
            command, stdout=PIPE, bufsize=0, pass_fds=(release_read, status_write)
        ) as process:
            assert process.stdout is not None
            owner = ArchiveDeadline(process, command, monotonic() + 0.5, raw=False)
            assert select.select([status_read], [], [], 3)[0]
            assert os.read(status_read, 1) == b"R"

            outcome: list[bytes | BaseException] = []

            def consume() -> None:
                assert process.stdout is not None
                try:
                    outcome.append(owner.read(process.stdout))
                except DockerTimeout as error:
                    outcome.append(error)

            reader = Thread(target=consume, daemon=True)
            reader.start()
            reader.join(timeout=3)

            assert not reader.is_alive(), "read waited for the descendant"
            assert [type(item) for item in outcome] == [DockerTimeout]
            owner.thread.join(timeout=3)
            assert not owner.thread.is_alive() and process.stdout.closed
    finally:
        os.write(release_write, b"x")
        for descriptor in (release_read, release_write, status_write):
            os.close(descriptor)
        assert os.read(status_read, 1) == b""
        os.close(status_read)


def test_read_after_natural_exit_ignores_the_passed_deadline_without_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: Counter[int] = Counter()
    read = os.read

    def counted(fd: int, size: int) -> bytes:
        attempts[fd] += 1
        return read(fd, size)

    monkeypatch.setattr(os, "read", counted)
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    command = [
        sys.executable,
        "-c",
        _HOLDER + "os._exit(0)\n",
        str(release_read),
        str(status_write),
    ]
    outcome: list[bytes | BaseException] = []
    try:
        with Popen(
            command, stdout=PIPE, bufsize=0, pass_fds=(release_read, status_write)
        ) as process:
            assert process.stdout is not None
            fd = process.stdout.fileno()
            owner = ArchiveDeadline(process, command, monotonic() + 0.3, raw=False)

            def consume() -> None:
                assert process.stdout is not None
                try:
                    outcome.append(owner.read(process.stdout))
                except DockerTimeout as error:
                    outcome.append(error)

            reader = Thread(target=consume, daemon=True)
            reader.start()
            assert select.select([status_read], [], [], 3)[0]
            assert os.read(status_read, 1) == b"R"

            # When the deadline passes after the child exited on its own.
            sleep(0.8)

            # Then the read keeps waiting for the descendant, untimed.
            assert reader.is_alive() and not outcome and not owner.expired
            assert attempts[fd] <= 3

            # And reaches EOF once the descendant lets go.
            os.write(release_write, b"x")
            reader.join(timeout=3)
            assert outcome == [b""]
            assert not owner.expired
    finally:
        os.write(release_write, b"x")
        for descriptor in (release_read, release_write, status_write):
            os.close(descriptor)
        assert os.read(status_read, 1) == b""
        os.close(status_read)


@pytest.mark.parametrize("fail_at", [1, 2])
def test_owner_start_failure_kills_the_child_and_joins_its_helper(
    fail_at: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = 0

    class FailingThread(Thread):
        @override
        def start(self) -> None:
            nonlocal starts
            starts += 1
            if starts == fail_at:
                raise RuntimeError("can't start new thread")
            super().start()

    monkeypatch.setattr(_archive_deadline, "Thread", FailingThread)
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    before = set(threading.enumerate())
    with Popen(command, stdout=PIPE, bufsize=0) as process:
        with pytest.raises(RuntimeError):
            ArchiveDeadline(process, command, monotonic() + 30, raw=False)

        assert process.wait(timeout=3) == -signal.SIGKILL
    assert set(threading.enumerate()) == before
