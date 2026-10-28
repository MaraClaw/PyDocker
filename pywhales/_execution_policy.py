"""Originating-config carriers and per-launch execution policy snapshots.

Resolve policy before converting command arguments to plain strings so later
config mutations affect new launches without altering an existing snapshot.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from types import MappingProxyType
from typing import Final, Protocol, Self

from pywhales._environment import consume_frozen_environment, copy_environment
from pywhales.telemetry import TelemetryObserver


class ExecutionPolicySource(Protocol):
    """Readable client settings used to snapshot each subprocess launch."""

    @property
    def environment(self) -> Mapping[str, str] | None:
        """Return the current client environment overlay."""
        ...

    @property
    def stream_buffer_bytes(self) -> int | None:
        """Return the current byte budget, or None for unbounded streams."""
        ...

    @property
    def raw_diagnostics(self) -> bool:
        """Return whether new launches may expose raw diagnostic values."""
        ...

    @property
    def telemetry_observer(self) -> TelemetryObserver | None:
        """Return the optional sanitized execution observer for new launches."""
        ...

    @property
    def default_timeout(self) -> float | None:
        """Return the default local CLI deadline in seconds."""
        ...


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Immutable stream and diagnostic settings for one subprocess launch.

    Attributes:
        stream_buffer_bytes: Retained-output budget in bytes; ``None`` is
            unbounded. Queue payload, pending records, and stderr history have
            separate limits rather than a single total-memory cap.
        raw_diagnostics: Whether automatic diagnostics may include confidential
            command, environment, and captured-output values.
        telemetry_observer: Optional payload-free observer snapshotted by reference.
        environment: Immutable client overlay captured at policy selection.
        frozen_environment: Optional preselected full environment for this launch.
        default_timeout: Default local CLI deadline in seconds, or None.
    """

    stream_buffer_bytes: int | None = None
    raw_diagnostics: bool = False
    telemetry_observer: TelemetryObserver | None = None
    environment: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )
    frozen_environment: Mapping[str, str] | None = field(default=None, repr=False)
    default_timeout: float | None = None


class BufferDefault(Enum):
    """Sentinel distinguishing client-inherited budgets from explicit None."""

    CLIENT = auto()


DEFAULT_BUFFER: Final = BufferDefault.CLIENT


class PolicyExecutable(str):
    """Executable text retaining its originating, mutable policy source.

    Shallow and deep copies preserve this carrier and its source rather than
    copying the config. The policy is read later, at subprocess launch.

    Attributes:
        policy_source: Originating config used for subsequent policy snapshots.
    """

    policy_source: ExecutionPolicySource

    def __new__(cls, value: str, policy_source: ExecutionPolicySource) -> Self:
        """Create executable text linked to a policy source.

        Args:
            value: Executable path to retain as the string value.
            policy_source: Live settings source retained by reference.

        Returns:
            The executable carrier; no policy snapshot is taken yet.
        """
        executable = super().__new__(cls, value)
        executable.policy_source = policy_source
        return executable

    def __copy__(self) -> Self:
        """Return this carrier without copying its policy source."""
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        """Return this carrier while preserving the originating source.

        Args:
            memo: Deep-copy bookkeeping, unused because the carrier is shared.

        Returns:
            This same executable carrier.
        """
        return self


def get_execution_policy(command: Sequence[object]) -> ExecutionPolicy:
    """Snapshot the first argument's policy source before stringifying argv.

    Args:
        command: Command arguments whose first item may be a PolicyExecutable.

    Returns:
        Current source settings, or safe diagnostics and an unbounded stream
        budget when the command has no policy carrier.
    """
    if command and isinstance(command[0], PolicyExecutable):
        source = command[0].policy_source
        frozen = consume_frozen_environment(source)
        environment = source.environment if frozen is None else None
        return ExecutionPolicy(
            source.stream_buffer_bytes,
            source.raw_diagnostics,
            source.telemetry_observer,
            MappingProxyType(copy_environment(environment))
            if environment is not None
            else MappingProxyType({}),
            frozen,
            source.default_timeout,
        )
    return ExecutionPolicy()
