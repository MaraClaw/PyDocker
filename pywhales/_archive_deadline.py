"""Deadline ownership for direct binary archive subprocesses."""

import os
import subprocess
from collections.abc import Buffer, Iterator
from contextlib import suppress
from io import BufferedReader, RawIOBase
from select import select
from subprocess import Popen
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import IO, override

from pywhales.exceptions import DockerTimeout


class ArchiveDeadline:
    """Account for archive pipe I/O and reap an expired local child.

    Counters include successful writes and reads, never submitted buffer sizes.
    The pipe lock serializes caller I/O with watchdog drains. Reap the process
    and join thread before publishing counters; a deadline is optional.
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
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and (
                deadline is not None or (pipe is process.stdin and os.name != "nt")
            ):
                os.set_blocking(pipe.fileno(), False)
        self.thread = Thread(target=self._watch, daemon=True)
        self.thread.start()

    def _watch(self) -> None:
        try:
            if self.deadline is None:
                self.process.wait()
                return
            while True:
                remaining = self.deadline - monotonic()
                try:
                    self.process.wait(timeout=max(0, min(86400, remaining)))
                    return
                except subprocess.TimeoutExpired:
                    if monotonic() < self.deadline:
                        continue
                    if self.process.poll() is not None:
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
                    return
        finally:
            self.released.set()

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
            with self.lock:
                if self.expired:
                    continue
                try:
                    return self._read(pipe, size)
                except BlockingIOError:
                    pass
            sleep(0.01)

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
            written = 0
            with self.lock:
                if self.expired:
                    continue
                with suppress(BlockingIOError):
                    end = offset + 4096 if os.name == "nt" else len(view)
                    written = os.write(pipe.fileno(), view[offset:end])
                    assert self.stdin_bytes is not None
                    self.stdin_bytes += written
            offset += written
            if not written:
                if self.deadline is None:
                    select([], [pipe], [])
                else:
                    sleep(0.01)


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
