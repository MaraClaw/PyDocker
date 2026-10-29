"""Finite retained-input policies for the trusted delivery formats.

These limits bound bodies and member payloads, not parser memory or CPU.
See README.md for sizing and the independent local entry points.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Entry count, individual member bytes and total expanded payload bytes."""

    entries: int
    member_bytes: int
    expanded_bytes: int


MIB: Final = 1024 * 1024
RESPONSE_BYTES: Final = 64 * MIB
PROJECT_BYTES: Final = MIB
DISTRIBUTION_BYTES: Final = 16 * MIB
PAGES_TAR_BYTES: Final = 160 * MIB
ARTIFACT: Final = ArchiveLimits(8192, 16 * MIB, 128 * MIB)
DISTRIBUTION: Final = ArchiveLimits(4096, 8 * MIB, 64 * MIB)
SITE: Final = ArchiveLimits(8192, 16 * MIB, 128 * MIB)
CHUNK_BYTES: Final = 64 * 1024
