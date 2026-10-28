from dataclasses import replace

import pytest

from pywhales.admission import EndpointIdentity
from pywhales.convergence import Cancelled, Deadline, Failure, Outcome, Success
from pywhales.reconciliation import (
    ActionContext,
    AmbiguousMutation,
    AttemptState,
    Controller,
    DesiredDescription,
    IdentityConflict,
    InputReference,
    InventoryDisposition,
    InventoryEvidence,
    LeaseRequest,
    MemoryJournalStore,
    OperationKey,
    OperationSpec,
    ReconciliationAdapters,
    ResourceIdentity,
)


def spec() -> OperationSpec:
    return OperationSpec(
        OperationKey(EndpointIdentity("manager-a"), "deploy-42"),
        DesiredDescription(
            approved_input=InputReference("a" * 64),
            owner="team-a",
            resource_class="ephemeral",
            target="web",
        ),
    )


class Endpoint:
    def __init__(self) -> None:
        self.resources: tuple[ResourceIdentity, ...] = ()
        self.trace: list[str] = []
        self.lost_response = False
        self.outcome: Outcome = Success()
        self.disposition: InventoryDisposition | None = None

    def inventory(self, context: ActionContext) -> InventoryEvidence:
        context.check_current()
        self.trace.append("inventory")
        return InventoryEvidence(
            self.disposition
            or (
                InventoryDisposition.APPLIED
                if self.resources
                else InventoryDisposition.ABSENT
            ),
            self.resources,
        )

    def apply(self, context: ActionContext) -> None:
        context.check_current()
        self.trace.append("mutate")
        desired, key = context.spec.desired, context.spec.key
        self.resources = (
            ResourceIdentity(
                "service",
                "service-id",
                key.endpoint.alias,
                desired.owner,
                desired.resource_class,
                key.operation,
            ),
        )
        if self.lost_response:
            raise AmbiguousMutation()

    def observe(
        self, context: ActionContext, resources: tuple[ResourceIdentity, ...]
    ) -> Outcome:
        context.check_current()
        self.trace.append("observe")
        assert resources == self.resources
        return self.outcome


def controller(store: MemoryJournalStore, endpoint: Endpoint) -> Controller:
    return Controller(
        store, ReconciliationAdapters(spec().key.endpoint, endpoint, endpoint, endpoint)
    )


def test_restart_same_spec_returns_existing_result_without_mutation() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    first = controller(store, endpoint)
    completed = first.reconcile(
        first.claim(first.submit(spec()), LeaseRequest("worker-a", 30))
    )
    endpoint.trace.clear()
    restarted = controller(store, endpoint)
    existing = restarted.submit(spec())
    observed = restarted.reconcile(
        restarted.claim(existing, LeaseRequest("worker-b", 30))
    )
    assert completed.progress.state is observed.progress.state is AttemptState.SUCCEEDED
    assert observed.progress.attempts == 1
    assert endpoint.trace == ["inventory", "observe"]


def test_conflicting_intent_never_invokes_endpoint() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    owner = controller(store, endpoint)
    original = owner.submit(spec())
    conflicting = replace(
        spec(), desired=replace(spec().desired, approved_input=InputReference("b" * 64))
    )
    with pytest.raises(IdentityConflict):
        owner.submit(conflicting)
    assert store.read(spec().key) == original
    assert endpoint.trace == []


def test_remote_success_lost_response_recovers_by_inventory_without_duplicate() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    endpoint.lost_response = True
    first = controller(store, endpoint)
    unknown = first.reconcile(
        first.claim(first.submit(spec()), LeaseRequest("worker-a", 30))
    )
    restarted = controller(store, endpoint)
    resolved = restarted.reconcile(
        restarted.claim(restarted.submit(spec()), LeaseRequest("worker-b", 30))
    )
    assert unknown.progress.state is AttemptState.UNKNOWN
    assert resolved.progress.state is AttemptState.SUCCEEDED
    assert endpoint.trace == ["inventory", "mutate", "inventory", "observe"]


def test_absent_after_ambiguous_attempt_never_authorizes_replay() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    endpoint.lost_response = True
    owner = controller(store, endpoint)
    unknown = owner.reconcile(
        owner.claim(owner.submit(spec()), LeaseRequest("worker-a", 30))
    )
    endpoint.resources = ()
    endpoint.trace.clear()
    result = owner.reconcile(owner.claim(unknown, LeaseRequest("worker-b", 30)))
    assert result.progress.state is AttemptState.UNKNOWN
    assert endpoint.trace == ["inventory"]


def test_missed_event_is_repaired_by_periodic_inventory() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    endpoint.outcome = Deadline()
    owner = controller(store, endpoint)
    pending = owner.reconcile(
        owner.claim(owner.submit(spec()), LeaseRequest("worker-a", 30))
    )
    endpoint.outcome = Success()
    endpoint.trace.clear()
    repaired = owner.reconcile(owner.claim(pending, LeaseRequest("worker-b", 30)))
    assert pending.progress.convergence == "deadline"
    assert repaired.progress.state is AttemptState.SUCCEEDED
    assert endpoint.trace == ["inventory", "observe"]


@pytest.mark.parametrize(
    "outcome,summary,state",
    [
        (Success(), "success", AttemptState.SUCCEEDED),
        (Deadline(), "deadline", AttemptState.OBSERVING),
        (Cancelled(), "cancelled", AttemptState.OBSERVING),
        (
            Failure(
                reason="superseded",
                state="confidential-state",
                cause=RuntimeError("credential-payload"),
            ),
            "superseded",
            AttemptState.FAILED,
        ),
        (Failure(reason="observation"), "observation", AttemptState.FAILED),
    ],
)
def test_public_convergence_outcomes_are_projected_without_raw_fields(
    outcome: Outcome, summary: str, state: AttemptState
) -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    endpoint.outcome = outcome
    owner = controller(store, endpoint)
    result = owner.reconcile(
        owner.claim(owner.submit(spec()), LeaseRequest("worker-a", 30))
    )
    assert (result.progress.state, result.progress.convergence) == (state, summary)
    assert "credential-payload" not in repr(result)
    assert "confidential-state" not in repr(result)


def test_foreign_owned_resources_cannot_be_adopted() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    endpoint.resources = (
        ResourceIdentity(
            "service",
            "service-id",
            "manager-a",
            "other-owner",
            "ephemeral",
            "deploy-42",
        ),
    )
    owner = controller(store, endpoint)
    with pytest.raises(IdentityConflict):
        owner.reconcile(owner.claim(owner.submit(spec()), LeaseRequest("worker-a", 30)))
    assert endpoint.trace == ["inventory"]


def test_preexisting_applied_resources_do_not_enable_later_mutation() -> None:
    store, endpoint = MemoryJournalStore(), Endpoint()
    endpoint.resources = (
        ResourceIdentity(
            "service", "service-id", "manager-a", "team-a", "ephemeral", "deploy-42"
        ),
    )
    owner = controller(store, endpoint)
    existing = owner.reconcile(
        owner.claim(owner.submit(spec()), LeaseRequest("worker-a", 30))
    )
    endpoint.resources = ()
    endpoint.disposition = InventoryDisposition.INDETERMINATE
    uncertain = owner.reconcile(owner.claim(existing, LeaseRequest("worker-b", 30)))
    endpoint.disposition = InventoryDisposition.ABSENT
    endpoint.trace.clear()
    result = owner.reconcile(owner.claim(uncertain, LeaseRequest("worker-c", 30)))
    assert result.progress.state is AttemptState.UNKNOWN
    assert endpoint.trace == ["inventory"]
