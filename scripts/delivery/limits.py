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
CHANGELOG_BYTES: Final = MIB
# GitHub rejects release bodies above 125,000 characters. UTF-8 bytes are never
# fewer than characters, so this byte cap also bounds the characters and leaves
# 5,000 for any server-side difference when the body is read back.
NOTES_BYTES: Final = 120_000
DISTRIBUTION_BYTES: Final = 16 * MIB
PAGES_TAR_BYTES: Final = 160 * MIB
ARTIFACT: Final = ArchiveLimits(8192, 16 * MIB, 128 * MIB)
DISTRIBUTION: Final = ArchiveLimits(4096, 8 * MIB, 64 * MIB)
SITE: Final = ArchiveLimits(8192, 16 * MIB, 128 * MIB)
CHUNK_BYTES: Final = 64 * 1024
# GitHub documents no page depth for the list endpoints below, so none of these
# caps is a GitHub limit. Each is derived from what this repository's own
# workflows produce, with generous headroom; a legitimate larger history needs an
# explicit policy review here. A paginator may make `items // PAGE_ITEMS + 1`
# requests: enough pages for the cap, plus one for the empty terminator that
# follows a final full page.
PAGE_ITEMS: Final = 100
# One attempt of python-package.yml expands to 24 jobs (seven fixed checks, the
# 16-entry component matrix and the seal) and python-publish.yml to one. 200
# leaves roughly eight times the producer's matrix before the seal must be
# reviewed.
JOB_ITEMS: Final = 200
# Artifacts are run-scoped across attempts. A producer attempt uploads three
# (distributions, site, seal) and a publisher attempt one receipt, so 100
# covers more than 30 full reruns of one run.
ARTIFACT_ITEMS: Final = 100
# release-on-merge.yml creates one release per published version, and the
# draft lookup scans all of them. The changelog holds two versions today; 500
# is a couple of orders of magnitude beyond the cadence of a pure-Python package.
RELEASE_ITEMS: Final = 500
# A merged-main commit is associated with the pull request that merged it,
# occasionally with a few others that contain it. One full page is ample.
PULL_ITEMS: Final = 100
# Total response bytes across all pages of one listing, beside the per-request
# RESPONSE_BYTES. Job, artifact and pull records are a few KiB each. A release
# can carry notes up to NOTES_BYTES, so one full page of maximum-size releases
# (100 * 120,000 bytes, about 11.4 MiB) still fits.
PAGINATION_BYTES: Final = 16 * MIB
