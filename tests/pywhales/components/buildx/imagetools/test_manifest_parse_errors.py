"""Safe and raw parse failures for both imagetools manifest parses, daemon-free."""

import json
import subprocess
import traceback
from collections.abc import Callable
from typing import Final
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ParsingError
from pywhales.exceptions import DockerException

SECRET: Final = "manifest-diagnostic-sentinel"
SURFACES: Final = {
    "inspect": lambda c: c.buildx.imagetools.inspect("example:latest"),
    "create-dry-run": lambda c: c.buildx.imagetools.create(
        ["example:latest"], dry_run=True
    ),
}
PAYLOADS: Final = {
    "malformed": (b"not-json", json.JSONDecodeError, False),
    "wrong-shape": (b"[]", TypeError, False),
    "invalid-model": (
        json.dumps({"mediaType": [SECRET], "schemaVersion": 2}).encode(),
        ValueError,
        True,
    ),
}


def _client(*, raw: bool) -> DockerClient:
    client = DockerClient(raw_diagnostics=raw)
    client.client_config._client_call_with_path = ["fake-docker"]
    return client


def _done(payload: bytes, code: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], code, payload, b"")


@pytest.mark.parametrize("raw", [False, True], ids=["safe", "raw"])
@pytest.mark.parametrize("payload", PAYLOADS.values(), ids=PAYLOADS.keys())
@pytest.mark.parametrize("surface", SURFACES.values(), ids=SURFACES.keys())
def test_manifest_parse_failure_is_parsing_error_by_policy(
    surface: Callable[[DockerClient], object],
    payload: tuple[bytes, type[Exception], bool],
    raw: bool,
) -> None:
    """Given a rejected manifest, when parsed, then policy decides chaining."""
    body, cause, leaks = payload
    with (
        patch("subprocess.run", return_value=_done(body)),
        pytest.raises(ParsingError) as raised,
    ):
        surface(_client(raw=raw))

    error = raised.value
    rendered = str(error) + "".join(traceback.format_exception(error))
    if raw:
        assert isinstance(error.__cause__, cause)
        assert (SECRET in rendered) is leaks
    else:
        assert error.__cause__ is None
        assert error.__context__ is None
        assert error.__suppress_context__ is True
        assert SECRET not in rendered


@pytest.mark.parametrize("surface", SURFACES.values(), ids=SURFACES.keys())
def test_manifest_command_failure_is_not_a_parsing_error(
    surface: Callable[[DockerClient], object],
) -> None:
    """Given a failing CLI, when called, then the launch error is untouched."""
    with (
        patch("subprocess.run", return_value=_done(b"", 1)),
        pytest.raises(DockerException),
    ):
        surface(_client(raw=False))


def test_create_without_dry_run_ignores_output() -> None:
    """Given a real create, then its output is not parsed as a manifest."""
    with patch("subprocess.run", return_value=_done(b"not-json")):
        assert _client(raw=False).buildx.imagetools.create(["example:latest"]) is None
