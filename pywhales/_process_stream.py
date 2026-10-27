from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import suppress
from subprocess import Popen
from threading import Event, Thread
from time import monotonic
from types import TracebackType
from typing import IO, Self, override

from pywhales._stream_buffer import StreamBuffer
from pywhales.exceptions import (
    DockerException,
    DockerTimeout,
    get_docker_exception_type,
)


class ProcessStream(Iterator[tuple[str, bytes]]):
    """Own a CLI, bounded output, and one independent cleanup/watchdog thread.

    A bare loop break does not close the stream. Closing only cancels the local
    CLI, never daemon-managed resources. Terminal errors are delivered once.
    """

    def __init__(
        self,
        process: Popen[bytes],
        full_cmd: list[str],
        *,
        deadline: float | None = None,
        buffer_bytes: int | None = None,
    ) -> None:
        self._process = process
        self._full_cmd = full_cmd
        self.stdin = process.stdin
        self._deadline = deadline
        self._buffer_bytes = buffer_bytes
        self._queue = StreamBuffer(buffer_bytes)
        self._stop = Event()
        self._released = Event()
        self._closed = False
        self._expired = False
        self._error: OSError | BufferError | DockerException | None = None
        self._failure_callback: (
            Callable[[OSError | BufferError | DockerException], None] | None
        ) = None
        self._timeout_stdout: bytes | None = None
        self._remaining_readers = 2
        self._stderr = bytearray()
        self._pending = {"stdout": bytearray(), "stderr": bytearray()}
        self._pending_bytes = {"stdout": 0, "stderr": 0}
        self._inflight = {"stdout": b"", "stderr": b""}
        assert process.stdout is not None
        assert process.stderr is not None
        self._threads = tuple(
            Thread(target=self._read_pipe, args=(pipe, name), daemon=True)
            for pipe, name in ((process.stdout, "stdout"), (process.stderr, "stderr"))
        )
        for thread in self._threads:
            thread.start()
        self._watchdog = Thread(target=self._monitor, daemon=True)
        self._watchdog.start()

    def _on_failure(
        self, callback: Callable[[OSError | BufferError | DockerException], None]
    ) -> None:
        """Notify an owner without joining readers, replaying a prior failure."""
        with self._queue.condition:
            error = self._error
            if error is None:
                self._failure_callback = callback
        if error is not None:
            callback(error)
        else:
            self._notify_exit_failure()

    def _fail(self, error: OSError | BufferError | DockerException) -> None:
        with self._queue.condition:
            if self._stop.is_set():
                return
            self._error = error.with_traceback(None)
            callback = self._failure_callback
            self._failure_callback = None
            self._stop.set()
            self._queue.close()
        if callback is not None:
            callback(error)

    def _notify_exit_failure(self) -> None:
        with self._queue.condition:
            exit_code = self._process.poll()
            if exit_code is None or exit_code == 0 or self._failure_callback is None:
                return
            stderr = bytes(self._stderr)
        self._fail(
            get_docker_exception_type(stderr)(self._full_cmd, exit_code, stderr=stderr)
        )

    def _read_pipe(self, pipe: IO[bytes], name: str) -> None:
        pending = self._pending[name]
        chunk = b""
        offset = 0
        try:
            with pipe:
                os.set_blocking(pipe.fileno(), False)
                while not self._stop.is_set():
                    try:
                        with self._queue.condition:
                            if self._stop.is_set():
                                return
                            chunk = os.read(pipe.fileno(), 65536)
                            offset = 0
                    except BlockingIOError:
                        self._stop.wait(0.01)
                        continue
                    if not chunk:
                        if pending:
                            with self._queue.condition:
                                record = bytes(pending)
                                pending.clear()
                                self._pending_bytes[name] = 0
                                self._publish(name, record)
                        break
                    if name == "stderr":
                        with self._queue.condition:
                            self._check_size(
                                len(self._stderr) + len(chunk), "stderr history"
                            )
                            self._stderr.extend(chunk)
                    while offset < len(chunk) and not self._stop.is_set():
                        newline = chunk.find(b"\n", offset)
                        end = len(chunk) if newline < 0 else newline + 1
                        self._check_size(len(pending) + end - offset, "output record")
                        with self._queue.condition:
                            if self._stop.is_set():
                                return
                            pending.extend(chunk[offset:end])
                            offset = end
                            self._pending_bytes[name] = len(pending)
                            self._queue.condition.notify_all()
                            if newline >= 0:
                                record = bytes(pending)
                                pending.clear()
                                self._pending_bytes[name] = 0
                                if not self._publish(name, record):
                                    return
        except (OSError, BufferError) as error:
            self._fail(error)
        finally:
            with self._queue.condition:
                if self._expired and name == "stdout":
                    assert self._timeout_stdout is not None
                    self._timeout_stdout += chunk[offset:]
                pending.clear()
                self._pending_bytes[name] = 0
            self._queue.finish()

    def _publish(self, name: str, record: bytes) -> bool:
        with self._queue.condition:
            self._inflight[name] = record
            try:
                return self._queue.put((name, record))
            finally:
                self._inflight[name] = b""

    def _check_size(self, size: int, category: str) -> None:
        if self._buffer_bytes is not None and size > self._buffer_bytes:
            raise BufferError(f"{category} exceeds buffer_bytes={self._buffer_bytes}")

    def _expire(self) -> None:
        if (
            not self._stop.is_set()
            and self._deadline is not None
            and monotonic() >= self._deadline
            and self._process.poll() is None
        ):
            self._expired = True
            self._timeout_stdout = (
                self._queue.stdout()
                + self._inflight["stdout"]
                + self._pending["stdout"]
            )
            self._stop.set()
            self._queue.close()

    def _monitor(self) -> None:
        try:
            with self._queue.condition:
                while not self._stop.is_set() and self._process.poll() is None:
                    self._expire()
                    if not self._stop.is_set():
                        remaining = (
                            0.01
                            if self._deadline is None
                            else max(0, min(0.01, self._deadline - monotonic()))
                        )
                        self._queue.condition.wait(remaining)
            if self._stop.is_set() and self._process.poll() is None:
                with suppress(ProcessLookupError):
                    self._process.kill()
            self._process.wait()
            self._notify_exit_failure()
            for thread in self._threads:
                thread.join()
            if self.stdin is not None and not self.stdin.closed:
                os.set_blocking(self.stdin.fileno(), False)
                with suppress(BrokenPipeError, BlockingIOError):
                    self.stdin.close()
        finally:
            self._released.set()

    def _raise_error(self) -> None:
        self._watchdog.join()
        with self._queue.condition:
            error = self._error
            self._error = None
            if self._expired:
                self._expired = False
                raise DockerTimeout(
                    self._full_cmd,
                    self._process.returncode,
                    self._timeout_stdout,
                    bytes(self._stderr),
                )
            if error is not None:
                raise error

    @override
    def __iter__(self) -> Self:
        return self

    @override
    def __next__(self) -> tuple[str, bytes]:
        while True:
            if self._stop.is_set():
                self._raise_error()
                raise StopIteration
            item = self._queue.get()
            if self._stop.is_set():
                self._raise_error()
                raise StopIteration
            if item is not None:
                return item
            self._remaining_readers -= 1
            if not self._remaining_readers:
                break
        self._raise_error()
        with self._queue.condition:
            if self._stop.is_set():
                raise StopIteration
            exit_code = self._process.returncode
            self._closed = True
            self._stop.set()
            self._queue.close()
        if exit_code != 0:
            stderr = bytes(self._stderr)
            raise get_docker_exception_type(stderr)(
                self._full_cmd, exit_code, stderr=stderr
            )
        raise StopIteration

    def close(self) -> None:
        """Cancel and release the CLI; leave any recorded error for consumption."""
        with self._queue.condition:
            self._expire()
            self._closed = True
            self._stop.set()
            self._queue.close()
        self._watchdog.join()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
        if exc_type is None:
            self._raise_error()
