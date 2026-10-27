from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final, Protocol, Self


class ExecutionPolicySource(Protocol):
    @property
    def stream_buffer_bytes(self) -> int | None: ...

    @property
    def raw_diagnostics(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    stream_buffer_bytes: int | None = None
    raw_diagnostics: bool = False


class BufferDefault(Enum):
    CLIENT = auto()


DEFAULT_BUFFER: Final = BufferDefault.CLIENT


class PolicyExecutable(str):
    """Carry the originating policy through ordinary command-list composition."""

    policy_source: ExecutionPolicySource

    def __new__(cls, value: str, policy_source: ExecutionPolicySource) -> Self:
        executable = super().__new__(cls, value)
        executable.policy_source = policy_source
        return executable

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self


def get_execution_policy(command: Sequence[object]) -> ExecutionPolicy:
    if command and isinstance(command[0], PolicyExecutable):
        source = command[0].policy_source
        return ExecutionPolicy(source.stream_buffer_bytes, source.raw_diagnostics)
    return ExecutionPolicy()
