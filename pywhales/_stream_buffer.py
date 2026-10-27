from collections import deque
from queue import Empty
from threading import Condition
from time import monotonic


class StreamBuffer:
    """Account queued payload independently from terminal notifications."""

    def __init__(self, limit: int | None) -> None:
        self.condition = Condition()
        self._limit = limit
        self._items: deque[tuple[str, bytes] | None] = deque()
        self._bytes = 0
        self._closed = False

    def put(self, item: tuple[str, bytes]) -> bool:
        size = len(item[1])
        with self.condition:
            while (
                not self._closed
                and self._limit is not None
                and self._bytes + size > self._limit
            ):
                self.condition.wait()
            if self._closed:
                return False
            self._items.append(item)
            self._bytes += size
            self.condition.notify_all()
            return True

    def finish(self) -> None:
        with self.condition:
            self._items.append(None)
            self.condition.notify_all()

    def get(self, timeout: float | None = None) -> tuple[str, bytes] | None:
        deadline = None if timeout is None else monotonic() + timeout
        with self.condition:
            while not self._closed and not self._items:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise Empty
                self.condition.wait(remaining)
            if self._closed:
                return None
            item = self._items.popleft()
            if item is not None:
                self._bytes -= len(item[1])
            self.condition.notify_all()
            return item

    def stdout(self) -> bytes:
        with self.condition:
            return b"".join(
                item[1]
                for item in self._items
                if item is not None and item[0] == "stdout"
            )

    def close(self) -> None:
        with self.condition:
            self._closed = True
            self._items.clear()
            self._bytes = 0
            self.condition.notify_all()
