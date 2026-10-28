import json
import subprocess
import sys
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales._observation import observation
from pywhales.client_config import ClientConfig, ParsingError, bulk_reload
from pywhales.components.context.cli_wrapper import Context
from pywhales.convergence import Failure, ServiceTarget, capture_service, wait
from pywhales.exceptions import DockerException, ObservationLimitError
from pywhales.inventory import InspectionPolicy, InventoryRequest, capture_inventory
from pywhales.telemetry import CompletionRecord, TelemetryRecord
from pywhales.utils import run

BASE = InspectionPolicy(max_references=1, max_argv_bytes=65536, max_response_bytes=1024)


def test_nested_siblings_charge_parent_and_allow_exact_limit() -> None:
    parent = replace(BASE, max_total_response_bytes=6)
    with observation(parent):
        with observation(BASE):
            assert run([sys.executable, "-c", "print('abc', end='')"]) == "abc"
        with observation(BASE):
            assert run([sys.executable, "-c", "print('def', end='')"]) == "def"
        with pytest.raises(ObservationLimitError), observation(BASE):
            run([sys.executable, "-c", "print('x', end='')"])


def test_policy_reuse_has_fresh_root_budget() -> None:
    policy = replace(BASE, max_total_response_bytes=1)
    for _ in range(2):
        with observation(policy):
            assert run([sys.executable, "-c", "print('x', end='')"]) == "x"


@pytest.mark.parametrize("field", ["max_total_references", "max_total_response_bytes"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "1"])
def test_optional_limits_reject_nonpositive_or_noninteger_values(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        replace(BASE, **{field: value})


def test_reference_preflight_counts_duplicates_before_normalization_and_lookup() -> (
    None
):
    config = ClientConfig()
    request = InventoryRequest(
        kind="context", references=("secret", "secret"), client_config=config
    )
    with (
        observation(replace(BASE, max_total_references=1)),
        patch("pywhales._inventory_staging._normalize") as normalize,
        patch("shutil.which") as lookup,
        pytest.raises(ObservationLimitError) as failure,
    ):
        capture_inventory([request], policy=BASE)
    assert (failure.value.reason, failure.value.limit, failure.value.measured) == (
        "total_references",
        1,
        2,
    )
    assert "secret" not in "".join(traceback.format_exception(failure.value))
    normalize.assert_not_called()
    lookup.assert_not_called()


def test_reference_limit_is_per_inventory_and_duplicates_are_detached() -> None:
    config = ClientConfig(
        _client_call_with_path=[sys.executable, "-c", 'print(\'[ {"Name":"x"} ]\')']
    )
    request = InventoryRequest(
        kind="context", references=("x", "x"), client_config=config
    )
    policy = replace(BASE, max_total_references=2)
    with observation(policy):
        first = capture_inventory([request], policy=BASE)
        second = capture_inventory([request], policy=BASE)
    assert [item.id for item in first.records] == ["x", "x"]
    assert first.records[0].payload is not first.records[1].payload
    assert len(second.records) == 2


def test_failed_command_still_consumes_parent_capacity() -> None:
    with observation(replace(BASE, max_total_response_bytes=4)):
        with pytest.raises(DockerException), observation(BASE):
            run(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(2,b'abc'); raise SystemExit(1)",
                ]
            )
        with pytest.raises(ObservationLimitError), observation(BASE):
            run([sys.executable, "-c", "print('de',end='')"])


def test_tighter_child_overflow_still_charges_parent_and_later_sibling() -> None:
    with observation(replace(BASE, max_total_response_bytes=6)):
        with (
            observation(replace(BASE, max_total_response_bytes=2)),
            pytest.raises(ObservationLimitError),
        ):
            run([sys.executable, "-c", "print('abcd',end='')"])
        with observation(BASE):
            assert run([sys.executable, "-c", "print('abc',end='')"]) == "abc"
        with pytest.raises(ObservationLimitError), observation(BASE):
            run([sys.executable, "-c", "print('x',end='')"])


def test_per_command_overflow_bytes_remain_charged_to_scope() -> None:
    policy = replace(BASE, max_response_bytes=2, max_total_response_bytes=4)
    with observation(policy):
        with pytest.raises(ObservationLimitError) as failure:
            run([sys.executable, "-c", "print('abc',end='')"])
        assert failure.value.reason == "response"
        with pytest.raises(ObservationLimitError):
            run([sys.executable, "-c", "print('de',end='')"])


