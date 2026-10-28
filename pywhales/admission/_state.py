"""Mutable lock-owned admission tickets, occupancy, and fixed counters."""

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from ._contracts import ClassLimit, ClassSnapshot, OutcomeCounts, PressureClass

UINT64_MAX: Final = (1 << 64) - 1


class Outcome(IntEnum):
    """Fixed indexes into bounded per-class counters."""

    COMPLETED = 0
    FAILED = 1
    STREAM_CLOSED = 2
    REJECTED_BUSY = 3
    REJECTED_FULL = 4
    CANCELLED = 5
    QUEUE_TIMED_OUT = 6
    REJECTED_CLOSED = 7


@dataclass(eq=False, slots=True)
class Ticket:
    """Mutable request transitions protected by its owner's condition lock."""

    started: bool = False
    cancelled: bool = False
    active: bool = False
    done: bool = False


class Bucket:
    """Lock-owned mutable occupancy and counters for one configured class."""

    def __init__(self, limit: ClassLimit) -> None:
        self.limit = limit
        self.active = 0
        self.pending: deque[Ticket] = deque()
        self.active_high_water = 0
        self.pending_high_water = 0
        self.counts = [0] * len(Outcome)
        self.saturated = False

    def record(self, outcome: Outcome) -> None:
        self.counts[outcome] = min(UINT64_MAX, self.counts[outcome] + 1)
        self.saturated |= self.counts[outcome] == UINT64_MAX

    def snapshot(self, pressure_class: PressureClass) -> ClassSnapshot:
        return ClassSnapshot(
            pressure_class,
            self.limit,
            self.active,
            len(self.pending),
            self.active_high_water,
            self.pending_high_water,
            OutcomeCounts(
                self.counts[Outcome.COMPLETED],
                self.counts[Outcome.FAILED],
                self.counts[Outcome.STREAM_CLOSED],
                self.counts[Outcome.REJECTED_BUSY],
                self.counts[Outcome.REJECTED_FULL],
                self.counts[Outcome.CANCELLED],
                self.counts[Outcome.QUEUE_TIMED_OUT],
                self.counts[Outcome.REJECTED_CLOSED],
                self.saturated,
            ),
        )
