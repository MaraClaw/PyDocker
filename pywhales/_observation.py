"""Context-local observation limits, deadlines, and cancellation checkpoints."""

import math
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event

from pywhales.exceptions import ObservationCancelled, ObservationTimeout
from pywhales.inventory import InspectionPolicy


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservationState:
    """Effective nested limits and all active cancellation tokens."""

    policy: InspectionPolicy
    deadline: float | None
    cancel_tokens: tuple[Event, ...]


_current_observation: ContextVar[ObservationState | None] = ContextVar(
    "pywhales_observation", default=None
)


def get_observation() -> ObservationState | None:
    """Return the effective state for the current context, if any."""
    return _current_observation.get()


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
    if parent is not None:
        policy = InspectionPolicy(
            max_references=min(parent.policy.max_references, policy.max_references),
            max_argv_bytes=min(parent.policy.max_argv_bytes, policy.max_argv_bytes),
            max_response_bytes=min(
                parent.policy.max_response_bytes, policy.max_response_bytes
            ),
        )
    deadlines = tuple(
        value
        for value in (parent.deadline if parent else None, deadline)
        if value is not None
    )
    tokens = parent.cancel_tokens if parent is not None else ()
    state = ObservationState(
        policy=policy,
        deadline=min(deadlines) if deadlines else None,
        cancel_tokens=(*tokens, cancel) if cancel is not None else tokens,
    )
    token = _current_observation.set(state)
    try:
        check_observation(state)
        yield state
        check_observation(state)
    finally:
        _current_observation.reset(token)
