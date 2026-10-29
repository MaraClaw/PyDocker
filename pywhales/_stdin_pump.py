"""Measured byte input while subprocess communication drains output concurrently."""

import errno
import os
from contextlib import suppress
from subprocess import Popen
from threading import Event, Thread
from types import TracebackType
from typing import Self

from pywhales.telemetry._lifecycle import ExecutionTelemetry


class StdinPump:
    """Own a cancellable writer and accumulate only successful local pipe writes.

    Detach stdin while communicate drains output, preventing it from closing the
    writer's pipe. Restore the closed pipe and join the writer before completion.
    No supplied input leaves stdin and its unavailable measurement unchanged.
    """

    def __init__(
        self, process: Popen[bytes], data: bytes | None, telemetry: ExecutionTelemetry
    ) -> None:
        self._process = process
        self._data = data
        self._telemetry = telemetry
        self._pipe = process.stdin
        self._stop = Event()
        self._thread: Thread | None = None
        self._error: OSError | None = None

    def __enter__(self) -> Self:
        if self._data is None:
            return self
        assert self._pipe is not None
        self._telemetry.stdin_bytes = 0
        try:
            os.set_blocking(self._pipe.fileno(), False)
        except OSError:
            with suppress(ProcessLookupError):
                self._process.kill()
            raise
        self._process.stdin = None
        self._thread = Thread(target=self._write, name="pywhales-stdin", daemon=True)
        self._thread.start()
        return self

    def _write(self) -> None:
        assert self._pipe is not None and self._data is not None
        view = memoryview(self._data)
        offset = 0
        try:
            with self._pipe:
                while offset < len(view) and not self._stop.is_set():
                    try:
                        written = os.write(
                            self._pipe.fileno(), view[offset : offset + 4096]
                        )
                    except BlockingIOError:
                        self._stop.wait(0.01)
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
        self._stop.set()
        if exc_type is not None:
            with suppress(ProcessLookupError):
                self._process.kill()
            self._process.wait()
        if self._thread is not None:
            self._thread.join()
            self._process.stdin = self._pipe
        if exc_type is None and self._error is not None:
            raise self._error
