from dataclasses import replace
from datetime import timedelta

import pytest

from pywhales.storage import (
    OwnershipLabels,
    ProtectionReason,
    ResourceClass,
    ResourceObservation,
    StorageError,
    plan_retention,
)
from tests.pywhales.test_storage_planning import ENDPOINT, NOW, POLICY
from tests.pywhales.test_storage_planning import observation as observation


@pytest.mark.parametrize(
    "field,value",
    [
        ("ownership_verified", "false"),
        ("ownership_verified", 1),
        ("rollback_protected", None),
        ("rollback_protected", 0),
        ("attached", "false"),
    ],
)
def test_malformed_evidence_is_not_a_candidate(
    field: str, value: object, observation: ResourceObservation
) -> None:
    with pytest.raises(StorageError):
        replace(observation, **{field: value})


def test_owned_ephemeral_and_protected_inventory(
    observation: ResourceObservation,
) -> None:
    persistent = replace(
        observation,
        ownership=OwnershipLabels(ENDPOINT, "operator", ResourceClass.PERSISTENT),
    )
    borrowed = replace(
        observation,
        ownership=OwnershipLabels(ENDPOINT, "operator", ResourceClass.BORROWED),
    )
    rollback = replace(
        observation,
        resource=replace(observation.resource, kind="image"),
        rollback_protected=True,
    )
    unknown = replace(observation, ownership=None)
    result = plan_retention(
        (observation, persistent, borrowed, rollback, unknown), POLICY, NOW
    )
    assert [entry.reason for entry in result] == [
        ProtectionReason.CANDIDATE,
        ProtectionReason.PERSISTENT,
        ProtectionReason.BORROWED,
        ProtectionReason.ROLLBACK,
        ProtectionReason.UNKNOWN,
    ]
    assert result[0].observation.observed_at == NOW
    assert result[0].observation.retention_deadline == NOW - timedelta(days=1)
