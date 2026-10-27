"""Interruptible readiness for captured subprocess pipes."""

import os
import selectors
import time
from collections.abc import Sequence
from types import TracebackType
from typing import IO, Self


class CapturePipes:
    """Own readiness registrations without owning the underlying pipe streams."""

    def __init__(self, pipes: Sequence[IO[bytes]]) -> None:
        self._pipes = {pipe.fileno(): pipe for pipe in pipes}
        self._selector = selectors.DefaultSelector() if os.name != "nt" else None
        if self._selector is not None:
            for fd in self._pipes:
                os.set_blocking(fd, False)
                self._selector.register(fd, selectors.EVENT_READ)

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

    def ready(self, timeout: float) -> list[tuple[int, int]]:
        """Return ready descriptors and their maximum safe read size."""
        if self._selector is not None:
            return [(key.fd, 65536) for key, _ in self._selector.select(timeout)]
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
            time.sleep(timeout)
        return ready

    def __bool__(self) -> bool:
        return bool(self._pipes)
