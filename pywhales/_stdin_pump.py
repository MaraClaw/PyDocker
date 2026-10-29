"""Measured byte input while subprocess communication drains output concurrently."""

import errno
import os
from contextlib import suppress
from subprocess import Popen
from threading import Thread
from types import TracebackType
from typing import Self

from pywhales._readiness import WakeLatch, wait_ready
from pywhales.telemetry._lifecycle import ExecutionTelemetry


class StdinPump:
    """Own a cancellable writer and accumulate only successful local pipe writes.

    Detach stdin while communicate drains output, preventing it from closing the
    writer's pipe. Restore the closed pipe and join the writer before completion.
    No supplied input leaves stdin and its unavailable measurement unchanged. A
    writer facing a full pipe waits for space or the stop latch, never a timer.
    """

    def __init__(
        self, process: Popen[bytes], data: bytes | None, telemetry: ExecutionTelemetry
    ) -> None:
        self._process = process
        self._data = data
        self._telemetry = telemetry
        self._pipe = process.stdin
        self._stop: WakeLatch | None = None
        self._thread: Thread | None = None
        self._error: OSError | None = None

    def __enter__(self) -> Self:
        if self._data is None:
            return self
        assert self._pipe is not None
        self._telemetry.stdin_bytes = 0
        stop: WakeLatch | None = None
        try:
            stop = WakeLatch()
            os.set_blocking(self._pipe.fileno(), False)
            thread = Thread(
                target=self._write, args=(stop,), name="pywhales-stdin", daemon=True
            )
            thread.start()
        except OSError, RuntimeError:
            if stop is not None:
                stop.close()
            with suppress(ProcessLookupError):
                self._process.kill()
            raise
        self._stop = stop
        self._thread = thread
        self._process.stdin = None
        return self

    def _write(self, stop: WakeLatch) -> None:
        assert self._pipe is not None and self._data is not None
        view = memoryview(self._data)
        offset = 0
        try:
            with self._pipe:
                while offset < len(view) and not stop.is_set():
                    try:
                        written = os.write(
                            self._pipe.fileno(), view[offset : offset + 4096]
                        )
                    except BlockingIOError:
                        wait_ready(self._pipe.fileno(), writable=True, latch=stop)
                        continue
                    offset += written
                    self._telemetry.stdin_bytes = offset
        except BrokenPipeError:
            # Match communicate: a child may close stdin before all input arrives.
            return
        except OSError as error:
            if os.name == "nt" and error.errno == errno.EINVAL:
                return
            self._error = error
            with suppress(ProcessLookupError):
                self._process.kill()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stop is not None:
            self._stop.set()
        try:
            if exc_type is not None:
                with suppress(ProcessLookupError):
                    self._process.kill()
                self._process.wait()
        finally:
            if self._thread is not None:
                self._thread.join()
                self._process.stdin = self._pipe
            if self._stop is not None:
                self._stop.close()
        if exc_type is None and self._error is not None:
            raise self._error
