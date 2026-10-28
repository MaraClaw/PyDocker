"""Check bounded command launches against explicit and native size limits."""

import os
import struct
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from pywhales.exceptions import ObservationLimitError

if TYPE_CHECKING:
    from pywhales.inventory import InspectionPolicy


def argv_size(args: Sequence[str]) -> int:
    """Return serialized argv bytes including terminators on the current platform."""
    if any("\0" in arg for arg in args):
        raise ObservationLimitError("nul", None, None)
    if os.name == "nt":
        return (
            len(
                subprocess.list2cmdline(args).encode(
                    "utf-16-le", errors="surrogatepass"
                )
            )
            + 2
        )
    return sum(len(os.fsencode(arg)) + 1 for arg in args)


def check_launch(
    args: Sequence[str], env: Mapping[str, str], policy: InspectionPolicy
) -> None:
    """Check complete argv and the exact environment passed to the subprocess.

    Args:
        args: Complete command, including executable and global options.
        env: Effective environment, including inherited and runner-added entries.
        policy: Explicit caller limits.

    Raises:
        ObservationLimitError: If a limit is exceeded, unknown, or a NUL is present.
    """
    size = argv_size(args)
    if size > policy.max_argv_bytes:
        raise ObservationLimitError("argv", policy.max_argv_bytes, size)
    if any("\0" in key or "\0" in value or "=" in key for key, value in env.items()):
        raise ObservationLimitError("environment", None, None)
    if os.name == "nt":
        command_units = size // 2
        if command_units > 32767:
            raise ObservationLimitError("native_argv", 32767, command_units)
        environment_units = (
            sum(
                len(f"{key}={value}".encode("utf-16-le", errors="surrogatepass")) // 2
                + 1
                for key, value in env.items()
            )
            + 1
        )
        if environment_units > 32767:
            raise ObservationLimitError("native_environment", 32767, environment_units)
        return
    try:
        native_limit = os.sysconf("SC_ARG_MAX")
        page_size = os.sysconf("SC_PAGESIZE") if sys.platform.startswith("linux") else 1
    except OSError, ValueError:
        raise ObservationLimitError("native_limit_unknown", None, None) from None
    if native_limit <= 0 or page_size <= 0:
        raise ObservationLimitError("native_limit_unknown", None, None)
    strings = [os.fsencode(arg) for arg in args]
    strings.extend(os.fsencode(f"{key}={value}") for key, value in env.items())
    if sys.platform.startswith("linux"):
        string_limit = 32 * page_size
        for value in strings:
            measured = len(value) + 1
            if measured > string_limit:
                raise ObservationLimitError("native_string", string_limit, measured)
    total = sum(len(value) + 1 for value in strings)
    total += (len(args) + len(env) + 2) * struct.calcsize("P")
    if total > native_limit:
        raise ObservationLimitError("native_total", native_limit, total)
