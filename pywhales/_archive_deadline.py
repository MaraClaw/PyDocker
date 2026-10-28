"""Deadline ownership for direct binary archive subprocesses."""

import os
import subprocess
from contextlib import suppress
from subprocess import Popen
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import IO

from pywhales.exceptions import DockerTimeout


class ArchiveDeadline:
    """Reap an expired archive child independently of its caller's iterator."""

    def __init__(
        self, process: Popen[bytes], command: list[str], deadline: float, *, raw: bool
    ) -> None:
        self.process = process
        self.command = command
        self.deadline = deadline
        self.raw = raw
        self.expired = False
        self.stdout: bytes | None = None
        self.stderr: bytes | None = None
        self.released = Event()
        self.lock = Lock()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                os.set_blocking(pipe.fileno(), False)
        self.thread = Thread(target=self._watch, daemon=True)
        self.thread.start()

    def _watch(self) -> None:
        try:
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
                chunk = os.read(pipe.fileno(), 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

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
                    return os.read(pipe.fileno(), size)
                except BlockingIOError:
                    pass
            sleep(0.01)

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
            offset += written
            if not written:
                sleep(0.01)
