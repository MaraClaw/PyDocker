"""Immutable admission configuration and bounded reporting values."""

import math
import re
from dataclasses import dataclass
from enum import Enum


def _validate_token(value: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None
    ):
        raise ValueError("identity must be a safe ASCII token of 1 to 64 characters")


@dataclass(frozen=True, slots=True)
class EndpointIdentity:
    """Safe reporting alias, not a connection string or a global owner key."""

    alias: str

    def __post_init__(self) -> None:
        _validate_token(self.alias)


@dataclass(frozen=True, slots=True)
class PressureClass:
    """Safe application-defined admission class name."""

    name: str

    def __post_init__(self) -> None:
        _validate_token(self.name)


class SaturationPolicy(Enum):
    """Behavior when a class has no immediately available capacity."""

    WAIT = "wait"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ClassLimit:
    """Positive active capacity and bounded pending capacity for one class."""

    active: int
    pending: int
    policy: SaturationPolicy = SaturationPolicy.WAIT

    def __post_init__(self) -> None:
        if type(self.active) is not int or self.active <= 0:
            raise ValueError("active must be a positive integer")
        if type(self.pending) is not int or self.pending < 0:
            raise ValueError("pending must be a nonnegative integer")
        if not isinstance(self.policy, SaturationPolicy):
            raise ValueError("policy must be a SaturationPolicy")


def validate_queue_timeout(timeout: float | None) -> None:
    """Reject nonpositive, boolean, or nonfinite queue timeouts."""
    if timeout is not None and (
        type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise ValueError("queue_timeout must be positive finite seconds or None")


def validate_drain_timeout(timeout: float) -> None:
    """Reject nonpositive, boolean, or nonfinite drain timeouts."""
    if (
        type(timeout) not in (int, float)
        or timeout <= 0
        or (type(timeout) is float and not math.isfinite(timeout))
    ):
        raise ValueError("timeout must be positive finite seconds")


@dataclass(frozen=True, slots=True)
class OutcomeCounts:
    """Fixed uint64-saturating outcome counts; saturated marks a reached ceiling."""

    completed: int
    failed: int
    stream_closed: int
    rejected_busy: int
    rejected_full: int
    cancelled: int
    queue_timed_out: int
    rejected_closed: int
    saturated: bool


@dataclass(frozen=True, slots=True)
class ClassSnapshot:
    """Exact class occupancy and high-water marks at a snapshot instant."""

    pressure_class: PressureClass
    limit: ClassLimit
    active: int
    pending: int
    active_high_water: int
    pending_high_water: int
    outcomes: OutcomeCounts


@dataclass(frozen=True, slots=True)
class AdmissionSnapshot:
    """Safe endpoint alias, terminal closure state, and bounded class snapshots."""

    endpoint: EndpointIdentity
    classes: tuple[ClassSnapshot, ...]
    closed: bool
