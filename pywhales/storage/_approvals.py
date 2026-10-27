"""Immutable approval persistence protocol and observational rechecks only."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ._contracts import (
    ResourceIdentity,
    ResourceObservation,
    RetentionPolicy,
    StorageError,
    aware,
    token,
)
from ._planning import PlanEntry, classify


@dataclass(frozen=True, slots=True)
class ApprovalKey:
    """Exact resource incarnation and approval identity, never just a name."""

    resource: ResourceIdentity
    approval_id: str

    def __post_init__(self) -> None:
        token(self.approval_id, "approval_id")


@dataclass(frozen=True, slots=True)
class ApprovalAuthority:
    """Explicit externally authorized authority, reason and expiry."""

    approval_id: str
    authority: str
    reason: str
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("approval_id", "authority", "reason"):
            token(getattr(self, name), name)
        aware(self.approved_at)
        aware(self.expires_at)
        if self.expires_at <= self.approved_at:
            raise StorageError("approval.expiry")


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """Immutable exact candidate approval, not executable deletion authority."""

    candidate: PlanEntry
    authorization: ApprovalAuthority

    def __post_init__(self) -> None:
        if not classify(
            self.candidate.observation,
            self.candidate.policy,
            self.authorization.approved_at,
        ).candidate:
            raise StorageError("approval.candidate")
        if (
            not self.candidate.candidate
            or self.candidate.planned_at > self.authorization.approved_at
        ):
            raise StorageError("approval.candidate")

    @property
    def key(self) -> ApprovalKey:
        """Return the immutable persistence identity."""
        return ApprovalKey(
            self.candidate.observation.resource, self.authorization.approval_id
        )


class ApprovalStore(Protocol):
    """Operator-provided durable, authorized create-if-absent/read persistence.

    create_if_absent must atomically preserve the first immutable record and
    return False for an existing key. Success means durably committed. read uses
    the exact resource plus approval identity. Unavailability must raise, never
    masquerade as absence or approval. No update, consume or delete is defined.
    """

    def create_if_absent(self, record: ApprovalRecord) -> bool: ...

    def read(self, key: ApprovalKey) -> ApprovalRecord | None: ...


def record_approval(store: ApprovalStore, approval: ApprovalRecord) -> ApprovalRecord:
    """Persist once, accepting only exact retries after an existing-key result.

    Store failures propagate. An ambiguous write may be resolved by explicitly
    reading the exact key; it never authorizes execution or mutation retries.
    """
    if store.create_if_absent(approval):
        return approval
    existing = read_approval(store, approval.key)
    if existing != approval:
        raise StorageError("approval.conflict")
    return approval


def read_approval(store: ApprovalStore, key: ApprovalKey) -> ApprovalRecord | None:
    """Read only an exact resource/approval key and reject a mismatched response."""
    approval = store.read(key)
    if approval is not None and approval.key != key:
        raise StorageError("approval.identity")
    return approval


class RecheckReason(StrEnum):
    """Observational validity results, never an atomic deletion fence."""

    VALID = "observed_valid"
    EXPIRED = "expired_or_future_approval"
    POLICY = "policy_changed"
    CHANGED = "resource_or_ownership_changed"
    PROTECTED = "currently_protected"


@dataclass(frozen=True, slots=True)
class RecheckContext:
    """Caller-selected current policy and observation time."""

    policy: RetentionPolicy
    now: datetime

    def __post_init__(self) -> None:
        aware(self.now)


def recheck_approval(
    approval: ApprovalRecord, current: ResourceObservation, context: RecheckContext
) -> RecheckReason:
    """Observe identity, ownership, protection, attachment, operations and expiry.

    This neither consumes approval nor deletes anything. State may change after
    return; a future executor must define authorization and race handling anew.
    """
    authority, candidate = approval.authorization, approval.candidate
    if not authority.approved_at <= context.now < authority.expires_at:
        return RecheckReason.EXPIRED
    if candidate.policy != context.policy:
        return RecheckReason.POLICY
    before = candidate.observation
    if (
        current.resource != before.resource
        or current.ownership != before.ownership
        or current.retention_deadline != before.retention_deadline
    ):
        return RecheckReason.CHANGED
    if current.observed_at < authority.approved_at:
        return RecheckReason.PROTECTED
    if not classify(current, context.policy, context.now).candidate:
        return RecheckReason.PROTECTED
    return RecheckReason.VALID
