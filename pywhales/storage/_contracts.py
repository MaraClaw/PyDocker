"""Storage metadata and inert observations; labels never confer authority."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, override

from pywhales.admission import EndpointIdentity

LABEL_NAMESPACE: Final = "io.pywhales.storage.v1"


@dataclass(slots=True)
class StorageError(Exception):
    """Bounded failure field without confidential input or provider errors."""

    field: str

    @override
    def __str__(self) -> str:
        return f"storage contract rejected: {self.field}"


def token(value: str, field: str) -> None:
    """Validate storage metadata tokens without echoing invalid values."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None:
        raise StorageError(field)


def aware(value: datetime) -> None:
    """Require an aware timestamp for durable cross-process evidence."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise StorageError("timestamp")


def positive_age(value: timedelta) -> None:
    """Require a positive evidence lifetime."""
    if value <= timedelta(0):
        raise StorageError("max_age")


class ResourceClass(StrEnum):
    """Retention classes; only owned ephemeral objects can be candidates."""

    BORROWED = "borrowed"
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"
    ROLLBACK = "rollback-protected"


@dataclass(frozen=True, slots=True)
class OwnershipLabels:
    """Versioned metadata, separate from independently verified ownership."""

    endpoint: EndpointIdentity
    owner: str
    resource_class: ResourceClass
    operation: str | None = None

    def __post_init__(self) -> None:
        token(self.owner, "owner")
        if self.operation is not None:
            token(self.operation, "operation")
        if not isinstance(self.resource_class, ResourceClass):
            raise StorageError("class")

    def as_labels(self) -> dict[str, str]:
        """Return Docker label metadata, never a credential or approval."""
        labels = {
            f"{LABEL_NAMESPACE}.endpoint": self.endpoint.alias,
            f"{LABEL_NAMESPACE}.owner": self.owner,
            f"{LABEL_NAMESPACE}.class": self.resource_class.value,
        }
        if self.operation is not None:
            labels[f"{LABEL_NAMESPACE}.operation"] = self.operation
        return labels

    @classmethod
    def from_labels(cls, labels: Mapping[str, str]) -> OwnershipLabels | None:
        """Treat missing, malformed or unknown-version metadata as unknown."""
        try:
            return cls(
                EndpointIdentity(labels[f"{LABEL_NAMESPACE}.endpoint"]),
                labels[f"{LABEL_NAMESPACE}.owner"],
                ResourceClass(labels[f"{LABEL_NAMESPACE}.class"]),
                labels.get(f"{LABEL_NAMESPACE}.operation"),
            )
        except KeyError, ValueError, StorageError:
            return None


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    """Exact endpoint/kind/identity plus externally verified incarnation.

    Docker volume names are reusable. The inventory provider must supply durable
    incarnation evidence; None stays protected even if all labels match.
    """

    endpoint: EndpointIdentity
    kind: str
    resource_id: str
    incarnation: str | None

    def __post_init__(self) -> None:
        token(self.kind, "kind")
        if not self.resource_id or "\x00" in self.resource_id:
            raise StorageError("resource_id")
        if self.incarnation is not None and not self.incarnation:
            raise StorageError("incarnation")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceObservation:
    """Operator-supplied evidence; None attachment/operations means unknown."""

    resource: ResourceIdentity
    ownership: OwnershipLabels | None
    ownership_verified: bool
    attached: bool | None
    active_operations: tuple[str, ...] | None
    retention_deadline: datetime | None
    observed_at: datetime
    rollback_protected: bool

    def __post_init__(self) -> None:
        if (
            type(self.ownership_verified) is not bool
            or type(self.rollback_protected) is not bool
        ):
            raise StorageError("observation.verification")
        if self.attached is not None and type(self.attached) is not bool:
            raise StorageError("observation.attachment")
        aware(self.observed_at)
        if self.retention_deadline is not None:
            aware(self.retention_deadline)
        if self.active_operations is not None:
            object.__setattr__(self, "active_operations", tuple(self.active_operations))
            for operation in self.active_operations:
                token(operation, "active_operations")


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Explicit owner, endpoint, policy version and evidence lifetime."""

    endpoint: EndpointIdentity
    owner: str
    version: str
    max_age: timedelta

    def __post_init__(self) -> None:
        token(self.owner, "owner")
        token(self.version, "policy.version")
        positive_age(self.max_age)
