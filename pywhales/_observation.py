"""Context-local observation limits, deadlines, and cancellation checkpoints."""

import math
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock

from pywhales.exceptions import (
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
)
from pywhales.inventory import InspectionPolicy


@dataclass(slots=True)
class _ResponseAccount:
    """Track actual bytes consumed during one observation scope.

    Workers running in copies of the scope's context share one account, so every
    charge updates ``consumed`` and judges the limit under ``lock``.
    """

    limit: int
    consumed: int = 0
    lock: Lock = field(default_factory=Lock, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservationState:
    """Effective nested limits and all active cancellation tokens."""

    policy: InspectionPolicy
    deadline: float | None
    cancel_tokens: tuple[Event, ...]
    response_accounts: tuple[_ResponseAccount, ...]

    def response_allowance(self, command_remaining: int) -> int:
        """Return the tightest remaining command or scope allowance."""
        return max(
            0,
            min(
                (
                    command_remaining,
                    *(
                        account.limit - account.consumed
                        for account in self.response_accounts
                    ),
                )
            ),
        )

    def charge_response(self, size: int) -> None:
        """Charge all enclosing scopes before reporting any overflow.

        Safe for concurrent charges from threads sharing these accounts: each
        account is updated and compared with its limit under its own lock, so no
        charge is lost and the first account to overflow reports the total it
        reached at that charge.
        """
        exceeded: tuple[int, int] | None = None
        for account in self.response_accounts:
            with account.lock:
                account.consumed += size
                consumed = account.consumed
            if consumed > account.limit and exceeded is None:
                exceeded = (account.limit, consumed)
        if exceeded is not None:
            raise ObservationLimitError("total_response", *exceeded)


_current_observation: ContextVar[ObservationState | None] = ContextVar(
    "pywhales_observation", default=None
)


def get_observation() -> ObservationState | None:
    """Return the effective state for the current context, if any."""
    return _current_observation.get()


def _minimum_optional(first: int | None, second: int | None) -> int | None:
    limits = [value for value in (first, second) if value is not None]
    return min(limits) if limits else None


def check_observation(state: ObservationState | None = None) -> None:
    """Raise safely if the supplied or current observation is cancelled or expired."""
    current = state if state is not None else get_observation()
    if current is None:
        return
    if any(token.is_set() for token in current.cancel_tokens):
        raise ObservationCancelled()
    if current.deadline is not None and time.monotonic() >= current.deadline:
        raise ObservationTimeout()


@contextmanager
def observation(
    policy: InspectionPolicy,
    *,
    deadline: float | None = None,
    cancel: Event | None = None,
) -> Generator[ObservationState]:
    """Apply nested bounds until exit, checking cancellation and deadline at both ends.

    Args:
        policy: Explicit bounds, intersected with any enclosing scope.
        deadline: Finite absolute monotonic timestamp in seconds, or no deadline.
        cancel: Optional token; every enclosing token remains effective.

    Yields:
        Effective state suitable for explicit checkpoints in other contexts.

    Raises:
        ValueError: If the deadline is not finite.
        ObservationCancelled: If any active cancellation token is set.
        ObservationTimeout: If the effective deadline has expired.
    """
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError("Observation deadline must be finite")
    parent = get_observation()
    response_limit = policy.max_total_response_bytes
    if parent is not None:
        policy = InspectionPolicy(
            max_references=min(parent.policy.max_references, policy.max_references),
            max_argv_bytes=min(parent.policy.max_argv_bytes, policy.max_argv_bytes),
            max_response_bytes=min(
                parent.policy.max_response_bytes, policy.max_response_bytes
            ),
            max_total_references=_minimum_optional(
                parent.policy.max_total_references, policy.max_total_references
            ),
            max_total_response_bytes=_minimum_optional(
                parent.policy.max_total_response_bytes, policy.max_total_response_bytes
            ),
        )
    deadlines = tuple(
        value
        for value in (parent.deadline if parent else None, deadline)
        if value is not None
    )
    tokens = parent.cancel_tokens if parent is not None else ()
    accounts = parent.response_accounts if parent is not None else ()
    state = ObservationState(
        policy=policy,
        deadline=min(deadlines) if deadlines else None,
        cancel_tokens=(*tokens, cancel) if cancel is not None else tokens,
        response_accounts=(
            (*accounts, _ResponseAccount(response_limit))
            if response_limit is not None
            else accounts
        ),
    )
    token = _current_observation.set(state)
    try:
        check_observation(state)
        yield state
        check_observation(state)
    finally:
        _current_observation.reset(token)
