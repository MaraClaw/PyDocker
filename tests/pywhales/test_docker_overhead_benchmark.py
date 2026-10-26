import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from scripts.benchmark_docker_overhead import (
    IMAGE_ID,
    Options,
    SimulatedExecution,
    main,
    run_benchmark,
)

SCRIPT: Final = (
    Path(__file__).resolve().parents[2] / "scripts" / "benchmark_docker_overhead.py"
)


def test_counts_when_real_library_uses_simulated_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    options = Options(repetitions=3)

    report = run_benchmark(options)

    assert {
        item.name: (
            item.execution_requests,
            item.builder_inspect_requests,
            item.builder_list_requests,
        )
        for item in report.scenarios
    } == {
        "bake_default": (6, 0, 0),
        "bake_without_config": (3, 0, 0),
        "build_image": (12, 3, 3),
        "build_push": (3, 0, 0),
        "build_export": (3, 0, 0),
    }


def test_json_schema_when_cli_runs_without_docker(tmp_path: Path) -> None:
    environment = {**os.environ, "PATH": str(tmp_path), "PYDOCKER_DEBUG": "1"}

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--repetitions", "2"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )

    data = json.loads(completed.stdout)
    assert set(data) == {
        "schema_version",
        "measurement",
        "docker_executed",
        "python_version",
        "scenarios",
    }
    assert data["schema_version"] == 1
    assert data["measurement"] == "simulated_wrapper_overhead_not_docker_speedup"
    assert data["docker_executed"] is False
    assert len(data["scenarios"]) == 5
    for scenario in data["scenarios"]:
        assert set(scenario) == {
            "name",
            "iterations",
            "execution_requests",
            "builder_inspect_requests",
            "builder_list_requests",
            "simulated_wrapper_timing",
        }
        assert scenario["iterations"] == 2
        timing = scenario["simulated_wrapper_timing"]
        assert set(timing) == {"min_ns", "median_ns", "max_ns"}
        assert 0 <= timing["min_ns"] <= timing["median_ns"] <= timing["max_ns"]


def test_fake_execution_when_image_id_file_is_requested(tmp_path: Path) -> None:
    iidfile = tmp_path / "image-id"
    execution = SimulatedExecution()

    completed = execution.run(
        ["sentinel", "buildx", "build", "--iidfile", str(iidfile), "."]
    )

    assert completed.returncode == 0
    assert iidfile.read_text() == IMAGE_ID
    assert len(execution.commands) == 1


def test_fake_execution_when_unexpected_command_is_requested() -> None:
    execution = SimulatedExecution()

    with pytest.raises(RuntimeError):
        execution.run(["sentinel", "system", "prune"])


@pytest.mark.parametrize(
    "arguments",
    [
        ["--repetitions", "0"],
        ["--repetitions", "-1"],
        ["--repetitions", "1001"],
        ["--repetitions", "abc"],
        ["--unknown"],
    ],
)
def test_cli_rejects_invalid_arguments_when_parsing(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 2


def test_help_when_execution_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")

    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0


def test_default_repetitions_when_cli_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main([])

    output = json.loads(capsys.readouterr().out)
    assert [item["iterations"] for item in output["scenarios"]] == [20] * 5
