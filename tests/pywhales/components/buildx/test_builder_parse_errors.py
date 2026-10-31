"""Safe and raw parse failures for Buildx builder reload and public list().

Everything runs behind a patched transport, with no Docker CLI or daemon.
"""

import json
import subprocess
import traceback
from typing import Final
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ParsingError
from pywhales.components.buildx.cli_wrapper import Builder
from pywhales.exceptions import DockerException

SECRET: Final = "builder-diagnostic-sentinel"
INVALID_LINES: Final = {
    "malformed": ("not-json", json.JSONDecodeError),
    "wrong-shape": ("[1]", TypeError),
    "invalid-model": (json.dumps({"Name": {"leak": SECRET}}), ValueError),
}


def _client(*, raw: bool) -> DockerClient:
    client = DockerClient(raw_diagnostics=raw)
    client.client_config._client_call_with_path = ["fake-docker"]
    return client


def _listing(*lines: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], 0, "\n".join(lines).encode(), b"")


def _rendered(error: BaseException) -> str:
    return str(error) + "".join(traceback.format_exception(error))


def _assert_safe(error: ParsingError) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is True
    assert SECRET not in _rendered(error)


@pytest.mark.parametrize("raw", [False, True], ids=["safe", "raw"])
@pytest.mark.parametrize("line", INVALID_LINES.values(), ids=INVALID_LINES.keys())
def test_reload_invalid_line_is_parsing_error_by_policy(
    line: tuple[str, type[Exception]], raw: bool
) -> None:
    """Given an invalid listing line, when reloading, then policy decides chaining."""
    client = _client(raw=raw)
    builder = Builder(client.client_config, "wanted", is_immutable_id=True)

    with (
        patch("subprocess.run", return_value=_listing(line[0])),
        pytest.raises(ParsingError) as raised,
    ):
        builder.reload()

    if raw:
        assert isinstance(raised.value.__cause__, line[1])
    else:
        _assert_safe(raised.value)


def test_reload_stops_at_the_matching_line() -> None:
    """Given a match before a bad line, then the later line is never parsed."""
    builder = Builder(_client(raw=False).client_config, "wanted", is_immutable_id=True)
    listing = _listing(json.dumps({"Name": "wanted", "Driver": "docker"}), "not-json")

    with patch("subprocess.run", return_value=listing):
        assert builder.driver == "docker"


@pytest.mark.parametrize("raw", [False, True], ids=["safe", "raw"])
def test_reload_missing_builder_names_it_only_in_raw_mode(raw: bool) -> None:
    """Given an absent builder, when reloading, then only raw mode names it."""
    name = f"{SECRET}-name"
    builder = Builder(_client(raw=raw).client_config, name, is_immutable_id=True)

    with (
        patch("subprocess.run", return_value=_listing('{"Name":"other"}')),
        pytest.raises(ValueError, match="Could not find a builder") as raised,
    ):
        builder.reload()

    assert (name in _rendered(raised.value)) is raw


@pytest.mark.parametrize("raw", [False, True], ids=["safe", "raw"])
@pytest.mark.parametrize("line", INVALID_LINES.values(), ids=INVALID_LINES.keys())
def test_list_invalid_line_is_parsing_error_by_policy(
    line: tuple[str, type[Exception]], raw: bool
) -> None:
    """Given an invalid line among valid ones, when listed, then it is rejected."""
    with (
        patch("subprocess.run", return_value=_listing('{"Name":"ok"}', line[0])),
        pytest.raises(ParsingError) as raised,
    ):
        _client(raw=raw).buildx.list()

    if raw:
        assert isinstance(raised.value.__cause__, line[1])
    else:
        _assert_safe(raised.value)


@pytest.mark.parametrize("raw", [False, True], ids=["safe", "raw"])
def test_list_nameless_builder_is_parsing_error(raw: bool) -> None:
    """Given a builder without a name, when listed, then no handle is built."""
    with (
        patch("subprocess.run", return_value=_listing('{"Driver":"docker"}')),
        pytest.raises(ParsingError) as raised,
    ):
        _client(raw=raw).buildx.list()

    if raw:
        assert isinstance(raised.value.__cause__, ValueError)
    else:
        _assert_safe(raised.value)


def test_list_stays_lenient() -> None:
    """Given repeated, conflicting, and lax-typed lines, then none is rejected."""
    lines = (
        '{"Name":"a","Driver":"docker"}',
        '{"Name":"a","Driver":"docker"}',
        '{"Name":"a","Driver":"other"}',
        '{"Name":"b","Dynamic":"true"}',
    )
    with patch("subprocess.run", return_value=_listing(*lines)) as transport:
        builders = _client(raw=False).buildx.list()
        snapshots = sorted((b.name, b.driver, b.dynamic) for b in builders)

    assert snapshots == [
        ("a", "docker", None),
        ("a", "other", None),
        ("b", None, True),
    ]
    assert transport.call_count == 1


def test_list_command_failure_is_not_a_parsing_error() -> None:
    """Given a failing CLI, when listed, then the launch error is untouched."""
    failed = subprocess.CompletedProcess([], 1, b"", b"boom")
    with patch("subprocess.run", return_value=failed), pytest.raises(DockerException):
        _client(raw=False).buildx.list()
