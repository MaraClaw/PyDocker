"""Deadline ownership for direct binary archive subprocesses."""

import os
from collections.abc import Buffer, Iterator
from contextlib import suppress
from io import BufferedReader, RawIOBase
from subprocess import Popen
from threading import Event, Lock, Thread
from time import monotonic
from typing import IO, override

from pywhales._readiness import bounded_wait, reap, wait_ready
from pywhales.exceptions import DockerTimeout


class ArchiveDeadline:
    """Account for archive pipe I/O and reap an expired local child.

    Counters include successful writes and reads, never submitted buffer sizes.
    The pipe lock serializes caller I/O with watchdog drains. Reap the process
    and join thread before publishing counters; a deadline is optional.

    Pipe waits are readiness waits made under the pipe lock, so the watchdog
    never closes a pipe beneath one. A wait lasts the remaining time to the
    deadline, which is when the watchdog needs the lock to expire the child,
    and has no timer at all without a deadline or after a natural exit.
    """

    def __init__(
        self,
        process: Popen[bytes],
        command: list[str],
        deadline: float | None,
        *,
        raw: bool,
    ) -> None:
        self.process = process
        self.command = command
        self.deadline = deadline
        self.raw = raw
        self.expired = False
        self.stdout: bytes | None = None
        self.stderr: bytes | None = None
        self.stdin_bytes: int | None = None if process.stdin is None else 0
        self.stdout_bytes: int | None = None if process.stdout is None else 0
        self.stderr_bytes: int | None = None if process.stderr is None else 0
        self.released = Event()
        self.lock = Lock()
        self._exited = Event()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and (
                deadline is not None or (pipe is process.stdin and os.name != "nt")
            ):
                os.set_blocking(pipe.fileno(), False)
        self._reaper = (
            None
            if deadline is None
            else Thread(target=reap, args=(process, self._exited.set), daemon=True)
        )
        self.thread = Thread(target=self._watch, daemon=True)
        try:
            if self._reaper is not None:
                self._reaper.start()
            self.thread.start()
        except RuntimeError:
            with suppress(ProcessLookupError):
                process.kill()
            if self._reaper is not None and self._reaper.ident is not None:
                self._reaper.join()
            raise

    def _watch(self) -> None:
        try:
            if self.deadline is None:
                self.process.wait()
                return
            while not self._exited.wait(bounded_wait(self.deadline - monotonic())):
                if monotonic() >= self.deadline:
                    break
            if self._exited.is_set():
                return
            self.expired = True
            with suppress(ProcessLookupError):
                self.process.kill()
            self.process.wait()
            with self.lock:
                self.stdout = self._drain(self.process.stdout)
                self.stderr = self._drain(self.process.stderr)
                for pipe in (
                    self.process.stdin,
                    self.process.stdout,
                    self.process.stderr,
                ):
                    if pipe is not None and not pipe.closed:
                        pipe.close()
        finally:
            if self._reaper is not None:
                self._reaper.join()
            self.released.set()

    def _wait(self, pipe: IO[bytes], *, writable: bool) -> bool:
        """Wait for readiness while holding the pipe lock.

        Returns:
            False if the deadline has passed and the watchdog has yet to decide
            between expiry and a natural exit; the caller then releases the
            lock and waits for that decision.
        """
        timeout = None
        if self.deadline is not None and not self.released.is_set():
            timeout = self.deadline - monotonic()
            if timeout <= 0:
                return False
        wait_ready(pipe.fileno(), writable=writable, timeout=timeout)
        return True

    def _drain(self, pipe: IO[bytes] | None) -> bytes | None:
        if pipe is None or pipe.closed:
            return None
        chunks = []
        while True:
            try:
                chunk = self._read(pipe, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _read(self, pipe: IO[bytes], size: int) -> bytes:
        """Read once under the caller-held lock and account at the pipe boundary."""
        chunk = os.read(pipe.fileno(), size)
        self.record_read(pipe, chunk)
        return chunk

    def record_read(self, pipe: IO[bytes], chunk: bytes) -> None:
        """Account for one pipe-boundary read while holding the lock."""
        if pipe is self.process.stdout:
            assert self.stdout_bytes is not None
            self.stdout_bytes += len(chunk)
        elif pipe is self.process.stderr:
            assert self.stderr_bytes is not None
            self.stderr_bytes += len(chunk)

    def check(self) -> None:
        if self.expired:
            self.released.wait()
            raise DockerTimeout(
                self.command,
                self.process.returncode,
                self.stdout,
                self.stderr,
                raw_diagnostics=self.raw,
            )

    def read(self, pipe: IO[bytes], size: int = 65536) -> bytes:
        while True:
            self.check()
            settle = False
            with self.lock:
                if self.expired:
                    continue
                try:
                    return self._read(pipe, size)
                except BlockingIOError:
                    settle = not self._wait(pipe, writable=False)
            if settle:
                self.released.wait()

    def lines(self, pipe: IO[bytes]) -> Iterator[bytes]:
        """Yield buffered lines, counting raw reads including read-ahead."""
        with BufferedReader(_ArchiveReader(self, pipe)) as reader:
            yield from reader

    def write(self, pipe: IO[bytes], data: bytes) -> None:
        self.check()
        offset = 0
        view = memoryview(data)
        while offset < len(data):
            self.check()
            settle = False
            with self.lock:
                if self.expired:
                    continue
                try:
                    end = offset + 4096 if os.name == "nt" else len(view)
                    written = os.write(pipe.fileno(), view[offset:end])
                except BlockingIOError:
                    settle = not self._wait(pipe, writable=True)
                else:
                    assert self.stdin_bytes is not None
                    self.stdin_bytes += written
                    offset += written
            if settle:
                self.released.wait()


class _ArchiveReader(RawIOBase):
    """Adapt measured reads without taking ownership of the Popen-owned pipe."""

    def __init__(self, owner: ArchiveDeadline, pipe: IO[bytes]) -> None:
        super().__init__()
        self._owner = owner
        self._pipe = pipe

    @override
    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer: Buffer) -> int:
        view = memoryview(buffer)
        chunk = self._owner.read(self._pipe, len(view))
        view[: len(chunk)] = chunk
        return len(chunk)
