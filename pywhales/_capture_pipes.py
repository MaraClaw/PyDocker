"""Interruptible readiness for captured subprocess pipes and child exit."""

import os
import selectors
import time
from collections.abc import Sequence
from types import TracebackType
from typing import IO, Self

from pywhales._readiness import POLL_SLICE, WakeLatch, bounded_wait


class CapturePipes:
    """Own readiness registrations without owning the underlying pipe streams.

    The child-exit latch is registered beside the pipes, so a wait ends when the
    child exits even after both outputs reached EOF. Once the latch is set its
    registration is dropped; otherwise it would stay readable and spin the wait
    while a descendant keeps an output pipe open.
    """

    def __init__(self, pipes: Sequence[IO[bytes]], exited: WakeLatch) -> None:
        self._pipes = {pipe.fileno(): pipe for pipe in pipes}
        self._exited = exited
        self._selector = selectors.DefaultSelector() if os.name != "nt" else None
        self._exit_fd = exited.fileno()
        if self._selector is not None:
            for fd in self._pipes:
                os.set_blocking(fd, False)
                self._selector.register(fd, selectors.EVENT_READ)
            if self._exit_fd is not None:
                self._selector.register(self._exit_fd, selectors.EVENT_READ)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._selector is not None:
            self._selector.close()

    def discard(self, fd: int) -> None:
        if self._selector is not None:
            self._selector.unregister(fd)
        del self._pipes[fd]

    def ready(self, timeout: float | None) -> list[tuple[int, int]]:
        """Return ready descriptors and their maximum safe read size.

        Args:
            timeout: Longest wait in seconds, or ``None`` to wait for readiness
                or child exit without a timer. Windows polls in bounded slices.
        """
        if self._selector is not None:
            events = self._selector.select(bounded_wait(timeout))
            if self._exit_fd is not None and self._exited.is_set():
                self._selector.unregister(self._exit_fd)
                self._exit_fd = None
            return [(key.fd, 65536) for key, _ in events if key.fd in self._pipes]
        import importlib
        from ctypes import wintypes

        ctypes = importlib.import_module("ctypes")
        msvcrt = importlib.import_module("msvcrt")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        peek.restype = wintypes.BOOL
        ready: list[tuple[int, int]] = []
        for fd in self._pipes:
            available = wintypes.DWORD()
            success = peek(
                msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(available), None
            )
            if not success:
                error = ctypes.get_last_error()
                if error != 109:
                    raise OSError(error, "Pipe readiness check failed")
                ready.append((fd, 0))
            elif available.value:
                ready.append((fd, available.value))
        if not ready:
            time.sleep(POLL_SLICE if timeout is None else min(timeout, POLL_SLICE))
        return ready

    def __bool__(self) -> bool:
        return bool(self._pipes)
