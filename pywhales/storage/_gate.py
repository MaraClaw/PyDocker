"""Storage pressure checks before deferred admission and again after queuing."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import override

from pywhales.admission import AdmissionRequest

from ._pressure import (
    PressureContext,
    PressureDecision,
    PressurePolicy,
    PressureProvider,
    assess_pressure,
)


@dataclass(slots=True)
class StoragePressureRejected(Exception):
    """Distinct storage reason, never disguised as a full admission queue."""

    decision: PressureDecision

    @override
    def __str__(self) -> str:
        return f"storage pressure rejected: {self.decision.reason.value}"


class PressureGate:
    """Application-owned pressure latch; callers serialize its use externally.

    This is not a quota reservation or atomic fence. Provider, clock and deferred
    request factory are trusted operator inputs for the same selected endpoint.
    """

    def __init__(
        self,
        policy: PressurePolicy,
        provider: PressureProvider,
        clock: Callable[[], datetime],
    ) -> None:
        """Start unlatched with an explicit provider and aware wall clock."""
        self.policy = policy
        self.provider = provider
        self.clock = clock
        self._latched = False

    def check(self) -> PressureDecision:
        """Observe once and preserve pressure until fresh recovery evidence."""
        sample = self.provider.read()
        decision = assess_pressure(
            self.policy, sample, PressureContext(self.clock(), self._latched)
        )
        self._latched = decision.latched
        return decision

    def call[T](
        self,
        admission_factory: Callable[[], AdmissionRequest],
        operation: Callable[[], T],
    ) -> T:
        """Check before creating admission, then again after the permit is granted.

        On rejection the SDK operation never starts. A post-grant rejection is
        counted by AdmissionOwner as failed work, not queue saturation. No active
        operation cancellation, mutation rollback or deletion is provided.
        """
        self._require_ready()

        def checked() -> T:
            self._require_ready()
            return operation()

        return admission_factory().call(checked)

    def _require_ready(self) -> None:
        decision = self.check()
        if not decision.allowed:
            raise StoragePressureRejected(decision)
