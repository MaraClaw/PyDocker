"""Service rollout contracts exercised with local Python subprocesses only."""

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import TypedDict

import pytest

from pywhales import DockerClient, Service
from pywhales.enums import FailureAction, RollbackFailureActionInput, UpdateOrder
from pywhales.exceptions import DockerException, NoSuchService


class PolicyArguments(TypedDict):
    update_parallelism: int
    update_delay: timedelta
    update_failure_action: FailureAction
    update_monitor: int
    update_max_failure_ratio: float
    update_order: UpdateOrder
    rollback_parallelism: int
    rollback_delay: int
    rollback_failure_action: RollbackFailureActionInput
    rollback_monitor: timedelta
    rollback_max_failure_ratio: float
    rollback_order: UpdateOrder


@pytest.fixture
def fake_cli(tmp_path: Path) -> tuple[DockerClient, Path]:
    script = tmp_path / "cli.py"
    log = tmp_path / "calls.jsonl"
    script.write_text(
        "import json, sys\n"
        "with open(sys.argv[1], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[2:]) + '\\n')\n"
        "if sys.argv[-1] in ('missing', 'secret-failure'):\n"
        "    print('no such service' if sys.argv[-1] == 'missing' else 'secret error', file=sys.stderr)\n"
        "    sys.exit(17)\n"
    )
    return DockerClient(
        client_call=[sys.executable, str(script), str(log)], host="example"
    ), log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_legacy_positional_cli_and_handle(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    handle = Service(client.client_config, "identity", is_immutable_id=True)
    assert client.service.update("identity", True, True, "image", True, True, 0) is None
    assert handle.update(True, True, "image", True, True, 0) is None
    expected = [
        "--host",
        "example",
        "service",
        "update",
        "--force",
        "--image",
        "image",
        "--with-registry-auth",
        "--detach",
        "--quiet",
        "--replicas",
        "0",
        "identity",
    ]
    assert calls(log) == [expected, expected]


def test_all_options_and_handle_forwarding(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    handle = Service(client.client_config, "identity", is_immutable_id=True)
    options: PolicyArguments = {
        "update_parallelism": 0,
        "update_delay": timedelta(microseconds=1),
        "update_failure_action": FailureAction.ROLLBACK,
        "update_monitor": 2,
        "update_max_failure_ratio": 0.25,
        "update_order": UpdateOrder.START_FIRST,
        "rollback_parallelism": 3,
        "rollback_delay": 4,
        "rollback_failure_action": FailureAction.PAUSE,
        "rollback_monitor": timedelta(microseconds=5),
        "rollback_max_failure_ratio": 0.0,
        "rollback_order": UpdateOrder.STOP_FIRST,
    }
    assert client.service.update(handle, **options) is None
    assert handle.update(**options) is None
    expected = [
        "--host",
        "example",
        "service",
        "update",
        "--update-parallelism",
        "0",
        "--update-delay",
        "1000ns",
        "--update-failure-action",
        "rollback",
        "--update-monitor",
        "2000000000ns",
        "--update-max-failure-ratio",
        "0.25",
        "--update-order",
        "start-first",
        "--rollback-parallelism",
        "3",
        "--rollback-delay",
        "4000000000ns",
        "--rollback-failure-action",
        "pause",
        "--rollback-monitor",
        "5000ns",
        "--rollback-max-failure-ratio",
        "0.0",
        "--rollback-order",
        "stop-first",
        "identity",
    ]
    assert calls(log) == [expected, expected]


@pytest.mark.parametrize("prefix", ["update", "rollback"])
@pytest.mark.parametrize(
    "name", ["parallelism", "delay", "monitor", "max_failure_ratio"]
)
def test_zero_is_forwarded(
    fake_cli: tuple[DockerClient, Path], prefix: str, name: str
) -> None:
    client, log = fake_cli
    kwargs = json.loads(json.dumps({f"{prefix}_{name}": 0}))
    client.service.update("identity", **kwargs)
    value = "0ns" if name in ("delay", "monitor") else "0"
    assert calls(log)[0][-3:] == [
        f"--{prefix}-{name.replace('_', '-')}",
        value,
        "identity",
    ]


@pytest.mark.parametrize("prefix", ["update", "rollback"])
@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("parallelism", -1, ValueError),
        ("parallelism", 2**64, ValueError),
        ("parallelism", True, TypeError),
        ("parallelism", 1.5, TypeError),
        ("delay", -1, ValueError),
        ("delay", 2**63, ValueError),
        ("delay", True, TypeError),
        ("delay", 0.5, TypeError),
        ("monitor", -1, ValueError),
        ("monitor", True, TypeError),
        ("max_failure_ratio", -0.1, ValueError),
        ("max_failure_ratio", 1.1, ValueError),
        ("max_failure_ratio", float("nan"), ValueError),
        ("max_failure_ratio", float("inf"), ValueError),
        ("max_failure_ratio", 10**400, ValueError),
        ("max_failure_ratio", True, TypeError),
        ("max_failure_ratio", "x", TypeError),
        ("failure_action", "bad", ValueError),
        ("failure_action", 1, TypeError),
        ("order", "bad", ValueError),
        ("order", 1, TypeError),
    ],
)
def test_invalid_options_precede_lookup(
    prefix: str, name: str, value: object, error: type[Exception]
) -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    kwargs = json.loads(json.dumps({f"{prefix}_{name}": value}))
    with pytest.raises(error):
        client.service.update("secret", **kwargs)


