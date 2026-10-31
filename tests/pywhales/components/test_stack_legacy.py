"""Stack deployment behavior without a Docker daemon."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from pywhales import DockerClient
from pywhales.exceptions import DockerException


@pytest.mark.parametrize("orchestrator", [None, "", "swarm"])
def test_removed_orchestrator_keyword_is_rejected(
    monkeypatch: pytest.MonkeyPatch, orchestrator: str | None
) -> None:
    client = DockerClient(client_call=[sys.executable])
    command = Mock(return_value="")
    deploy = Mock(wraps=client.stack.deploy)
    monkeypatch.setattr("pywhales.components.stack.cli_wrapper.run", command)

    with pytest.raises(TypeError, match="orchestrator"):
        deploy("example", orchestrator=orchestrator)

    command.assert_not_called()


def test_deploy_defaults_when_orchestrator_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DockerClient(client_call=[sys.executable])
    command = Mock(return_value="")
    monkeypatch.setattr("pywhales.components.stack.cli_wrapper.run", command)

    stack = client.stack.deploy("example")

    command.assert_called_once()
    assert command.call_args.args[0] == [
        sys.executable,
        "stack",
        "deploy",
        "--resolve-image",
        "always",
        "example",
    ]
    assert command.call_args.kwargs == {
        "capture_stdout": False,
        "env": {},
        "timeout": None,
    }
    assert stack.name == "example"
    assert stack.client_config is client.client_config


@pytest.mark.parametrize("raw_diagnostics", [False, True])
def test_deploy_positional_arguments_and_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_diagnostics: bool,
) -> None:
    client = DockerClient(client_call=[sys.executable], raw_diagnostics=raw_diagnostics)
    env_file = tmp_path / "secrets.env"
    env_file.write_text("OVERRIDE=file-secret\nFILE_ONLY=file-only-secret\n")
    command = Mock(return_value="")
    monkeypatch.setattr("pywhales.components.stack.cli_wrapper.run", command)

    stack = client.stack.deploy(
        "secret-stack",
        ["secret-compose.yml"],
        True,
        "never",
        True,
        [env_file],
        {"OVERRIDE": "variable-secret"},
    )

    command.assert_called_once()
    assert command.call_args.args[0] == [
        sys.executable,
        "stack",
        "deploy",
        "--compose-file",
        "secret-compose.yml",
        "--prune",
        "--resolve-image",
        "never",
        "--with-registry-auth",
        "secret-stack",
    ]
    assert command.call_args.kwargs == {
        "capture_stdout": False,
        "env": {"OVERRIDE": "variable-secret", "FILE_ONLY": "file-only-secret"},
        "timeout": None,
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
    sys.stderr.write('deployment rejected\\n')
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
    stack = fake_cli.stack.deploy("example")

    assert capfd.readouterr().out == "stack deployed\n"
    assert stack.name == "example"
    assert stack.client_config is fake_cli.client_config
    launches = (tmp_path / "launches.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in launches] == [
        ["stack", "deploy", "--resolve-image", "always", "example"]
    ]


def test_fake_cli_rejection_propagates_without_retry(
    fake_cli: DockerClient, tmp_path: Path
) -> None:
    fake_cli.client_config.client_call[-1] = "reject"
    with pytest.raises(DockerException) as failure:
        fake_cli.stack.deploy("example")

    assert type(failure.value) is DockerException
    assert failure.value.return_code == 23
    assert failure.value.stderr == "deployment rejected\n"
    assert failure.value.stdout is None
    launches = (tmp_path / "launches.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in launches] == [
        ["stack", "deploy", "--resolve-image", "always", "example"]
    ]