def test_exact_total_counts_stdout_and_stderr_raw_bytes() -> None:
    with observation(replace(BASE, max_total_response_bytes=4)):
        result = run(
            [sys.executable, "-c", "import os; os.write(1,b'ab'); os.write(2,b'cd')"],
            return_stderr=True,
        )
    assert result == ("ab", "cd")


def test_parse_failure_still_consumes_parent_capacity() -> None:
    config = ClientConfig(
        _client_call_with_path=[sys.executable, "-c", "print('bad',end='')"]
    )
    request = InventoryRequest(kind="context", references=("x",), client_config=config)
    with observation(replace(BASE, max_total_response_bytes=5)):
        with pytest.raises(ParsingError):
            capture_inventory([request], policy=BASE)
        with pytest.raises(ObservationLimitError):
            run([sys.executable, "-c", "print('abc',end='')"])


def test_later_bulk_chunk_overflow_preserves_cache_and_deduplicates() -> None:
    code = 'import json,sys; print(json.dumps([{"Name": value} for value in sys.argv[3:]]))'
    config = ClientConfig(_client_call_with_path=[sys.executable, "-c", code])
    first = Context(config, "a", is_immutable_id=True)
    second = Context(config, "b", is_immutable_id=True)
    before = [
        (item._inspect_result, item._last_refreshed_time) for item in (first, second)
    ]
    response_size = len(json.dumps([{"Name": "a"}]).encode()) + 1
    with pytest.raises(ObservationLimitError):
        bulk_reload(
            [first, first, second],
            policy=replace(
                BASE, max_total_references=2, max_total_response_bytes=response_size
            ),
        )
    assert [
        (item._inspect_result, item._last_refreshed_time) for item in (first, second)
    ] == before


def test_aggregate_overflow_reaps_child_and_reports_actual_bytes() -> None:
    records: list[TelemetryRecord] = []
    children: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def launch(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        process = popen(args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds)
        children.append(process)
        return process

    config = ClientConfig(
        client_call=[sys.executable], telemetry_observer=records.append
    )
    with observation(replace(BASE, max_total_response_bytes=4)):
        run([*config.docker_cmd, "-c", "print('ab',end='')"])
        with (
            patch("pywhales._bounded_capture.subprocess.Popen", side_effect=launch),
            pytest.raises(ObservationLimitError) as failure,
        ):
            run(
                [
                    *config.docker_cmd,
                    "-c",
                    "import os,threading; os.write(2,b'X'*8); threading.Event().wait()",
                ]
            )
    assert (failure.value.reason, failure.value.limit, failure.value.measured) == (
        "total_response",
        4,
        5,
    )
    assert "XXXXXXXX" not in "".join(traceback.format_exception(failure.value))
    assert len(children) == 1 and children[0].returncode is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed
    assert isinstance(records[-1], CompletionRecord)
    assert (records[-1].stdout_bytes, records[-1].stderr_bytes) == (0, 3)


def test_wait_cycles_share_budget(tmp_path: Path) -> None:
    service = {
        "ID": "s",
        "Version": {"Index": 1},
        "Spec": {
            "Mode": {"Replicated": {"Replicas": 1}},
            "TaskTemplate": {"ContainerSpec": {"Image": "image"}},
        },
    }
    payload = json.dumps([service]) + "\n"
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import pathlib,sys\n"
        "root=pathlib.Path(__file__).parent\n"
        "with (root/'calls').open('a') as stream: stream.write(' '.join(sys.argv[1:])+'\\n')\n"
        f"if sys.argv[1:3] == ['service','inspect']: sys.stdout.write({payload!r})\n"
    )
    client = DockerClient(client_call=[sys.executable, str(script)])
    target = capture_service(client, "s", policy=BASE, timeout=3)
    assert isinstance(target, ServiceTarget)
    (tmp_path / "calls").unlink()
    result = wait(
        target,
        policy=replace(BASE, max_total_response_bytes=2 * len(payload.encode())),
        timeout=3,
        poll_interval=0.01,
    )
    assert isinstance(result, Failure) and result.reason == "observation"
    assert isinstance(result.cause, ObservationLimitError)
    assert result.cause.reason == "total_response"
    assert (tmp_path / "calls").read_text().splitlines().count("service inspect s") == 3
