"""Lossless process-listing results and fail-closed CLI table parsing."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ProcessTable:
    """Original headings and ordered string cells, without a fixed process schema."""

    headings: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ContainerTopResult(ProcessTable):
    """Docker container process table and the unmodified output returned by run."""

    raw_output: str


@dataclass(frozen=True, slots=True)
class ComposeTopSection(ProcessTable):
    """A Compose container section, or a flat table with attribution columns.

    ``container`` is None for flat SERVICE/# tables. Their attribution remains
    in the original columns rather than being synthesized into container names.
    """

    container: str | None


@dataclass(frozen=True, slots=True)
class ComposeTopResult:
    """Compose process sections in output order, retaining the complete output."""

    raw_output: str
    sections: tuple[ComposeTopSection, ...]


class TopParseError(ValueError):
    """The CLI output or selected fields have no unambiguous supported layout."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"Cannot parse top output: {reason}. Use raw=True for raw output."
        )


_TEXT_HEADINGS: Final = frozenset(
    {
        "CMD",
        "COMMAND",
        "ARGS",
        "COMM",
        "LSTART",
        "STARTED",
        "LABEL",
        "CONTEXT",
        "CGROUP",
        "CGROUPS",
    }
)
_TEXT_FIELDS: Final = frozenset(
    {
        "args",
        "cmd",
        "command",
        "comm",
        "fname",
        "ucomm",
        "lstart",
        "start",
        "start_time",
        "label",
        "context",
        "%a",
        "%c",
    }
)
_SCALAR_FIELDS: Final = re.compile(
    r"pid|ppid|pgid|pgrp|sid|sess|tpgid|tid|lwp|nlwp|spid|uid|euid|ruid|suid|fuid|"
    r"gid|egid|rgid|sgid|fgid|user|euser|ruser|suser|fuser|group|egroup|rgroup|"
    r"sgroup|fgroup|pcpu|cpu|c|cp|cpuid|psr|pmem|rss|rsz|vsz|vsize|sz|size|"
    r"tty|tt|tname|time|cputime|bsdtime|etime|etimes|stime|start|start_time|"
    r"stat|state|s|nice|ni|pri|priority|rtprio|policy|cls|class|flags|flag|f|"
    r"wchan|nwchan|addr|maj_flt|min_flt|majflt|minflt|%C|%G|%P|%U|%g|%n|%p|%r|"
    r"%t|%u|%x|%y|%z|userns|utsns|ipcns|mntns|netns|pidns|cgroupns"
)


def parse_table(
    output: str,
    fields: Sequence[str] | None = None,
) -> ProcessTable:
    """Parse scalar columns and at most one final free-text field.

    Explicit selections supply field identities independently of renamed headings.
    Unknown selected fields require raw mode, since their whitespace semantics are
    not known. Padding between scalar cells is not part of those cells.

    Args:
        output: CLI table text with headings followed by process rows.
        fields: Explicit ps field selections, optionally with renamed headings.
            None infers column semantics from the headings.

    Returns:
        Original headings and string cells in their output order.

    Raises:
        TopParseError: If headings, field selections, or row widths are unsupported
            or ambiguous.
    """
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise TopParseError("missing headings")
    headings = tuple(lines[0].split())
    text_columns = [
        index
        for index, heading in enumerate(headings)
        if heading.upper() in _TEXT_HEADINGS
    ]
    if fields is not None:
        if len(fields) != len(headings):
            raise TopParseError("selected fields and headings differ in count")
        text_columns = []
        for index, field in enumerate(fields):
            name = field.partition("=")[0]
            if name in _TEXT_FIELDS:
                text_columns.append(index)
            elif not _SCALAR_FIELDS.fullmatch(name):
                raise TopParseError(
                    f"unsupported whitespace semantics for field {name!r}"
                )
    if text_columns and text_columns != [len(headings) - 1]:
        raise TopParseError("free-text fields must be selected last")
    if fields is None and not any(
        heading.upper() in {"PID", "TID", "USER", "UID"} for heading in headings
    ):
        raise TopParseError("unrecognized or missing headings")
    rows = []
    for line in lines[1:]:
        cells = tuple(
            line.split(None, len(headings) - 1) if text_columns else line.split()
        )
        if len(cells) != len(headings):
            raise TopParseError(f"row has {len(cells)} cells, expected {len(headings)}")
        rows.append(cells)
    return ProcessTable(headings, tuple(rows))


def parse_compose_top(output: str) -> ComposeTopResult:
    """Parse grouped container tables or a flat Compose service table.

    Args:
        output: Captured Compose top output, including optional section separators.

    Returns:
        Sections in output order with the complete original text. Empty output
        produces no sections; flat service tables have no container attribution.

    Raises:
        TopParseError: If a section name or process table has an unsupported layout.
    """
    lines = output.splitlines()
    populated = [line for line in lines if line.strip()]
    if not populated:
        return ComposeTopResult(output, ())
    if populated[0].split()[:2] == ["SERVICE", "#"]:
        table = parse_table(output)
        return ComposeTopResult(
            output, (ComposeTopSection(table.headings, table.rows, None),)
        )
    sections = []
    position = 0
    while position < len(lines):
        if not lines[position].strip():
            position += 1
            continue
        container = lines[position].strip()
        if len(container.split()) != 1:
            raise TopParseError("expected a Compose container section name")
        position += 1
        start = position
        while position < len(lines) and lines[position].strip():
            position += 1
        table_lines = lines[start:position]
        if len(table_lines) > 1 and re.fullmatch(r"[\s=-]+", table_lines[1]):
            table_lines = [table_lines[0], *table_lines[2:]]
        table = parse_table("\n".join(table_lines))
        sections.append(ComposeTopSection(table.headings, table.rows, container))
    return ComposeTopResult(output, tuple(sections))
