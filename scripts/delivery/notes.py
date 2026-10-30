"""Exact CHANGELOG section extraction and release-body comparison.

Headings are matched by string equality, never by pattern, because versions
such as ``0.7.0rc1`` contain regular-expression metacharacters.
"""

from . import limits
from .api import require

HEADING = "## "


def unix_lines(text: str) -> list[str]:
    """Split on LF after folding CRLF and CR, never on other Unicode separators."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def section(text: str, version: str) -> str:
    """Return the body under the one level-2 heading that is exactly the version.

    The body runs to the next level-2 heading or the end of the file, keeps its
    deeper subheadings, and excludes the heading and surrounding blank lines.
    """
    lines = unix_lines(text)
    heading = f"{HEADING}{version}"
    starts = [index for index, line in enumerate(lines) if line == heading]
    require(bool(starts), "release notes section missing")
    require(len(starts) == 1, "release notes section duplicated")
    body = lines[starts[0] + 1 :]
    end = next(
        (index for index, line in enumerate(body) if line.startswith(HEADING)),
        len(body),
    )
    filled = [index for index, line in enumerate(body[:end]) if line.strip()]
    require(bool(filled), "release notes section empty")
    notes = "\n".join(body[filled[0] : filled[-1] + 1])
    require(len(notes.encode()) <= limits.NOTES_BYTES, "release notes size budget")
    return notes


def normalized(text: str | None) -> str:
    """Fold line endings and drop trailing whitespace per line and at the end."""
    return "\n".join(line.rstrip() for line in unix_lines(text or "")).rstrip()


def matches(published: str | None, sealed: str) -> bool:
    """Compare a release body with the sealed notes, ignoring only that drift."""
    return normalized(published) == normalized(sealed)
