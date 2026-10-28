"""Automatic diagnostic privacy contracts with mocked images and local children.

Compose streaming launches a real Python child rather than Docker. Every test
runs with debug both unset and enabled, independently of inherited debug flags.
"""

import logging
import sys
from unittest.mock import patch

import pytest

from pywhales import DockerClient, Image
from pywhales.exceptions import NoSuchImage
from pywhales.utils import ProcessStream


@pytest.fixture(params=[False, True], ids=["debug-unset", "debug-set"], autouse=True)
def debug_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolate the debug variable and select the parametrized diagnostic mode.

    Pytest restores the original environment after each test.

    Args:
        request: Supplies whether PYWHALES_DEBUG is enabled for this case.
        monkeypatch: Removes inherited flags and scopes any enabled flag.
    """
    monkeypatch.delenv("PYWHALES_DEBUG", raising=False)
    if request.param:
        monkeypatch.setenv("PYWHALES_DEBUG", "1")


@pytest.mark.parametrize("raw_diagnostics", [False, True], ids=["safe", "raw"])
def test_compose_create_diagnostics_preserve_stream_when_raw_is_opted_in(
    raw_diagnostics: bool,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "compose-service-secret-sentinel"
    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            "import sys; print('|'.join(sys.argv[1:])); print(sys.argv[-1], file=sys.stderr)",
        ]
    )
    if raw_diagnostics:
        client.client_config.raw_diagnostics = True
    caplog.set_level(logging.DEBUG)

    stream = client.compose.create(
        sentinel, build=True, pull="missing", stream_logs=True
    )

    assert isinstance(stream, ProcessStream)
    with stream:
        records = list(stream)
    assert sorted(records) == [
        ("stderr", f"{sentinel}\n".encode()),
        ("stdout", f"compose|create|--build|--pull|missing|{sentinel}\n".encode()),
    ]
    captured = capsys.readouterr()
    assert (sentinel in captured.out) is raw_diagnostics
    if not raw_diagnostics:
        assert sentinel not in captured.err + caplog.text
        assert all(sentinel not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.parametrize("raw_diagnostics", [False, True], ids=["safe", "raw"])
def test_missing_image_diagnostics_preserve_pull_when_raw_is_opted_in(
    raw_diagnostics: bool,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "private-registry/image-secret-sentinel:latest"
    client = DockerClient()
    if raw_diagnostics:
        client.client_config.raw_diagnostics = True
    pulled = Image(client.client_config, "pulled-image-id", is_immutable_id=True)
    caplog.set_level(logging.DEBUG)

    with (
        patch.object(
            client.image,
            "inspect",
            side_effect=NoSuchImage(["docker", "image", "inspect", sentinel], 1),
        ) as inspect,
        patch.object(client.image, "pull", return_value=pulled) as pull,
    ):
        result = client.image._pull_if_necessary(sentinel)

    assert result is pulled
    inspect.assert_called_once_with(sentinel)
    pull.assert_called_once_with(sentinel)
    captured = capsys.readouterr()
    assert (sentinel in captured.out) is raw_diagnostics
    if not raw_diagnostics:
        assert sentinel not in captured.err + caplog.text
        assert all(sentinel not in repr(record.__dict__) for record in caplog.records)
