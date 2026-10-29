"""Incremental retained-payload accounting, including standalone local files."""

from pathlib import Path
from typing import Protocol

from .api import require
from .limits import CHUNK_BYTES, ArchiveLimits


class Readable(Protocol):
    """A binary source whose size declarations need not be trusted."""

    def read(self, size: int = -1, /) -> bytes: ...


def read(stream: Readable, limit: int) -> bytes:
    """Return at most limit bytes, probing one excess byte before retaining it.

    Reaching the limit is not EOF: the final probe distinguishes an exact fit
    from excess even when the source has no length or lies about its length.
    """
    result = bytearray()
    remaining = limit
    while data := stream.read(min(CHUNK_BYTES, remaining + 1)):
        require(len(data) <= remaining, "input byte budget exceeded")
        remaining -= len(data)
        result.extend(data)
    return bytes(result)


def local(path: Path, limit: int) -> bytes:
    """Bound actual local file reads rather than relying on a stat size."""
    with path.open("rb") as stream:
        return read(stream, limit)


class Expansion:
    """Mutable per-archive accounting shared by all of its members."""

    def __init__(self, limits: ArchiveLimits) -> None:
        """Start an independent entry and expanded-payload allowance."""
        self.limits = limits
        self.entries = 0
        self.expanded = 0

    def entry(self, declared_size: int) -> None:
        """Count directories too; use declared size only for early rejection."""
        self.entries += 1
        require(self.entries <= self.limits.entries, "archive entry budget exceeded")
        require(0 <= declared_size <= self.remaining(), "archive byte budget exceeded")

    def remaining(self) -> int:
        """Return the smaller of the individual and remaining aggregate limits."""
        return min(self.limits.member_bytes, self.limits.expanded_bytes - self.expanded)

    def member(self, stream: Readable) -> bytes:
        """Charge actual expanded bytes before accepting the complete member."""
        content = read(stream, self.remaining())
        self.expanded += len(content)
        return content
