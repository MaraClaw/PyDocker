"""Pure retention planning with positive evidence and protected defaults."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import assert_never

from ._contracts import ResourceClass, ResourceObservation, RetentionPolicy, aware


class ProtectionReason(StrEnum):
    """Fixed reasons describing why an observation is not a candidate."""

    CANDIDATE = "candidate"
    ENDPOINT = "endpoint"
    UNKNOWN = "unknown_ownership"
    IDENTITY = "unknown_incarnation"
    STALE = "stale_evidence"
    BORROWED = "borrowed"
    PERSISTENT = "persistent"
    ROLLBACK = "rollback_protected"
    ATTACHED = "attached_or_unknown"
    ACTIVE = "active_or_unknown_operations"
    RETAINED = "retained_or_unknown_deadline"


@dataclass(frozen=True, slots=True)
class PlanEntry:
    """Evidence-bearing recommendation; candidate is not deletion permission."""

    observation: ResourceObservation
    policy: RetentionPolicy
    planned_at: datetime
    reason: ProtectionReason

    @property
    def candidate(self) -> bool:
        """Return whether positive planning evidence permits separate approval."""
        return self.reason is ProtectionReason.CANDIDATE


def classify(
    observation: ResourceObservation, policy: RetentionPolicy, now: datetime
) -> PlanEntry:
    """Classify one immutable snapshot without Docker or storage mutations."""
    aware(now)
    reason = _reason(observation, policy, now)
    return PlanEntry(observation, policy, now, reason)


def plan_retention(
    observations: Iterable[ResourceObservation], policy: RetentionPolicy, now: datetime
) -> tuple[PlanEntry, ...]:
    """Return a plan in input order, including protected and duplicate evidence."""
    return tuple(classify(observation, policy, now) for observation in observations)


def _reason(
    observation: ResourceObservation, policy: RetentionPolicy, now: datetime
) -> ProtectionReason:
    resource, ownership = observation.resource, observation.ownership
    if resource.endpoint != policy.endpoint:
        return ProtectionReason.ENDPOINT
    if resource.incarnation is None:
        return ProtectionReason.IDENTITY
    if not timedelta(0) <= now - observation.observed_at <= policy.max_age:
        return ProtectionReason.STALE
    if (
        ownership is None
        or not observation.ownership_verified
        or ownership.owner != policy.owner
        or ownership.endpoint != policy.endpoint
    ):
        return ProtectionReason.UNKNOWN
    if observation.rollback_protected:
        return ProtectionReason.ROLLBACK
    match ownership.resource_class:
        case ResourceClass.BORROWED:
            return ProtectionReason.BORROWED
        case ResourceClass.PERSISTENT:
            return ProtectionReason.PERSISTENT
        case ResourceClass.ROLLBACK:
            return ProtectionReason.ROLLBACK
        case ResourceClass.EPHEMERAL:
            if observation.attached is not False:
                return ProtectionReason.ATTACHED
            if observation.active_operations is None or observation.active_operations:
                return ProtectionReason.ACTIVE
            if (
                observation.retention_deadline is None
                or now < observation.retention_deadline
            ):
                return ProtectionReason.RETAINED
            return ProtectionReason.CANDIDATE
        case unreachable:
            assert_never(unreachable)
