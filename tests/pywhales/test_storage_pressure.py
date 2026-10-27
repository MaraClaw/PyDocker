from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from pywhales.admission import (
    AdmissionOwner,
    AdmissionRequest,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
)
from pywhales.storage import (
    Headroom,
    MissingPressurePolicy,
    PressureContext,
    PressureGate,
    PressurePolicy,
    PressureReason,
    ProviderStatus,
    StorageError,
    StoragePressureRejected,
    Threshold,
    assess_pressure,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ENDPOINT = EndpointIdentity("offline")
POLICY = PressurePolicy(
    endpoint=ENDPOINT,
    provider="filesystem",
    max_age=timedelta(seconds=10),
    bytes_threshold=Threshold(100, 200),
    inodes_threshold=Threshold(10, 20),
    missing=MissingPressurePolicy.REJECT,
)
HEALTHY = Headroom(ENDPOINT, "filesystem", NOW, ProviderStatus.OK, 300, 30)


class Provider:
    def __init__(self, sample: Headroom | None) -> None:
        self.sample = sample

    def read(self) -> Headroom | None:
        return self.sample


@pytest.mark.parametrize(
    "sample,reason",
    [
        (None, PressureReason.MISSING),
        (replace(HEALTHY, status=ProviderStatus.FAILED), PressureReason.MISSING),
        (replace(HEALTHY, free_inodes=None), PressureReason.MISSING),
        (
            replace(HEALTHY, observed_at=NOW - timedelta(seconds=11)),
            PressureReason.STALE,
        ),
        (
            replace(HEALTHY, observed_at=NOW + timedelta(seconds=1)),
            PressureReason.STALE,
        ),
    ],
)
def test_missing_or_stale_pressure_rejects_before_deferred_admission(
    sample: Headroom | None, reason: PressureReason
) -> None:
    gate = PressureGate(POLICY, Provider(sample), lambda: NOW)
    factory, work = Mock(), Mock()
    with pytest.raises(StoragePressureRejected) as error:
        gate.call(factory, work)
    assert error.value.decision.reason is reason
    factory.assert_not_called()
    work.assert_not_called()


def test_operator_degraded_policy_is_explicit() -> None:
    degraded = replace(POLICY, missing=MissingPressurePolicy.DEGRADED)
    result = assess_pressure(degraded, None, PressureContext(NOW, False))
    assert result.allowed
    assert result.reason is PressureReason.DEGRADED


def test_missing_data_cannot_clear_existing_pressure_latch() -> None:
    degraded = replace(POLICY, missing=MissingPressurePolicy.DEGRADED)
    assert not assess_pressure(degraded, None, PressureContext(NOW, True)).allowed


def test_wrong_scope_never_degrades() -> None:
    degraded = replace(POLICY, missing=MissingPressurePolicy.DEGRADED)
    result = assess_pressure(
        degraded,
        replace(HEALTHY, provider="other", status=ProviderStatus.FAILED),
        PressureContext(NOW, False),
    )
    assert not result.allowed
    assert result.reason is PressureReason.SCOPE


def test_both_measurements_must_cross_recovery_thresholds() -> None:
    provider = Provider(replace(HEALTHY, free_bytes=100))
    gate = PressureGate(POLICY, provider, lambda: NOW)
    assert gate.check().reason is PressureReason.LOW
    provider.sample = replace(HEALTHY, free_bytes=199)
    assert gate.check().reason is PressureReason.RECOVERING
    provider.sample = replace(HEALTHY, free_bytes=200, free_inodes=19)
    assert gate.check().reason is PressureReason.RECOVERING
    provider.sample = replace(HEALTHY, free_bytes=200, free_inodes=20)
    assert gate.check().allowed


def test_pressure_is_checked_again_after_admission_and_releases_permit() -> None:
    provider = Provider(HEALTHY)
    gate = PressureGate(POLICY, provider, lambda: NOW)
    pressure_class = PressureClass("create")
    owner = AdmissionOwner(ENDPOINT, {pressure_class: ClassLimit(1, 0)})

    def changed_while_waiting() -> AdmissionRequest:
        provider.sample = replace(HEALTHY, free_inodes=10)
        return owner.request(pressure_class)

    work = Mock()
    with pytest.raises(StoragePressureRejected):
        gate.call(changed_while_waiting, work)
    work.assert_not_called()
    snapshot = owner.snapshot().classes[0]
    assert snapshot.active == 0
    assert snapshot.outcomes.failed == 1


def test_ready_pressure_admits_deferred_work_once() -> None:
    owner = AdmissionOwner(ENDPOINT, {PressureClass("create"): ClassLimit(1, 0)})
    gate = PressureGate(POLICY, Provider(HEALTHY), lambda: NOW)
    work = Mock(return_value="created")
    assert gate.call(lambda: owner.request(PressureClass("create")), work) == "created"
    work.assert_called_once_with()
    assert owner.snapshot().classes[0].outcomes.completed == 1


@pytest.mark.parametrize("low,recovery", [(0, 0), (-1, 1), (100, 99), (True, 200)])
def test_invalid_thresholds_fail_closed(low: int, recovery: int) -> None:
    with pytest.raises(StorageError):
        Threshold(low, recovery)


def test_provider_exceptions_do_not_admit() -> None:
    provider = Mock()
    provider.read.side_effect = OSError("confidential provider detail")
    factory, work = Mock(), Mock()
    with pytest.raises(OSError):
        PressureGate(POLICY, provider, lambda: NOW).call(factory, work)
    factory.assert_not_called()
    work.assert_not_called()
