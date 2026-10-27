import json
import traceback
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from pydantic import JsonValue, ValidationError

from pywhales import (
    Config,
    Container,
    Context,
    Image,
    Network,
    Node,
    Plugin,
    Secret,
    Service,
    Task,
    Volume,
)
from pywhales.client_config import ClientConfig, ParsingError
from pywhales.components.manifest.cli_wrapper import ManifestList
from pywhales.exceptions import NoSuchContainer

type ResourceType = type[
    Config
    | Container
    | Context
    | Image
    | ManifestList
    | Network
    | Node
    | Plugin
    | Secret
    | Service
    | Task
    | Volume
]


@pytest.mark.parametrize(
    "resource_type",
    [
        Config,
        Container,
        Context,
        Image,
        ManifestList,
        Network,
        Node,
        Plugin,
        Secret,
        Service,
        Task,
        Volume,
    ],
)
@pytest.mark.parametrize("raw", [False, True])
def test_scalar_inspect_fetch_validation_is_safe_without_changing_raw_errors(
    resource_type: ResourceType, raw: bool, tmp_path: Path
) -> None:
    secret = "scalar-fetch-diagnostic-sentinel"
    nested: JsonValue = secret
    for _ in range(300):
        nested = [nested]
    snapshot = {secret: nested}
    payload = json.dumps(
        snapshot if resource_type is ManifestList else [snapshot]
    ).encode()
    config = ClientConfig(raw_diagnostics=raw, _client_call_with_path=["mock-cli"])

    with (
        patch("subprocess.run", return_value=CompletedProcess([], 0, payload, b"")),
        patch("pywhales.client_config.tempfile.tempdir", str(tmp_path)),
        pytest.raises(ValidationError if raw else ParsingError) as raised,
    ):
        resource_type(config, "reference")

    assert (secret in "".join(traceback.format_exception(raised.value))) is raw
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "resource_type",
    [
        Config,
        Container,
        Context,
        Image,
        ManifestList,
        Network,
        Node,
        Plugin,
        Secret,
        Service,
        Task,
        Volume,
    ],
)
@pytest.mark.parametrize("raw", [False, True])
def test_scalar_inspect_command_failures_keep_classification_and_raw_attributes(
    resource_type: ResourceType,
    raw: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "execution-diagnostic-sentinel"
    stderr = f"No such container: {secret}"
    config = ClientConfig(raw_diagnostics=raw, _client_call_with_path=["mock-cli"])

    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 7, secret.encode(), stderr.encode()),
        ),
        patch("pywhales.client_config.tempfile.tempdir", str(tmp_path)),
        pytest.raises(NoSuchContainer) as raised,
    ):
        resource_type(config, secret)

    assert (secret in "".join(traceback.format_exception(raised.value))) is raw
    assert raised.value.return_code == 7
    assert raised.value.stdout == secret
    assert raised.value.stderr == stderr
    assert raised.value.docker_command[-1] == secret
    if not raw:
        assert secret not in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
