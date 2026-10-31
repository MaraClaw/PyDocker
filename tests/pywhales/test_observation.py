import sys
import time
from collections.abc import Callable, Generator
from contextvars import Context, copy_context
from threading import Barrier, Event, Thread
from typing import override

import pytest

from pywhales._observation import (
    ObservationState,
    _ResponseAccount,
    _StreamAccount,
    check_observation,
    get_observation,
    observation,
)
from pywhales.exceptions import (
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
)
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


def charge_from_threads(
    charge: Callable[[int], None], threads: int, charges: int
) -> list[ObservationLimitError]:
    """Charge one byte per call from many threads, each in its own context copy."""
    start = Barrier(threads)
    overflows: list[list[ObservationLimitError]] = [[] for _ in range(threads)]

    def run(mine: list[ObservationLimitError]) -> None:
        start.wait(timeout=3)
        for _ in range(charges):
            try:
                charge(1)
            except ObservationLimitError as error:
                mine.append(error)

    workers = [
        Thread(target=copy_context().run, args=(run, mine)) for mine in overflows
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert not worker.is_alive()
    return [error for mine in overflows for error in mine]


@pytest.fixture
def switch_often() -> Generator[None]:
    """Make the interpreter switch threads as often as it can during a test."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(interval)


@pytest.mark.usefixtures("switch_often")
def test_concurrent_charges_are_never_lost_across_nested_accounts() -> None:
    threads, charges = 8, 4000
    total = threads * charges
    outer_policy = InspectionPolicy(
        max_references=1,
        max_argv_bytes=1,
        max_response_bytes=1,
        max_total_response_bytes=total,
    )
    inner_policy = InspectionPolicy(
        max_references=1,
        max_argv_bytes=1,
        max_response_bytes=1,
        max_total_response_bytes=total,
    )
    with observation(outer_policy), observation(inner_policy) as state:
        assert charge_from_threads(state.charge_response, threads, charges) == []
        assert [account.consumed for account in state.response_accounts] == [total] * 2


@pytest.mark.usefixtures("switch_often")
def test_concurrent_overflow_reports_each_charge_beyond_the_limit_once() -> None:
    threads, charges, limit = 8, 1500, 1000
    total_policy = InspectionPolicy(
        max_references=1,
        max_argv_bytes=1,
        max_response_bytes=1,
        max_total_response_bytes=limit,
    )
    with observation(total_policy) as state:
        overflows = charge_from_threads(state.charge_response, threads, charges)

        # Every charge saw its own running total, so no total is skipped or repeated.
        assert sorted(error.measured or 0 for error in overflows) == list(
            range(limit + 1, threads * charges + 1)
        )
        assert {(error.reason, error.limit) for error in overflows} == {
            ("total_response", limit)
        }
        assert state.response_accounts[0].consumed == threads * charges


def charge_current(size: int) -> None:
    current = get_observation()
    assert current is not None
    current.charge_response(size)


def test_contexts_copied_for_workers_share_the_same_accounts() -> None:
    total_policy = InspectionPolicy(
        max_references=1,
        max_argv_bytes=1,
        max_response_bytes=1,
        max_total_response_bytes=10,
    )
    with observation(total_policy) as state:
        for context in [copy_context() for _ in range(3)]:
            context.run(charge_current, 2)

        assert get_observation() is state
        assert state.response_accounts[0].consumed == 6


class GuardedAccount(_ResponseAccount):
    """An account that fails any update made without holding its lock."""

    armed = False

    @override
    def __setattr__(self, name: str, value: object) -> None:
        assert name != "consumed" or not self.armed or self.lock.locked(), (
            "unlocked update"
        )
        super().__setattr__(name, value)


def test_every_response_charge_updates_its_account_under_the_account_lock() -> None:
    accounts = tuple(GuardedAccount(limit) for limit in (10, 3))
    for account in accounts:
        account.armed = True
    state = ObservationState(
        policy=policy(1, 1, 1),
        deadline=None,
        cancel_tokens=(),
        response_accounts=accounts,
    )

    state.charge_response(2)
    with pytest.raises(ObservationLimitError) as failure:
        state.charge_response(2)

    assert [account.consumed for account in accounts] == [4, 4]
    assert (failure.value.reason, failure.value.limit, failure.value.measured) == (
        "total_response",
        3,
        4,
    )


def stream_policy(
    limit: int | None, *, response: int | None = None
) -> InspectionPolicy:
    return InspectionPolicy(
        max_references=1,
        max_argv_bytes=1,
        max_response_bytes=1,
        max_total_response_bytes=response,
        max_total_stream_bytes=limit,
    )


def test_nested_stream_totals_take_the_smallest_set_value() -> None:
    with observation(stream_policy(10)):
        with observation(stream_policy(25)) as wider:
            assert wider.policy.max_total_stream_bytes == 10
        with observation(stream_policy(4)) as tighter:
            assert tighter.policy.max_total_stream_bytes == 4
        with observation(stream_policy(None)) as inherited:
            assert inherited.policy.max_total_stream_bytes == 10
    with observation(stream_policy(None)) as unset:
        assert unset.policy.max_total_stream_bytes is None


def test_each_scope_that_sets_a_stream_total_gets_its_own_account() -> None:
    with observation(stream_policy(10)) as outer:
        with observation(stream_policy(25)) as wider:
            assert [account.limit for account in wider.stream_accounts] == [10, 25]
        with observation(stream_policy(None)) as inherited:
            assert inherited.stream_accounts == outer.stream_accounts
        assert [account.limit for account in outer.stream_accounts] == [10]


def test_stream_charges_hit_every_enclosing_account_and_report_the_first_overflow() -> (
    None
):
    with (
        observation(stream_policy(10)) as outer,
        observation(stream_policy(4)) as inner,
    ):
        inner.charge_stream(3)
        with pytest.raises(ObservationLimitError) as failure:
            inner.charge_stream(2)

        assert (failure.value.reason, failure.value.limit, failure.value.measured) == (
            "total_stream",
            4,
            5,
        )
        assert [account.consumed for account in inner.stream_accounts] == [5, 5]
        assert outer.stream_accounts[0].consumed == 5


def test_stream_and_response_charges_use_separate_accounts() -> None:
    with observation(stream_policy(3, response=3)) as state:
        state.charge_stream(2)
        state.charge_response(3)

        with pytest.raises(ObservationLimitError) as stream:
            state.charge_stream(2)
        with pytest.raises(ObservationLimitError) as response:
            state.charge_response(1)

        assert (stream.value.reason, stream.value.measured) == ("total_stream", 4)
        assert (response.value.reason, response.value.measured) == ("total_response", 4)


def test_state_built_without_stream_accounts_charges_nothing_and_binds_nothing() -> (
    None
):
    state = ObservationState(
        policy=policy(1, 1, 1),
        deadline=None,
        cancel_tokens=(),
        response_accounts=(),
    )

    state.charge_stream(10**9)

    assert state.stream_accounts == ()
    assert state.stream_allowance(65536) == 65536
    assert not state.binds_launches


def test_launches_keep_a_scope_only_when_something_outlives_the_with_block() -> None:
    with observation(policy(1, 1, 1)) as plain:
        assert not plain.binds_launches
    with observation(stream_policy(1)) as budgeted:
        assert budgeted.binds_launches
    with observation(policy(1, 1, 1), deadline=time.monotonic() + 60) as timed:
        assert timed.binds_launches
    with observation(policy(1, 1, 1), cancel=Event()) as cancellable:
        assert cancellable.binds_launches


def test_stream_allowance_cuts_a_read_to_flag_overflow_at_its_first_excess_byte() -> (
    None
):
    with observation(stream_policy(10)), observation(stream_policy(6)) as state:
        assert state.stream_allowance(3) == 3
        assert state.stream_allowance(65536) == 7
        state.charge_stream(4)
        assert state.stream_allowance(65536) == 3
        with pytest.raises(ObservationLimitError):
            state.charge_stream(6)
        assert state.stream_allowance(65536) == 1


@pytest.mark.usefixtures("switch_often")
def test_concurrent_stream_charges_are_never_lost_across_nested_accounts() -> None:
    threads, charges = 8, 4000
    total = threads * charges
    with observation(stream_policy(total)), observation(stream_policy(total)) as state:
        assert charge_from_threads(state.charge_stream, threads, charges) == []
        assert [account.consumed for account in state.stream_accounts] == [total] * 2
        assert state.response_accounts == ()


@pytest.mark.usefixtures("switch_often")
def test_concurrent_stream_overflow_reports_each_charge_beyond_the_limit_once() -> None:
    threads, charges, limit = 8, 1500, 1000
    with observation(stream_policy(limit)) as state:
        overflows = charge_from_threads(state.charge_stream, threads, charges)

        assert sorted(error.measured or 0 for error in overflows) == list(
            range(limit + 1, threads * charges + 1)
        )
        assert {(error.reason, error.limit) for error in overflows} == {
            ("total_stream", limit)
        }


class GuardedStreamAccount(_StreamAccount):
    """A stream account that fails any update made without holding its lock."""

    armed = False

    @override
    def __setattr__(self, name: str, value: object) -> None:
        assert name != "consumed" or not self.armed or self.lock.locked(), (
            "unlocked update"
        )
        super().__setattr__(name, value)


def test_every_stream_charge_updates_its_account_under_the_account_lock() -> None:
    accounts = tuple(GuardedStreamAccount(limit) for limit in (10, 3))
    for account in accounts:
        account.armed = True
    state = ObservationState(
        policy=policy(1, 1, 1),
        deadline=None,
        cancel_tokens=(),
        response_accounts=(),
        stream_accounts=accounts,
    )

    state.charge_stream(2)
    with pytest.raises(ObservationLimitError):
        state.charge_stream(2)

    assert [account.consumed for account in accounts] == [4, 4]
