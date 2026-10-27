"""Stack legacy option compatibility without a Docker daemon."""

import json
import sys
import warnings
from pathlib import Path
from unittest.mock import Mock

import pytest

from pywhales import DockerClient
from pywhales.exceptions import DockerException


@pytest.mark.parametrize("explicit_none", [False, True])
def test_omitted_or_none_orchestrator_has_no_warning_or_flag(
    monkeypatch: pytest.MonkeyPatch, explicit_none: bool
) -> None:
    client = DockerClient(client_call=[sys.executable])
    command = Mock(return_value="")
    monkeypatch.setattr("pywhales.components.stack.cli_wrapper.run", command)

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        if explicit_none:
            stack = client.stack.deploy("example", orchestrator=None)
        else:
            stack = client.stack.deploy("example")

    assert recorded == []
    command.assert_called_once()
    assert command.call_args.args[0] == [
        sys.executable,
        "stack",
        "deploy",
        "--resolve-image",
        "always",
        "example",
    ]
    assert command.call_args.kwargs == {"capture_stdout": False, "env": {}}
    assert stack.name == "example"
    assert stack.client_config is client.client_config


@pytest.mark.parametrize("orchestrator", ["", "sensitive-orchestrator-value"])
@pytest.mark.parametrize("raw_diagnostics", [False, True])
def test_legacy_warning_precedes_run_and_preserves_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    orchestrator: str,
    raw_diagnostics: bool,
) -> None:
    client = DockerClient(client_call=[sys.executable], raw_diagnostics=raw_diagnostics)
    env_file = tmp_path / "secrets.env"
    env_file.write_text("OVERRIDE=file-secret\nFILE_ONLY=file-only-secret\n")
    command = Mock(return_value="")
    monkeypatch.setattr("pywhales.components.stack.cli_wrapper.run", command)

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")

        def check_warning_before_run(*args: object, **kwargs: object) -> str:
            assert len(recorded) == 1
            return ""

        command.side_effect = check_warning_before_run
        call_line = sys._getframe().f_lineno + 1
        stack = client.stack.deploy(
            "secret-stack",
            ["secret-compose.yml"],
            orchestrator,
            True,
            "never",
            True,
            [env_file],
            {"OVERRIDE": "variable-secret"},
        )

    assert len(recorded) == 1
    warning = recorded[0]
    assert warning.category is DeprecationWarning
    assert warning.filename == __file__
    assert warning.lineno == call_line
    message = str(warning.message)
    for secret in (
        orchestrator,
        "secret-stack",
        "secret-compose.yml",
        "file-secret",
        "file-only-secret",
        "variable-secret",
        str(env_file),
    ):
        if secret:
            assert secret not in message
    command.assert_called_once()
    assert command.call_args.args[0] == [
        sys.executable,
        "stack",
        "deploy",
        "--compose-file",
        "secret-compose.yml",
        "--orchestrator",
        orchestrator,
        "--prune",
        "--resolve-image",
        "never",
        "--with-registry-auth",
        "secret-stack",
    ]
    assert command.call_args.kwargs == {
        "capture_stdout": False,
        "env": {"OVERRIDE": "variable-secret", "FILE_ONLY": "file-only-secret"},
    }
    assert stack.name == "secret-stack"
    assert stack.client_config is client.client_config


@pytest.fixture
def fake_cli(tmp_path: Path) -> DockerClient:
    driver = tmp_path / "driver.py"
    driver.write_text(
        """import json
import sys
from pathlib import Path

record = Path(sys.argv[1])
mode = sys.argv[2]
args = sys.argv[3:]
with record.open('a') as output:
    output.write(json.dumps(args) + '\\n')
if mode == 'reject':
    sys.stderr.write('unknown flag: --orchestrator\\n')
    sys.exit(23)
print('stack deployed', flush=True)
"""
    )
    return DockerClient(
        client_call=[
            sys.executable,
            str(driver),
            str(tmp_path / "launches.jsonl"),
            "success",
        ]
    )


def test_fake_cli_success_preserves_stdout_and_stack(
    fake_cli: DockerClient, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with pytest.warns(DeprecationWarning) as recorded:
        stack = fake_cli.stack.deploy("example", orchestrator="swarm")

    assert len(recorded) == 1
    assert capfd.readouterr().out == "stack deployed\n"
    assert stack.name == "example"
    assert stack.client_config is fake_cli.client_config
    launches = (tmp_path / "launches.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in launches] == [
        [
            "stack",
            "deploy",
            "--orchestrator",
            "swarm",
            "--resolve-image",
            "always",
            "example",
        ]
    ]


def test_fake_cli_rejection_propagates_without_retry(
    fake_cli: DockerClient, tmp_path: Path
) -> None:
    fake_cli.client_config.client_call[-1] = "reject"
    with (
        pytest.warns(DeprecationWarning) as recorded,
        pytest.raises(DockerException) as failure,
    ):
        fake_cli.stack.deploy("example", orchestrator="swarm")

    assert len(recorded) == 1
    assert type(failure.value) is DockerException
    assert failure.value.return_code == 23
    assert failure.value.stderr == "unknown flag: --orchestrator\n"
    assert failure.value.stdout is None
    launches = (tmp_path / "launches.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in launches] == [
        [
            "stack",
            "deploy",
            "--orchestrator",
            "swarm",
            "--resolve-image",
            "always",
            "example",
        ]
    ]
