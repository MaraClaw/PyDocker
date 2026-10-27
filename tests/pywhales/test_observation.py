import time
from contextvars import Context
from threading import Event

import pytest

from pywhales._observation import check_observation, get_observation, observation
from pywhales.exceptions import ObservationCancelled, ObservationTimeout
from pywhales.inventory import InspectionPolicy


def policy(references: int, argv: int, response: int) -> InspectionPolicy:
    return InspectionPolicy(
        max_references=references, max_argv_bytes=argv, max_response_bytes=response
    )


def test_nested_scope_intersects_limits_and_restores_parent() -> None:
    outer_cancel, inner_cancel = Event(), Event()
    deadline = time.monotonic() + 60
    with observation(
        policy(5, 100, 30), deadline=deadline, cancel=outer_cancel
    ) as outer:
        with observation(
            policy(8, 40, 20), deadline=deadline + 5, cancel=inner_cancel
        ) as inner:
            assert inner.policy == policy(5, 40, 20)
            assert inner.deadline == deadline
            assert inner.cancel_tokens == (outer_cancel, inner_cancel)
            assert get_observation() is inner
            assert Context().run(get_observation) is None
        assert get_observation() is outer
    assert get_observation() is None


def test_cancellation_after_body_raises_and_restores_parent() -> None:
    cancel = Event()
    with observation(policy(1, 1, 1)) as outer:
        with (
            pytest.raises(ObservationCancelled),
            observation(policy(1, 1, 1), cancel=cancel),
        ):
            cancel.set()
        assert get_observation() is outer


def test_parent_cancellation_is_checked_in_nested_scope() -> None:
    cancel = Event()
    with (
        pytest.raises(ObservationCancelled),
        observation(policy(1, 1, 1), cancel=cancel),
    ):
        cancel.set()
        with observation(policy(1, 1, 1)):
            pytest.fail("cancelled scope was entered")
    assert get_observation() is None


def test_expired_deadline_rejects_entry_and_restores_scope() -> None:
    with pytest.raises(ObservationTimeout), observation(policy(1, 1, 1), deadline=0):
        pytest.fail("expired scope was entered")
    assert get_observation() is None


def test_explicit_state_checkpoint_works_outside_context() -> None:
    cancel = Event()
    with observation(policy(1, 1, 1), cancel=cancel) as state:
        pass
    cancel.set()
    with pytest.raises(ObservationCancelled):
        check_observation(state)


def test_body_error_is_preserved_when_cancellation_is_set() -> None:
    cancel = Event()
    with pytest.raises(RuntimeError), observation(policy(1, 1, 1), cancel=cancel):
        cancel.set()
        raise RuntimeError("body error")
    assert get_observation() is None


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_deadline_is_rejected(deadline: float) -> None:
    with pytest.raises(ValueError), observation(policy(1, 1, 1), deadline=deadline):
        pytest.fail("invalid scope was entered")
