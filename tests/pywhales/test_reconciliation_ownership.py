"""Reconciliation ownership must round-trip through storage-v1 metadata."""

from dataclasses import replace

import pytest

from pywhales.admission import EndpointIdentity
from pywhales.reconciliation import (
    InvalidDescription,
    LeaseRequest,
    MemoryJournalStore,
    ResourceIdentity,
    SecretReference,
    ownership_labels,
)
from pywhales.storage import OwnershipLabels
from tests.pywhales.test_reconciliation import spec


@pytest.mark.parametrize("length", [1, 64])
def test_accepted_ownership_round_trips_through_storage(length: int) -> None:
    original = spec()
    token = "a" * length
    operation = replace(
        original,
        key=replace(original.key, endpoint=EndpointIdentity(token), operation=token),
        desired=replace(original.desired, owner=token),
    )
    store = MemoryJournalStore()

    entry = store.create_if_absent(operation)
    decoded = OwnershipLabels.from_labels(ownership_labels(entry.spec))

    assert decoded is not None
    assert (decoded.endpoint.alias, decoded.owner, decoded.operation) == (
        token,
        token,
        token,
    )


@pytest.mark.parametrize("field", ["owner", "operation"])
@pytest.mark.parametrize("length", [65, 128])
def test_oversized_ownership_rejects_before_journal_acceptance(
    field: str, length: int
) -> None:
    original = spec()
    store = MemoryJournalStore()

    with pytest.raises(InvalidDescription):
        if field == "owner":
            operation = replace(
                original, desired=replace(original.desired, owner="a" * length)
            )
        else:
            operation = replace(
                original, key=replace(original.key, operation="a" * length)
            )
        store.create_if_absent(operation)

    assert store.read(original.key) is None


@pytest.mark.parametrize("field", ["endpoint", "owner", "operation"])
def test_observed_ownership_rejects_storage_incompatible_tokens(field: str) -> None:
    resource = ResourceIdentity(
        kind="volume",
        identity="volume-a",
        endpoint="manager-a",
        owner="team-a",
        resource_class="ephemeral",
        operation="deploy-42",
    )

    with pytest.raises(InvalidDescription):
        if field == "endpoint":
            replace(resource, endpoint="a" * 65)
        elif field == "owner":
            replace(resource, owner="a" * 65)
        else:
            replace(resource, operation="a" * 65)


def test_non_ownership_metadata_retains_its_existing_bound() -> None:
    token = "a" * 128
    original = spec()

    desired = replace(
        original.desired, target=token, secrets=(SecretReference(token, token),)
    )
    lease = LeaseRequest(token, 30)
    resource = ResourceIdentity(
        kind="volume",
        identity=token,
        endpoint="manager-a",
        owner="team-a",
        resource_class="ephemeral",
        operation="deploy-42",
    )

    assert desired.target == lease.worker == resource.identity == token
