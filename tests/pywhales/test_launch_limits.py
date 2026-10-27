import os
import struct
from unittest.mock import patch

import pytest

from pywhales._launch_limits import argv_size, check_launch
from pywhales.exceptions import ObservationLimitError
from pywhales.inventory import InspectionPolicy


def policy(limit: int) -> InspectionPolicy:
    return InspectionPolicy(
        max_references=1, max_argv_bytes=limit, max_response_bytes=1
    )


def test_caller_argv_limit_includes_utf8_and_terminators() -> None:
    args = ["command", "é"]
    assert argv_size(args) == 11
    check_launch(args, {}, policy(11))
    with pytest.raises(ObservationLimitError) as failure:
        check_launch(args, {}, policy(10))
    assert (failure.value.reason, failure.value.limit, failure.value.measured) == (
        "argv",
        10,
        11,
    )


def test_native_total_includes_environment_and_pointers() -> None:
    args, env = ["cmd"], {"KEY": "secret"}
    total = 4 + 11 + 4 * struct.calcsize("P")
    with patch("pywhales._launch_limits.os.sysconf", return_value=total):
        check_launch(args, env, policy(100))
    with (
        patch("pywhales._launch_limits.os.sysconf", return_value=total - 1),
        pytest.raises(ObservationLimitError) as failure,
    ):
        check_launch(args, env, policy(100))
    assert failure.value.measured == total
    assert "secret" not in str(failure.value)


@pytest.mark.parametrize(
    "args,env", [(["secret\0arg"], {}), (["cmd"], {"key": "secret\0value"})]
)
def test_nul_is_rejected_without_exposing_values(
    args: list[str], env: dict[str, str]
) -> None:
    with pytest.raises(ObservationLimitError) as failure:
        check_launch(args, env, policy(100))
    assert "secret" not in repr(failure.value)


def test_unknown_native_limit_fails_closed() -> None:
    with (
        patch("pywhales._launch_limits.os.sysconf", side_effect=ValueError("secret")),
        pytest.raises(ObservationLimitError) as failure,
    ):
        check_launch(["cmd"], {}, policy(100))
    assert failure.value.reason == "native_limit_unknown"
    assert failure.value.__cause__ is None


def test_linux_per_string_limit_counts_environment_terminator() -> None:
    with (
        patch("pywhales._launch_limits.sys.platform", "linux"),
        patch(
            "pywhales._launch_limits.os.sysconf",
            side_effect=lambda name: 100000 if name == "SC_ARG_MAX" else 4,
        ),
    ):
        check_launch(["cmd"], {"K": "x" * 125}, policy(100))
        with pytest.raises(ObservationLimitError) as failure:
            check_launch(["cmd"], {"K": "x" * 126}, policy(100))
    assert (failure.value.reason, failure.value.measured) == ("native_string", 129)


def test_macos_does_not_apply_linux_string_limit() -> None:
    with (
        patch("pywhales._launch_limits.sys.platform", "darwin"),
        patch("pywhales._launch_limits.os.sysconf", return_value=100000),
    ):
        check_launch(["cmd", "x" * 1000], {}, policy(2000))


def test_windows_uses_quoted_utf16_command_and_environment() -> None:
    with patch("pywhales._launch_limits.os.name", "nt"):
        assert argv_size(["cmd", "a b", "😀"]) == 26
        check_launch(["cmd", "a b", "😀"], {"K": "V"}, policy(26))
        with pytest.raises(ObservationLimitError):
            check_launch(["cmd"], {"K": "x" * 32765}, policy(100))


def test_windows_command_native_boundary_in_utf16_units() -> None:
    with patch("pywhales._launch_limits.os.name", "nt"):
        check_launch(["x" * 32766], {}, policy(65536))
        with pytest.raises(ObservationLimitError) as failure:
            check_launch(["x" * 32767], {}, policy(65536))
    assert (failure.value.limit, failure.value.measured) == (32767, 32768)


def test_native_argv_size_matches_filesystem_encoding() -> None:
    assert argv_size(["é"]) == len(os.fsencode("é")) + 1
