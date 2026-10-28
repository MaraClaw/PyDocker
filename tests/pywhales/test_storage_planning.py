from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from pywhales.admission import EndpointIdentity
from pywhales.storage import (
    ApprovalAuthority,
    ApprovalKey,
    ApprovalRecord,
    OwnershipLabels,
    ProtectionReason,
    RecheckContext,
    RecheckReason,
    ResourceClass,
    ResourceIdentity,
    ResourceObservation,
    RetentionPolicy,
    StorageError,
    classify,
    read_approval,
    recheck_approval,
    record_approval,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ENDPOINT = EndpointIdentity("offline")
POLICY = RetentionPolicy(ENDPOINT, "operator", "v1", timedelta(seconds=30))


@pytest.fixture
def observation() -> ResourceObservation:
    return ResourceObservation(
        resource=ResourceIdentity(ENDPOINT, "volume", "scratch", "generation-1"),
        ownership=OwnershipLabels(
            ENDPOINT, "operator", ResourceClass.EPHEMERAL, "op-1"
        ),
        ownership_verified=True,
        attached=False,
        active_operations=(),
        retention_deadline=NOW - timedelta(days=1),
        observed_at=NOW,
        rollback_protected=False,
    )


@pytest.fixture
def approval(observation: ResourceObservation) -> ApprovalRecord:
    return ApprovalRecord(
        classify(observation, POLICY, NOW),
        ApprovalAuthority(
            "approval-1",
            "operator",
            "expired-retention",
            NOW,
            NOW + timedelta(minutes=5),
        ),
    )


class MemoryApprovalStore:
    def __init__(self) -> None:
        self.records: dict[ApprovalKey, ApprovalRecord] = {}
        self.reads: list[ApprovalKey] = []

    def create_if_absent(self, record: ApprovalRecord) -> bool:
        if record.key in self.records:
            return False
        self.records[record.key] = record
        return True

    def read(self, key: ApprovalKey) -> ApprovalRecord | None:
        self.reads.append(key)
        return self.records.get(key)


@pytest.mark.parametrize(
    "reason",
    [
        ProtectionReason.UNKNOWN,
        ProtectionReason.IDENTITY,
        ProtectionReason.STALE,
        ProtectionReason.ATTACHED,
        ProtectionReason.ACTIVE,
        ProtectionReason.RETAINED,
        ProtectionReason.ENDPOINT,
    ],
)
def test_incomplete_or_conflicting_evidence_stays_protected(
    reason: ProtectionReason, observation: ResourceObservation
) -> None:
    variants = {
        ProtectionReason.UNKNOWN: replace(observation, ownership_verified=False),
        ProtectionReason.IDENTITY: replace(
            observation, resource=replace(observation.resource, incarnation=None)
        ),
        ProtectionReason.STALE: replace(
            observation, observed_at=NOW - timedelta(seconds=31)
        ),
        ProtectionReason.ATTACHED: replace(observation, attached=None),
        ProtectionReason.ACTIVE: replace(observation, active_operations=None),
        ProtectionReason.RETAINED: replace(observation, retention_deadline=None),
        ProtectionReason.ENDPOINT: replace(
            observation,
            resource=replace(observation.resource, endpoint=EndpointIdentity("other")),
        ),
    }
    assert classify(variants[reason], POLICY, NOW).reason is reason


def test_labels_roundtrip_is_metadata_not_authority(
    observation: ResourceObservation,
) -> None:
    metadata = OwnershipLabels(ENDPOINT, "operator", ResourceClass.EPHEMERAL, "op-1")
    labels = metadata.as_labels()
    decoded = OwnershipLabels.from_labels(labels)
    assert decoded == metadata
    assert labels["io.pywhales.storage.v1.operation"] == "op-1"
    assert not classify(
        replace(observation, ownership=decoded, ownership_verified=False), POLICY, NOW
    ).candidate


@pytest.mark.parametrize(
    "labels",
    [
        {},
        {"io.pywhales.storage.v1.class": "ephemeral"},
        {"io.pywhales.storage.v2.owner": "operator"},
        {
            "io.pywhales.storage.v1.endpoint": "offline",
            "io.pywhales.storage.v1.owner": "operator",
            "io.pywhales.storage.v1.class": "unrecognized",
        },
    ],
)
def test_unknown_metadata_is_unknown(labels: dict[str, str]) -> None:
    assert OwnershipLabels.from_labels(labels) is None


def test_approval_store_preserves_first_record_and_exact_retry(
    approval: ApprovalRecord,
) -> None:
    store = MemoryApprovalStore()
    assert record_approval(store, approval) == approval
    assert record_approval(store, approval) == approval
    assert read_approval(store, approval.key) == approval
    assert read_approval(store, replace(approval.key, approval_id="another")) is None
    assert len(store.records) == 1


def test_approval_collision_never_overwrites(approval: ApprovalRecord) -> None:
    store = MemoryApprovalStore()
    record_approval(store, approval)
    changed = replace(
        approval, authorization=replace(approval.authorization, reason="different")
    )
    with pytest.raises(StorageError, match=r"approval\.conflict"):
        record_approval(store, changed)
    assert store.records[approval.key] == approval


def test_approval_and_observations_are_immutable(approval: ApprovalRecord) -> None:
    with pytest.raises(FrozenInstanceError):
        name = "reason"
        setattr(approval.authorization, name, "changed")


@pytest.mark.parametrize(
    "change",
    [
        "attached",
        "active",
        "unknown",
        "ownership",
        "incarnation",
        "rollback",
        "retention",
        "stale",
        "preapproval",
    ],
)
def test_changed_approval_observation_is_invalid(
    change: str, approval: ApprovalRecord, observation: ResourceObservation
) -> None:
    variants = {
        "attached": replace(observation, attached=True),
        "active": replace(observation, active_operations=("op-2",)),
        "unknown": replace(observation, ownership_verified=False),
        "ownership": replace(
            observation,
            ownership=OwnershipLabels(ENDPOINT, "other-owner", ResourceClass.EPHEMERAL),
        ),
        "incarnation": replace(
            observation,
            resource=replace(observation.resource, incarnation="replacement"),
        ),
        "rollback": replace(observation, rollback_protected=True),
        "retention": replace(observation, retention_deadline=NOW + timedelta(days=1)),
        "stale": replace(observation, observed_at=NOW - timedelta(minutes=1)),
        "preapproval": replace(observation, observed_at=NOW - timedelta(seconds=1)),
    }
    assert (
        recheck_approval(approval, variants[change], RecheckContext(POLICY, NOW))
        is not RecheckReason.VALID
    )


def test_recheck_is_read_only_and_valid_at_fresh_evidence(
    approval: ApprovalRecord, observation: ResourceObservation
) -> None:
    store = MemoryApprovalStore()
    record_approval(store, approval)
    for _ in range(2):
        stored = read_approval(store, approval.key)
        assert stored is not None
        assert (
            recheck_approval(stored, observation, RecheckContext(POLICY, NOW))
            is RecheckReason.VALID
        )
    assert store.records[approval.key] == approval


def test_approval_expiry_boundary(
    approval: ApprovalRecord, observation: ResourceObservation
) -> None:
    assert (
        recheck_approval(
            approval,
            observation,
            RecheckContext(POLICY, approval.authorization.expires_at),
        )
        is RecheckReason.EXPIRED
    )


def test_current_policy_version_must_match(
    approval: ApprovalRecord, observation: ResourceObservation
) -> None:
    assert (
        recheck_approval(
            approval, observation, RecheckContext(replace(POLICY, version="v2"), NOW)
        )
        is RecheckReason.POLICY
    )


def test_persistent_data_cannot_receive_approval(
    observation: ResourceObservation,
) -> None:
    persistent = replace(
        observation,
        ownership=OwnershipLabels(ENDPOINT, "operator", ResourceClass.PERSISTENT),
    )
    with pytest.raises(StorageError):
        ApprovalRecord(
            classify(persistent, POLICY, NOW),
            ApprovalAuthority("a", "operator", "test", NOW, NOW + timedelta(seconds=1)),
        )
