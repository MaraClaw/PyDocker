import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from threading import Barrier

import pytest

import pywhales.utils
from pywhales.client_config import ClientConfig, Command
from pywhales.exceptions import DockerException, NoSuchContainer


def test_debug_level_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    command = [
        *ClientConfig(client_call=["echo"], raw_diagnostics=True).docker_cmd,
        "hello",
    ]
    pywhales.utils.run(command)
    assert " ".join(str(arg) for arg in command) in caplog.text


def test_default_diagnostics_hide_payloads_without_altering_execution(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "safe-secret-sentinel"
    monkeypatch.setenv("PYDOCKER_DEBUG", "1")
    monkeypatch.setenv("INHERITED_SECRET", secret)
    caplog.set_level(logging.DEBUG)
    command = [
        sys.executable,
        "-c",
        "import os, sys; print(sys.argv[1]); print(sys.stdin.read()); print(os.environ['INHERITED_SECRET']); print(os.environ['CUSTOM_SECRET'])",
        secret,
    ]

    output = pywhales.utils.run(
        command, input=secret.encode(), env={"CUSTOM_SECRET": secret}
    )

    assert output.splitlines() == [secret] * 4
    assert secret not in capsys.readouterr().out + caplog.text
    assert all(secret not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.parametrize(
    "composition",
    ["addition", "copy", "list_copy", "deepcopy", "expansion", "command", "compose"],
)
def test_concurrent_clients_keep_policy_through_command_composition(
    composition: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("PYDOCKER_DEBUG", "1")
    barrier = Barrier(2)

    def execute(raw: bool) -> DockerException:
        secret = "raw-client-sentinel" if raw else "safe-client-sentinel"
        config = ClientConfig(
            client_call=[
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1]); sys.stderr.write('No such container: ' + sys.argv[1]); sys.exit(7)",
                secret,
            ],
            raw_diagnostics=not raw,
        )
        command = (
            config.docker_compose_cmd if composition == "compose" else config.docker_cmd
        )
        commands = {
            "addition": command.__add__(["extra"]),
            "copy": copy(command),
            "list_copy": command.copy(),
            "deepcopy": deepcopy(command),
            "expansion": [*command],
            "command": Command(command),
            "compose": command,
        }
        config.raw_diagnostics = raw
        barrier.wait(timeout=3)
        with pytest.raises(DockerException) as raised:
            pywhales.utils.run(commands[composition])
        return raised.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        errors = list(pool.map(execute, [False, True]))

    debug_output = capsys.readouterr().out
    assert "safe-client-sentinel" not in caplog.text + debug_output + str(errors[0])
    assert "raw-client-sentinel" in caplog.text and "raw-client-sentinel" in str(
        errors[1]
    )
    assert errors[0].stdout == "safe-client-sentinel\n"
    assert errors[1].stdout == "raw-client-sentinel\n"
    assert all(error.return_code == 7 for error in errors)
    assert all(isinstance(error, NoSuchContainer) for error in errors)
