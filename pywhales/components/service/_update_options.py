"""Validate and encode rollout policies before resolving the CLI executable."""

from datetime import timedelta

from pywhales.enums import (
    RollbackFailureActionInput,
    UpdateFailureActionInput,
    UpdateOrderInput,
)


def _duration(value: int | timedelta) -> str:
    if isinstance(value, timedelta):
        nanoseconds = (
            (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
        ) * 1000
    elif isinstance(value, int) and not isinstance(value, bool):
        nanoseconds = value * 1000000000
    else:
        raise TypeError("duration must be integer seconds or timedelta")
    if not 0 <= nanoseconds <= 2**63 - 1:
        raise ValueError("duration is outside the signed 64-bit nanosecond range")
    return f"{nanoseconds}ns"


def update_options(
    prefix: str,
    parallelism: int | None,
    delay: int | timedelta | None,
    failure_action: UpdateFailureActionInput | RollbackFailureActionInput | None,
    monitor: int | timedelta | None,
    max_failure_ratio: float | None,
    order: UpdateOrderInput | None,
) -> list[str]:
    """Return validated CLI flags in policy order, retaining explicit zeroes."""
    result: list[str] = []
    if parallelism is not None:
        if not isinstance(parallelism, int) or isinstance(parallelism, bool):
            raise TypeError("parallelism must be an integer")
        if not 0 <= parallelism <= 2**64 - 1:
            raise ValueError("parallelism is outside the unsigned 64-bit range")
        result.extend([f"--{prefix}-parallelism", str(parallelism)])
    if delay is not None:
        result.extend([f"--{prefix}-delay", _duration(delay)])
    if failure_action is not None:
        if not isinstance(failure_action, str):
            raise TypeError("failure action must be a string")
        choices = (
            ("pause", "continue", "rollback")
            if prefix == "update"
            else ("pause", "continue")
        )
        if failure_action not in choices:
            raise ValueError("invalid failure action")
        result.extend([f"--{prefix}-failure-action", failure_action])
    if monitor is not None:
        result.extend([f"--{prefix}-monitor", _duration(monitor)])
    if max_failure_ratio is not None:
        if not isinstance(max_failure_ratio, (int, float)) or isinstance(
            max_failure_ratio, bool
        ):
            raise TypeError("failure ratio must be numeric")
        if not 0 <= max_failure_ratio <= 1:
            raise ValueError("failure ratio must be between zero and one")
        result.extend([f"--{prefix}-max-failure-ratio", str(max_failure_ratio)])
    if order is not None:
        if not isinstance(order, str):
            raise TypeError("order must be a string")
        if order not in ("stop-first", "start-first"):
            raise ValueError("invalid update order")
        result.extend([f"--{prefix}-order", order])
    return result
