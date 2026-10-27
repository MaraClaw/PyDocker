"""Process-backed output iteration, deadlines, and local CLI cleanup.

Reader threads and a watchdog keep running independently of stream consumption.
Cleanup controls only the local process, not daemon-managed resources.
"""

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
from pywhales.telemetry import Outcome
from pywhales.telemetry._lifecycle import ExecutionTelemetry


class ProcessStream(Iterator[tuple[str, bytes]]):
    """Owned CLI process with output readers and an independent watchdog.

    Iteration blocks for stdout/stderr records and drains output after natural
    child exit. A launch-based deadline keeps advancing even when iteration
    pauses or never starts. Use a context manager or call close() when stopping
    early; a bare loop break does not close the stream. Closing kills a running
    local CLI and waits for cleanup, never removing daemon-managed resources.
    Recorded terminal errors are delivered once through iteration or clean
    context exit, without replacing an exception from the context body.

    Attributes:
        stdin: Writable child input pipe when launch requested one; otherwise
            ``None``. Cleanup closes this pipe along with the owned process.
    """

    def __init__(
        self,
        process: Popen[bytes],
        full_cmd: list[str],
        *,
        deadline: float | None = None,
        buffer_bytes: int | None = None,
        raw_diagnostics: bool = False,
        telemetry: ExecutionTelemetry | None = None,
    ) -> None:
        """Take ownership of a launched process and start readers and a watchdog.

        Args:
            process: Running child with stdout and stderr pipes already open.
            full_cmd: Launched arguments retained for exception attributes.
            deadline: Absolute monotonic deadline in seconds, or ``None`` for no
                deadline. The caller computes it from launch time.
            buffer_bytes: Positive byte budget, or ``None`` for unbounded output.
                Queued payload, each unfinished record, and stderr history are
                bounded separately; this is not a total-memory cap.
            raw_diagnostics: Whether exception text may expose command and
                captured-output values. Raw exception attributes remain available
                regardless and must be treated as confidential.
            telemetry: Optional launch state completed by consumption or closure.
        """
        self._process = process
        self._full_cmd = full_cmd
        self.stdin = process.stdin
        self._deadline = deadline
        self._buffer_bytes = buffer_bytes
        self._raw_diagnostics = raw_diagnostics
        self._telemetry = telemetry
        self._telemetry_outcome: Outcome | None = None
        self._read_bytes = {"stdout": 0, "stderr": 0}
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
        """Register an owner callback, replaying any already recorded failure.

        Notification does not join reader threads and may happen on a worker
        thread. A newly observed nonzero child exit can also trigger it.

        Args:
            callback: Failure notification that must not wait for reader cleanup.
        """
        with self._queue.condition:
            error = self._error
            if error is None:
                self._failure_callback = callback
        if error is not None:
            callback(error)
        else:
            self._notify_exit_failure()

    def _fail(self, error: OSError | BufferError | DockerException) -> None:
        """Record the first failure, wake blocked workers, and notify the owner.

        Args:
            error: Terminal error retained for later delivery to the consumer.
        """
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
        """Notify a registered owner if the child has exited unsuccessfully."""
        with self._queue.condition:
            exit_code = self._process.poll()
            if exit_code is None or exit_code == 0 or self._failure_callback is None:
                return
            stderr = bytes(self._stderr)
        self._fail(
            get_docker_exception_type(stderr)(
                self._full_cmd,
                exit_code,
                stderr=stderr,
                raw_diagnostics=self._raw_diagnostics,
            )
        )

    def _read_pipe(self, pipe: IO[bytes], name: str) -> None:
        """Read newline-delimited records and publish completion for one pipe.

        Retain a final unterminated record at EOF. Record read or size failures
        outside the payload queue so queue saturation cannot hide them.

        Args:
            pipe: Owned stdout or stderr pipe, closed when this reader finishes.
            name: ``stdout`` or ``stderr`` source label used for accounting.
        """
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
                            if self._telemetry is not None:
                                self._read_bytes[name] += len(chunk)
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
        """Publish a record while tracking bytes waiting for queue capacity.

        Args:
            name: Output source label.
            record: Complete record that already satisfies the record-size bound.

        Returns:
            Whether the record was queued; false if cleanup closed the queue.
        """
        with self._queue.condition:
            self._inflight[name] = record
            try:
                return self._queue.put((name, record))
            finally:
                self._inflight[name] = b""

    def _check_size(self, size: int, category: str) -> None:
        """Enforce an individual record or stderr-history byte limit.

        Args:
            size: Proposed retained byte count, including any newline.
            category: Accounting category included in the failure message.

        Raises:
            BufferError: If the proposed count exceeds a configured budget.
        """
        if self._buffer_bytes is not None and size > self._buffer_bytes:
            raise BufferError(f"{category} exceeds buffer_bytes={self._buffer_bytes}")

    def _expire(self) -> None:
        """Record expiry of a still-running child while the queue lock is held.

        Capture queued and unfinished stdout before closing the queue. Natural
        child exit is not converted into a timeout during post-exit draining.
        """
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
        """Enforce the deadline and reap the child independently of iteration.

        Kill a running child when stopped, join readers, and close piped stdin
        before signalling that owned resources have been released.
        """
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
        """Wait for cleanup and deliver a recorded terminal error at most once.

        Raises:
            DockerTimeout: If the launch deadline expired while the child ran.
            DockerException: If a subprocess failure was recorded.
            BufferError: If retained output exceeded an individual byte limit.
            OSError: If a pipe reader recorded an operating-system failure.
        """
        self._watchdog.join()
        with self._queue.condition:
            error = self._error
            self._error = None
            expired = self._expired
            self._expired = False
            if self._telemetry_outcome is None:
                if expired:
                    self._telemetry_outcome = "timeout"
                elif error is not None:
                    self._telemetry_outcome = "failure"
        self._complete_telemetry()
        if expired:
            raise DockerTimeout(
                self._full_cmd,
                self._process.returncode,
                self._timeout_stdout,
                bytes(self._stderr),
                raw_diagnostics=self._raw_diagnostics,
            )
        if error is not None:
            raise error

    def _complete_telemetry(self) -> None:
        """Publish the selected outcome after cleanup, outside stream locks."""
        if self._telemetry is not None and self._telemetry_outcome is not None:
            self._telemetry.stdout_bytes = self._read_bytes["stdout"]
            self._telemetry.stderr_bytes = self._read_bytes["stderr"]
            self._telemetry.finish(self._telemetry_outcome)

    @override
    def __iter__(self) -> Self:
        """Return this owned output iterator."""
        return self

    @override
    def __next__(self) -> tuple[str, bytes]:
        """Wait for the next output record or deliver terminal status.

        Returns:
            A ``(source, payload)`` pair labelled ``stdout`` or ``stderr``.
            Records include their newline, except an unterminated final record.

        Raises:
            StopIteration: After successful exhaustion or explicit closure with
                no remaining recorded failure.
            DockerException: If the child exits unsuccessfully.
            DockerTimeout: If the running child exceeded its launch deadline.
            BufferError: If a record or retained stderr exceeded its byte limit.
            OSError: If a pipe read failed.
        """
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
            self._telemetry_outcome = "success" if exit_code == 0 else "failure"
        self._complete_telemetry()
        if exit_code != 0:
            stderr = bytes(self._stderr)
            raise get_docker_exception_type(stderr)(
                self._full_cmd,
                exit_code,
                stderr=stderr,
                raw_diagnostics=self._raw_diagnostics,
            )
        raise StopIteration

    def close(self) -> None:
        """Cancel the local CLI and wait for process and reader cleanup.

        Repeated calls are safe. Discard queued payloads but leave recorded
        errors for one later iteration or clean context-exit delivery. This does
        not cancel containers, services, or other daemon-side work.
        """
        with self._queue.condition:
            self._expire()
            if self._telemetry_outcome is None:
                self._telemetry_outcome = (
                    "timeout"
                    if self._expired
                    else "failure"
                    if self._error is not None
                    else "closed"
                )
            self._closed = True
            self._stop.set()
            self._queue.close()
        self._watchdog.join()
        self._complete_telemetry()

    def __enter__(self) -> Self:
        """Return the already-running stream without launching another process."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the stream and surface recorded errors only on clean exit.

        Args:
            exc_type: Active context-body exception type, or ``None``.
            exc_value: Active context-body exception, preserved by this method.
            traceback: Context-body traceback supplied by the context protocol.
        """
        self.close()
        if exc_type is None:
            self._raise_error()
