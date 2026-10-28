"""Immutable convergence targets and caller-visible outcomes."""

from dataclasses import dataclass, field
from typing import Literal

from pywhales.client_config import ClientConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceTarget:
    """A captured service generation and its originating client configuration."""

    client_config: ClientConfig = field(repr=False, compare=False)
    service_id: str
    version: int
    fingerprint: str
    template: str = field(repr=False)
    replicas: int
    mode: Literal["replicated", "global"] = "replicated"
    approved_node_ids: tuple[str, ...] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject invalid or mode-inconsistent node membership."""
        nodes = self.approved_node_ids
        if self.mode == "replicated" and nodes is None:
            return
        if (
            self.mode != "global"
            or type(nodes) is not tuple
            or not nodes
            or any(type(node) is not str or not node for node in nodes)
            or len(set(nodes)) != len(nodes)
        ):
            raise ValueError("Global targets require distinct nonempty node IDs")


@dataclass(frozen=True, slots=True, kw_only=True)
class StackTarget:
    """An explicit, nonempty and fixed set of service generations."""

    services: tuple[ServiceTarget, ...]

    def __post_init__(self) -> None:
        """Reject empty or duplicate membership."""
        if (
            type(self.services) is not tuple
            or not self.services
            or len({target.service_id for target in self.services})
            != len(self.services)
        ):
            raise ValueError("Stack target must contain distinct services")


@dataclass(frozen=True, slots=True)
class Success:
    """All fixed services met the captured convergence conditions."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Failure:
    """Terminal outcome; the optional cause may contain confidential details."""

    reason: Literal[
        "unsupported", "malformed", "superseded", "update_failed", "observation"
    ]
    service_id: str | None = None
    state: str | None = None
    cause: Exception | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class DeadlineDetail:
    """Affected-service counts from the latest complete polling cycle.

    Each reason counts a service at most once; reasons can overlap. Counts describe
    a past observation over captured membership, not daemon state at expiry. No
    service identities, task payloads, client settings or failure causes are kept.
    """

    captured_services: int
    missing_ready_slots: int = 0
    duplicate_slots: int = 0
    stale_templates: int = 0
    non_running_tasks: int = 0
    invalid_task_shape: int = 0
    ongoing_updates: int = 0
    missing_ready_nodes: int = 0
    duplicate_node_assignments: int = 0
    missing_node_assignments: int = 0
    outside_approved_nodes: int = 0

    def __post_init__(self) -> None:
        """Reject non-integer counts or counts outside captured membership."""
        if type(self.captured_services) is not int or self.captured_services < 0:
            raise ValueError("Captured membership must be a nonnegative integer")
        counts = (
            self.missing_ready_slots,
            self.duplicate_slots,
            self.stale_templates,
            self.non_running_tasks,
            self.invalid_task_shape,
            self.ongoing_updates,
            self.missing_ready_nodes,
            self.duplicate_node_assignments,
            self.missing_node_assignments,
            self.outside_approved_nodes,
        )
        if any(
            type(count) is not int or not 0 <= count <= self.captured_services
            for count in counts
        ):
            raise ValueError("Affected-service counts must be bounded by membership")


@dataclass(frozen=True, slots=True)
class Deadline:
    """The total deadline expired, optionally retaining a past complete cycle.

    Detail is absent if no cycle completed before expiry, including during capture.
    Only the concrete aggregate is accepted, excluding payload-bearing subclasses.
    """

    detail: DeadlineDetail | None = None

    def __post_init__(self) -> None:
        """Reject arbitrary payloads and extended aggregate types."""
        if self.detail is not None and type(self.detail) is not DeadlineDetail:
            raise ValueError("Deadline detail must be a concrete DeadlineDetail")


@dataclass(frozen=True, slots=True)
class Cancelled:
    """An active cancellation token was set."""


type Outcome = Success | Failure | Deadline | Cancelled
