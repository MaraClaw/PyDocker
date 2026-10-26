"""Docker ps field selection, separate from the Compose grammar."""

import re
from collections.abc import Sequence

from pywhales.components.top import TopParseError


def docker_top_fields(ps_args: Sequence[str]) -> tuple[str, ...] | None:
    """Identify explicit ps output fields, including heading aliases.

    Scalar options pass through unchanged. Headerless and implicit-column format
    extensions are available in raw mode only.
    """
    selections: list[str] = []
    arguments = iter(ps_args)
    for argument in arguments:
        if argument in {
            "-C",
            "--command",
            "-p",
            "--pid",
            "--ppid",
            "-q",
            "--quick-pid",
            "-u",
            "-U",
            "--user",
            "--User",
            "-g",
            "-G",
            "--group",
            "--Group",
            "-s",
            "--sid",
            "-t",
            "--tty",
            "--sort",
            "-k",
            "--cols",
            "--columns",
            "--width",
            "--rows",
            "--lines",
        }:
            next(arguments, None)
            continue
        if re.fullmatch(r"-[CpquUgGstk].+", argument):
            continue
        format_option = re.fullmatch(r"(-?[A-Za-z]*?)o(.*)", argument)
        flags = format_option.group(1) if format_option else argument
        if argument in {"--no-headers", "--no-heading"} or re.fullmatch(
            r"-?[A-Za-z]*h[A-Za-z]*", flags
        ):
            raise TopParseError("headerless ps selections require raw mode")
        if argument.startswith("--Format") or re.fullmatch(r"-?[A-Za-z]*O.*", flags):
            raise TopParseError("implicit ps column selections require raw mode")
        if argument == "--format":
            selection = next(arguments, "")
            if not selection:
                raise TopParseError("missing ps output field selection")
            selections.append(selection)
        elif argument.startswith("--format="):
            selections.append(argument.partition("=")[2])
        elif format_option:
            selection = format_option.group(2) or next(arguments, "")
            if not selection:
                raise TopParseError("missing ps output field selection")
            selections.append(selection)
    if not selections:
        return None
    if any(
        not part.strip() for selection in selections for part in selection.split(",")
    ):
        raise TopParseError("empty ps fields require raw mode")
    fields = tuple(
        field
        for selection in selections
        for field in selection.replace(",", " ").split()
    )
    if any(
        not field or ("=" in field and not field.partition("=")[2]) for field in fields
    ):
        raise TopParseError("empty ps headings or fields require raw mode")
    return fields