@pytest.mark.parametrize("prefix", ["update", "rollback"])
def test_duration_and_integer_bounds(
    fake_cli: tuple[DockerClient, Path], prefix: str
) -> None:
    client, log = fake_cli
    largest_microseconds = (2**63 - 1) // 1000
    if prefix == "update":
        client.service.update(
            "identity",
            update_parallelism=2**64 - 1,
            update_delay=timedelta(microseconds=largest_microseconds),
            update_monitor=(2**63 - 1) // 10**9,
        )
    else:
        client.service.update(
            "identity",
            rollback_parallelism=2**64 - 1,
            rollback_delay=timedelta(microseconds=largest_microseconds),
            rollback_monitor=(2**63 - 1) // 10**9,
        )
    assert calls(log)[0][-7:] == [
        f"--{prefix}-parallelism",
        str(2**64 - 1),
        f"--{prefix}-delay",
        f"{largest_microseconds * 1000}ns",
        f"--{prefix}-monitor",
        "9223372036000000000ns",
        "identity",
    ]
    with pytest.raises(ValueError):
        if prefix == "update":
            client.service.update(
                "identity",
                update_delay=timedelta(microseconds=largest_microseconds + 1),
            )
        else:
            client.service.update(
                "identity",
                rollback_delay=timedelta(microseconds=largest_microseconds + 1),
            )


def test_rollback_rejects_update_only_action_before_lookup() -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    kwargs = json.loads('{"rollback_failure_action":"rollback"}')
    with pytest.raises(ValueError):
        client.service.update("identity", **kwargs)


@pytest.mark.parametrize("prefix", ["update", "rollback"])
@pytest.mark.parametrize(
    "value",
    [timedelta(microseconds=-1), timedelta(microseconds=(2**63 - 1) // 1000 + 1)],
)
def test_invalid_timedelta_precedes_lookup(prefix: str, value: timedelta) -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    with pytest.raises(ValueError):
        if prefix == "update":
            client.service.update("identity", update_monitor=value)
        else:
            client.service.update("identity", rollback_monitor=value)


def test_unspecified_options_are_omitted(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    client.service.update("identity")
    assert calls(log) == [["--host", "example", "service", "update", "identity"]]


@pytest.mark.parametrize("detach", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("handle_target", [False, True])
def test_rollback_single_launch(
    fake_cli: tuple[DockerClient, Path], detach: bool, quiet: bool, handle_target: bool
) -> None:
    client, log = fake_cli
    target = (
        Service(client.client_config, "identity", is_immutable_id=True)
        if handle_target
        else "identity"
    )
    assert client.service.rollback(target, detach=detach, quiet=quiet) is None
    assert calls(log) == [
        [
            "--host",
            "example",
            "service",
            "rollback",
            *(["--detach"] if detach else []),
            *(["--quiet"] if quiet else []),
            "identity",
        ]
    ]


@pytest.mark.parametrize(
    "target,error", [("missing", NoSuchService), ("secret-failure", DockerException)]
)
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("debug", ["0", "1"])
def test_rollback_failure_is_once_and_diagnostics_follow_config(
    fake_cli: tuple[DockerClient, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target: str,
    error: type[DockerException],
    raw: bool,
    debug: str,
) -> None:
    client, log = fake_cli
    client.client_config.raw_diagnostics = raw
    monkeypatch.setenv("PYDOCKER_DEBUG", debug)
    with pytest.raises(error) as failure:
        client.service.rollback(target)
    assert failure.value.return_code == 17
    assert (target in str(failure.value)) is raw
    assert (target in capsys.readouterr().out) is (raw and debug == "1")
    assert calls(log) == [["--host", "example", "service", "rollback", target]]
