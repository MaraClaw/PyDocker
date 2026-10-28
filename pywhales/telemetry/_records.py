"""Optional, payload-free local execution metadata and application-owned export.

Records never contain argv, endpoints, environment, output, or exception text,
even when raw diagnostics are enabled. No monitoring dependency is required.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

type OperationFamily = Literal["captured", "stream"]
type Outcome = Literal["success", "failure", "timeout", "closed"]


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    """Versioned metadata emitted immediately before a local launch attempt.

    Attributes:
        family: Fixed execution family, never inferred from command arguments.
        attempt_id: Opaque identifier shared with the completion record.
        schema_version: Record schema version, currently 2.
        phase: Fixed launch discriminator. Measurements are not yet available.
    """

    family: OperationFamily
    attempt_id: str = field(kw_only=True)
    schema_version: Literal[2] = field(default=2, init=False)
    phase: Literal["launch"] = field(default="launch", init=False)


@dataclass(frozen=True, slots=True)
class CompletionRecord:
    """Consumer-visible local completion, not remote workload status.

    Attributes:
        family: Fixed execution family matching the launch record.
        outcome: Success, failure, timeout, or explicit consumer closure.
        duration_seconds: Monotonic seconds from launch attempt to termination
            or stream closure/exhaustion, including paused stream consumption.
        stdout_bytes: Bytes actually captured/read, or None when unavailable.
        stderr_bytes: Bytes actually captured/read, or None when unavailable.
        attempt_id: Opaque identifier shared with the launch record.
        return_code: Reaped local process status, or None if unavailable.
        schema_version: Record schema version, currently 2.
        phase: Fixed completion discriminator.
    """

    family: OperationFamily
    outcome: Outcome
    duration_seconds: float
    stdout_bytes: int | None = None
    stderr_bytes: int | None = None
    attempt_id: str = field(kw_only=True)
    return_code: int | None = field(default=None, kw_only=True)
    schema_version: Literal[2] = field(default=2, init=False)
    phase: Literal["completion"] = field(default="completion", init=False)


type TelemetryRecord = LaunchRecord | CompletionRecord
type TelemetryObserver = Callable[[TelemetryRecord], None]
