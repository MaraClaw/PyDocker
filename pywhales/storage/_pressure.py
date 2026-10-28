"""Fresh provider headroom with explicit missing-data policy and hysteresis."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, assert_never

from pywhales.admission import EndpointIdentity

from ._contracts import StorageError, aware, positive_age, token


class ProviderStatus(StrEnum):
    """Provider collection status, separate from headroom values."""

    OK = "ok"
    FAILED = "failed"


class MissingPressurePolicy(StrEnum):
    """Explicit operator response to missing or stale required measurements."""

    REJECT = "reject"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class Headroom:
    """Filesystem/provider evidence; Docker category totals are not headroom."""

    endpoint: EndpointIdentity
    provider: str
    observed_at: datetime
    status: ProviderStatus
    free_bytes: int | None
    free_inodes: int | None

    def __post_init__(self) -> None:
        token(self.provider, "provider")
        aware(self.observed_at)
        for value in (self.free_bytes, self.free_inodes):
            if value is not None and (type(value) is not int or value < 0):
                raise StorageError("headroom")
        if not isinstance(self.status, ProviderStatus):
            raise StorageError("provider.status")


class PressureProvider(Protocol):
    """Operator-selected host/storage collection, with failures as data.

    read returns None for missing collection or FAILED for an observed failure;
    unexpected exceptions propagate and therefore never admit work silently.
    """

    def read(self) -> Headroom | None: ...


@dataclass(frozen=True, slots=True)
class Threshold:
    """Reject at or below low; recover only at or above the higher recovery."""

    low: int
    recovery: int

    def __post_init__(self) -> None:
        if (
            type(self.low) is not int
            or type(self.recovery) is not int
            or not 0 <= self.low < self.recovery
        ):
            raise StorageError("threshold")


@dataclass(frozen=True, slots=True, kw_only=True)
class PressurePolicy:
    """Required measurements, provider scope, freshness and recovery policy."""

    endpoint: EndpointIdentity
    provider: str
    max_age: timedelta
    bytes_threshold: Threshold | None
    inodes_threshold: Threshold | None
    missing: MissingPressurePolicy

    def __post_init__(self) -> None:
        token(self.provider, "provider")
        positive_age(self.max_age)
        if self.bytes_threshold is None and self.inodes_threshold is None:
            raise StorageError("pressure.measurements")
        if not isinstance(self.missing, MissingPressurePolicy):
            raise StorageError("pressure.missing_policy")


class PressureReason(StrEnum):
    """Distinct storage pressure outcomes, not admission saturation reasons."""

    READY = "ready"
    LOW = "low_headroom"
    RECOVERING = "awaiting_recovery"
    MISSING = "missing_or_failed"
    STALE = "stale_or_future"
    SCOPE = "provider_scope_mismatch"
    DEGRADED = "operator_approved_degraded"


@dataclass(frozen=True, slots=True)
class PressureDecision:
    """Safe outcome plus the pressure latch to carry to the next observation."""

    allowed: bool
    latched: bool
    reason: PressureReason


@dataclass(frozen=True, slots=True)
class PressureContext:
    """Explicit observation time and prior pressure state for a pure decision."""

    now: datetime
    latched: bool

    def __post_init__(self) -> None:
        aware(self.now)


def assess_pressure(
    policy: PressurePolicy, sample: Headroom | None, context: PressureContext
) -> PressureDecision:
    """Apply freshness and hysteresis without I/O, deletion or quota claims."""
    invalid = None
    if sample is None:
        invalid = PressureReason.MISSING
    elif sample.endpoint != policy.endpoint or sample.provider != policy.provider:
        return PressureDecision(False, context.latched, PressureReason.SCOPE)
    elif sample.status is ProviderStatus.FAILED:
        invalid = PressureReason.MISSING
    elif not timedelta(0) <= context.now - sample.observed_at <= policy.max_age:
        invalid = PressureReason.STALE
    else:
        measurements = (
            (policy.bytes_threshold, sample.free_bytes),
            (policy.inodes_threshold, sample.free_inodes),
        )
        if any(
            threshold is not None and value is None for threshold, value in measurements
        ):
            invalid = PressureReason.MISSING
        else:
            required = [
                (threshold, value)
                for threshold, value in measurements
                if threshold is not None and value is not None
            ]
            if any(value <= threshold.low for threshold, value in required):
                return PressureDecision(False, True, PressureReason.LOW)
            if context.latched and any(
                value < threshold.recovery for threshold, value in required
            ):
                return PressureDecision(False, True, PressureReason.RECOVERING)
            return PressureDecision(True, False, PressureReason.READY)
    match policy.missing:
        case MissingPressurePolicy.REJECT:
            return PressureDecision(False, context.latched, invalid)
        case MissingPressurePolicy.DEGRADED:
            return PressureDecision(
                not context.latched, context.latched, PressureReason.DEGRADED
            )
        case unreachable:
            assert_never(unreachable)
