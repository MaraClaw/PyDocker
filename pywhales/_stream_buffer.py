"""Thread-safe stream queue with shared payload-byte accounting.

Completion notifications do not consume the payload budget. Terminal failures
are managed by the owning process stream rather than inserted into this queue.
"""

from collections import deque
from queue import Empty
from threading import Condition
from time import monotonic


class StreamBuffer:
    """Blocking output queue with payload accounting and wake-on-close behavior.

    Producers share one queued-payload budget across output sources. The owner
    must bound individual records before insertion; this queue only waits for
    capacity and does not reject oversized records itself.

    Attributes:
        condition: Shared reentrant condition used by the queue and its owner
            to coordinate payload, deadline, and cleanup state.
    """

    def __init__(self, limit: int | None) -> None:
        """Create an empty output queue.

        Args:
            limit: Positive queued-payload budget in bytes, or ``None`` for no
                bound. The caller validates this value before construction.
        """
        self.condition = Condition()
        self._limit = limit
        self._items: deque[tuple[str, bytes] | None] = deque()
        self._bytes = 0
        self._closed = False

    def put(self, item: tuple[str, bytes]) -> bool:
        """Wait for payload capacity and enqueue a record unless closed.

        Args:
            item: Source-labelled payload whose size fits any configured limit.

        Returns:
            True when queued, or false if closure interrupts the insertion.
        """
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
        """Enqueue one reader-completion marker without charging payload bytes."""
        with self.condition:
            self._items.append(None)
            self.condition.notify_all()

    def get(self, timeout: float | None = None) -> tuple[str, bytes] | None:
        """Wait for a record, completion marker, or queue closure.

        Args:
            timeout: Maximum wait in seconds, or ``None`` to wait indefinitely.

        Returns:
            The oldest source-labelled record, or ``None`` for a completion
            marker or closed queue. Removing a payload wakes waiting producers.

        Raises:
            Empty: If an open, empty queue remains empty until the wait expires.
        """
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
        """Return queued stdout payloads concatenated without consuming them."""
        with self.condition:
            return b"".join(
                item[1]
                for item in self._items
                if item is not None and item[0] == "stdout"
            )

    def close(self) -> None:
        """Discard queued records and wake all blocked readers and producers.

        Repeated closure is safe; future insertion fails and reads return None.
        """
        with self.condition:
            self._closed = True
            self._items.clear()
            self._bytes = 0
            self.condition.notify_all()
