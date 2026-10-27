import sys
from time import monotonic

import pytest

from pywhales._observation import observation
from pywhales.client_config import ClientConfig
from pywhales.exceptions import DockerTimeout, NoSuchContainer, ObservationTimeout
from pywhales.inventory import InspectionPolicy
from pywhales.telemetry import CompletionRecord, TelemetryRecord
from pywhales.utils import run


@pytest.mark.parametrize("timeout", [False, True])
def test_bounded_capture_deadlines_preserve_timeout_type_and_partial_bytes(
    timeout: bool,
) -> None:
    records: list[TelemetryRecord] = []
    config = ClientConfig(
        client_call=[sys.executable], telemetry_observer=records.append
    )
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=65536, max_response_bytes=1024
    )
    expected = DockerTimeout if timeout else ObservationTimeout
    with (
        pytest.raises(expected),
        observation(policy, deadline=None if timeout else monotonic() + 0.3),
    ):
        run(
            [
                *config.docker_cmd,
                "-c",
                "import os,threading; os.write(1,b'partial\\xff'); threading.Event().wait()",
            ],
            timeout=0.3 if timeout else None,
        )

    assert len(records) == 2
    completed = records[1]
    assert isinstance(completed, CompletionRecord)
    assert (completed.outcome, completed.stdout_bytes, completed.stderr_bytes) == (
        "timeout",
        8,
        0,
    )


@pytest.mark.parametrize("failure", [False, True])
def test_bounded_capture_preserves_results_and_errors_when_observer_throws(
    failure: bool,
) -> None:
    records: list[TelemetryRecord] = []

    def throwing(record: TelemetryRecord) -> None:
        records.append(record)
        raise RuntimeError("private-observer-error")

    config = ClientConfig(client_call=[sys.executable], telemetry_observer=throwing)
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=65536, max_response_bytes=1024
    )
    args = [
        *config.docker_cmd,
        "-c",
        "import os; os.write(1,b'out\\xff\\n'); os.write(2,b'No such container: private'); "
        + ("raise SystemExit(7)" if failure else "pass"),
    ]

    with observation(policy):
        if failure:
            with pytest.raises(NoSuchContainer) as raised:
                run(args)
            assert raised.value.return_code == 7
            assert raised.value.stdout == "out\ufffd\n"
        else:
            assert run(args) == "out\ufffd"

    assert len(records) == 2
    completed = records[1]
    assert isinstance(completed, CompletionRecord)
    assert completed.outcome == ("failure" if failure else "success")
    assert (completed.stdout_bytes, completed.stderr_bytes) == (5, 26)
