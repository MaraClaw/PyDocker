"""Explicit observation/mutation boundaries, without SDK behavior replacement."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

from pywhales.admission import AdmissionOwner, EndpointIdentity, PressureClass
from pywhales.convergence import Outcome
from pywhales.reconciliation._intent import OperationSpec
from pywhales.reconciliation._journal import ResourceIdentity

OWNERSHIP_NAMESPACE: Final = "io.pywhales.storage.v1"


def ownership_labels(spec: OperationSpec) -> Mapping[str, str]:
    """Return storage-v1 metadata to apply when creating operation-owned resources."""
    return {
        f"{OWNERSHIP_NAMESPACE}.endpoint": spec.key.endpoint.alias,
        f"{OWNERSHIP_NAMESPACE}.owner": spec.desired.owner,
        f"{OWNERSHIP_NAMESPACE}.class": spec.desired.resource_class,
        f"{OWNERSHIP_NAMESPACE}.operation": spec.key.operation,
    }


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Approved metadata and a live lease check for one adapter invocation.

    Adapters must call check_current immediately before every SDK launch, resolve
    and verify approved input externally, and never nest admission. Docker cannot
    enforce this fence; a pause after a check still races with lease transfer.
    """

    spec: OperationSpec
    check_current: Callable[[], None] = field(repr=False, compare=False)


class InventoryDisposition(StrEnum):
    """Evidence about the exact approved desired state, not merely existence."""

    APPLIED = "applied"
    ABSENT = "absent"
    INDETERMINATE = "indeterminate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class InventoryEvidence:
    """Fresh bounded inventory projection, excluding inspected payloads.

    APPLIED requires the complete desired operation and exact ownership. ABSENT
    never proves an earlier in-flight action cannot materialize. Use INDETERMINATE
    for partial, stale or ambiguous desired-state evidence; never infer readiness.
    """

    disposition: InventoryDisposition
    resources: tuple[ResourceIdentity, ...] = ()


class InventoryAdapter(Protocol):
    """Inspect bounded current inventory at the configured exact endpoint.

    Use public capture_inventory for known immutable IDs; discovery and projection
    are application-specific. Verify actual storage-v1 labels and desired input,
    not just names. Translate unavailable observations to AdapterUnavailable.
    """

    def inventory(self, context: ActionContext) -> InventoryEvidence:
        """Return fresh evidence even when no event hint has arrived."""
        ...


class MutationAdapter(Protocol):
    """Apply one approved operation, without hidden retries or other endpoints.

    Use ownership_labels on created resources. Multi-action workflows must check
    context.check_current before every launch. Translate potentially effective
    failures to AmbiguousMutation; never interpret CLI timeout as remote rollback.
    """

    def apply(self, context: ActionContext) -> None:
        """Launch approved work once; a normal return does not imply convergence."""
        ...


class ConvergenceAdapter(Protocol):
    """Observe readiness separately via the public convergence Outcome contract.

    Service/stack adapters capture exact approved generation and fixed membership,
    then call public convergence.wait with explicit bounds. Never persist captured
    templates, client configuration, causes or wire payloads in the journal.
    """

    def observe(
        self, context: ActionContext, resources: tuple[ResourceIdentity, ...]
    ) -> Outcome:
        """Return the public observation outcome without mutating resources."""
        ...


@dataclass(frozen=True, slots=True)
class ReconciliationAdapters:
    """Explicit application-owned adapters bound to one operator-defined endpoint."""

    endpoint: EndpointIdentity
    inventory: InventoryAdapter
    mutation: MutationAdapter
    convergence: ConvergenceAdapter


@dataclass(frozen=True, slots=True)
class AdmittedReconciliation:
    """Optional outer permit covering a complete deferred reconciliation pass."""

    owner: AdmissionOwner
    pressure: PressureClass
    queue_timeout: float | None = None
