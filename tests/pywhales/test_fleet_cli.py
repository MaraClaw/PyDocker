"""Real offline CLI surfaces and input-safe error rendering."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest

from scripts.fleet_contract.__main__ import main

FIXTURES: Final = (
    Path(__file__).resolve().parents[2] / "scripts/fleet_contract/fixtures"
)


@pytest.mark.parametrize("kind", ["manifest", "support", "result", "evidence"])
def test_schema_export_when_external_contact_is_forbidden(
    kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("process contact")
        ),
        patch.object(socket, "socket", side_effect=AssertionError("socket contact")),
    ):
        status = main(["schema", kind])

    schema = json.loads(capsys.readouterr().out)
    assert status == 0
    assert schema["additionalProperties"] is False
    assert schema["properties"] and schema["$defs"]


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("manifest", "synthetic-runnable"),
        ("manifest", "draft"),
        ("support", "support-matrix"),
        ("result", "incomplete-result"),
    ],
)
def test_cli_normalization_when_docker_is_unavailable(
    kind: str, name: str, tmp_path: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.fleet_contract",
            "validate",
            kind,
            "--input",
            str(FIXTURES / f"{name}.json"),
        ],
        env={**os.environ, "PATH": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )

    assert json.loads(completed.stdout) == json.loads(
        (FIXTURES / f"{name}.json").read_text()
    )


@pytest.mark.parametrize(
    ("name", "complete"), [("synthetic-result", True), ("incomplete-result", False)]
)
def test_report_cli_when_no_docker_is_on_path(
    name: str, complete: bool, tmp_path: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.fleet_contract",
            "report",
            "--input",
            str(FIXTURES / f"{name}.json"),
            "--manifest",
            str(FIXTURES / "synthetic-runnable.json"),
            "--support",
            str(FIXTURES / "support-matrix.json"),
        ],
        env={**os.environ, "PATH": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )

    report = json.loads(completed.stdout)
    assert report["evidence_complete"] is complete
    assert report["qualification"] == "unqualified"
    assert (
        report["evidence"]["result"]["outcomes"]
        == json.loads((FIXTURES / f"{name}.json").read_text())["outcomes"]
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"credential-secret-key":"credential-secret-value"}',
        '{"identity":{"profile":"credential-secret-value"}}',
        "not-json-credential-secret-value",
        '{"schema_version":NaN}',
    ],
)
def test_safe_errors_when_input_is_invalid(
    raw: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "credential-secret-filename.json"
    path.write_text(raw)

    status = main(["validate", "--input", str(path)])

    output = capsys.readouterr()
    assert status == 2 and output.out == ""
    assert "credential-secret" not in output.err
    errors = json.loads(output.err)["errors"]
    assert errors and {error["code"] for error in errors} <= {"missing", "invalid"}
    assert all(error["path"].startswith("$") for error in errors)


def test_error_exit_when_real_cli_receives_missing_authorization(
    tmp_path: Path,
) -> None:
    raw = json.loads((FIXTURES / "synthetic-runnable.json").read_text())
    del raw["authorization"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw))

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.fleet_contract",
            "validate",
            "--input",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 2
    assert {"code": "missing", "path": "$.authorization"} in json.loads(
        completed.stderr
    )["errors"]
    assert completed.stdout == ""


def test_argument_errors_when_secret_values_are_supplied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        main(["secret-command-value"])

    assert error.value.code == 2
    output = capsys.readouterr()
    assert "secret-command-value" not in output.err
    assert json.loads(output.err)["errors"] == [{"code": "arguments", "path": "$"}]


def test_report_when_all_external_contact_is_forbidden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("process contact")
        ),
        patch.object(subprocess, "run", side_effect=AssertionError("process contact")),
        patch.object(socket, "socket", side_effect=AssertionError("socket contact")),
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS contact")),
    ):
        status = main(
            [
                "report",
                "--manifest",
                str(FIXTURES / "synthetic-runnable.json"),
                "--support",
                str(FIXTURES / "support-matrix.json"),
                "--input",
                str(FIXTURES / "incomplete-result.json"),
            ]
        )

    assert status == 0
    assert json.loads(capsys.readouterr().out)["qualification"] == "unqualified"


def test_unreadable_input_when_filename_contains_private_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(["validate", "--input", str(tmp_path / "private-credential.json")])

    output = capsys.readouterr()
    assert status == 2
    assert "private-credential" not in output.err
    assert json.loads(output.err)["errors"] == [
        {"code": "input_unreadable", "path": "$"}
    ]
