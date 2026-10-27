"""Validation, safe reporting, and isolated admission contracts."""

from dataclasses import FrozenInstanceError

import pytest

from pywhales.admission import (
    AdmissionOwner,
    AdmissionRejected,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
    RejectionReason,
    SaturationPolicy,
)


@pytest.mark.parametrize("token", ["", "_bad", "a/b", "a:b", "a b", "é", "a" * 65])
def test_identity_rejects_unsafe_tokens(token: str) -> None:
    with pytest.raises(ValueError) as raised:
        EndpointIdentity(token)
    assert token not in str(raised.value) or not token
    with pytest.raises(ValueError):
        PressureClass(token)


@pytest.mark.parametrize(
    "active,pending", [(0, 1), (-1, 1), (True, 1), (1, -1), (1, True)]
)
def test_limit_rejects_invalid_counts(active: int, pending: int) -> None:
    with pytest.raises(ValueError):
        ClassLimit(active, pending, SaturationPolicy.WAIT)


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan")])
def test_timeout_validation_precedes_request_registration(timeout: float) -> None:
    pressure = PressureClass("read")
    owner = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    with pytest.raises(ValueError):
        owner.request(pressure, queue_timeout=timeout)
    assert owner.snapshot().classes[0].pending == 0


def test_owner_copies_limits_and_snapshots_are_frozen() -> None:
    pressure = PressureClass("read")
    limits = {pressure: ClassLimit(1, 0)}
    owner = AdmissionOwner(EndpointIdentity("local"), limits)
    limits.clear()
    assert owner.request(pressure).call(lambda: 7) == 7
    snapshot = owner.snapshot()
    assert snapshot.classes[0].outcomes.completed == 1
    attribute = "active"
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot.classes[0], attribute, 100)
    with pytest.raises(ValueError):
        AdmissionOwner(EndpointIdentity("empty"), {})
    with pytest.raises(ValueError):
        owner.request(PressureClass("unknown"))


def test_equal_endpoint_aliases_do_not_merge_owners() -> None:
    pressure = PressureClass("transfer")
    first = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    second = AdmissionOwner(EndpointIdentity("local"), {pressure: ClassLimit(1, 0)})
    assert (
        first.request(pressure).call(lambda: second.request(pressure).call(lambda: 3))
        == 3
    )
    assert first.snapshot().classes[0].outcomes.completed == 1
    assert second.snapshot().classes[0].outcomes.completed == 1


def test_rejection_has_bounded_reason_and_safe_text() -> None:
    error = AdmissionRejected(RejectionReason.QUEUE_FULL)
    assert error.reason is RejectionReason.QUEUE_FULL
    assert str(error) == "admission rejected: queue_full"
