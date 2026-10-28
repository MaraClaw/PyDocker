import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError, asdict, dataclass, field, fields, replace
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any

import pytest

from pywhales import DockerClient
from pywhales._observation import ObservationState
from pywhales.convergence import (
    Cancelled,
    Deadline,
    DeadlineDetail,
    Failure,
    ServiceTarget,
    StackTarget,
    Success,
    capture_service,
    wait,
)
from pywhales.convergence._service import ServiceObservation
from pywhales.inventory import InspectionPolicy


@pytest.fixture
def policy() -> InspectionPolicy:
    return InspectionPolicy(
        max_references=1, max_argv_bytes=65536, max_response_bytes=65536
    )


@pytest.fixture
def fake_cli(tmp_path: Path) -> DockerClient:
    script = tmp_path / "docker.py"
    script.write_text("""import json, pathlib, sys, threading
root = pathlib.Path(__file__).parent
args = sys.argv[1:]
with (root / "calls").open("a") as log:
    log.write(json.dumps(args) + "\\n")
data = json.loads((root / "data.json").read_text())
calls = [json.loads(line) for line in (root / "calls").read_text().splitlines()]
hang = data.get("hang_at")
if hang and args == hang["args"] and calls.count(args) == hang["occurrence"]:
    (root / "started").write_text(str(__import__("os").getpid()))
    threading.Event().wait()
tasks = data["tasks"]
for identity, sequence in data.get("task_sequences", {}).items():
    count = sum(call[:2] == ["service", "ps"] and call[-1] == identity for call in calls)
    tasks = [item for item in tasks if item["ServiceID"] != identity] + sequence[min(max(count - 1, 0), len(sequence) - 1)]
if args[:2] == ["service", "inspect"]:
    sequence = data.get("sequences", {}).get(args[2])
    calls = [json.loads(line) for line in (root / "calls").read_text().splitlines()]
    count = sum(call[:2] == ["service", "inspect"] and call[2] == args[2] for call in calls)
    print(json.dumps([sequence[min(count - 1, len(sequence) - 1)] if sequence else data["services"][args[2]]]))
elif args[:2] == ["service", "ps"]:
    if (root / "hang").exists():
        (root / "started").touch()
        threading.Event().wait()
    print("\\n".join(task["ID"] for task in tasks if task["ServiceID"] == args[-1]))
elif args[:3] == ["inspect", "--type", "task"]:
    print(json.dumps([task for task in tasks if task["ID"] == args[3]]))
elif args[:2] in (["stack", "deploy"], ["service", "update"]):
    pass
else:
    sys.exit(2)
""")
    return DockerClient(client_call=[sys.executable, str(script)])


def service(identity: str = "service-a", replicas: int = 1) -> dict[str, Any]:
    return {
        "ID": identity,
        "Version": {"Index": 5},
        "Spec": {
            "Mode": {"Replicated": {"Replicas": replicas}},
            "TaskTemplate": {
                "ContainerSpec": {
                    "Image": "example:1",
                    "Mounts": [{"Target": "/data", "Future": {"A": 2}}],
                },
                "ForceUpdate": 3,
            },
        },
    }


def task(
    identity: str = "task-a", service_id: str = "service-a", slot: int = 1
) -> dict[str, Any]:
    return {
        "ID": identity,
        "ServiceID": service_id,
        "Slot": slot,
        "DesiredState": "running",
        "Status": {"State": "running"},
        "Spec": {**service()["Spec"]["TaskTemplate"], "Runtime": "container"},
    }


def store(
    path: Path, services: list[dict[str, Any]], tasks: list[dict[str, Any]]
) -> None:
    (path / "data.json").write_text(
        json.dumps(
            {"services": {item["ID"]: item for item in services}, "tasks": tasks}
        )
    )


def capture(
    client: DockerClient, policy: InspectionPolicy, identity: str = "service-a"
) -> ServiceTarget:
    result = capture_service(client, identity, policy=policy, timeout=2)
    assert isinstance(result, ServiceTarget)
    return result


def test_matching_template_converges_via_fake_cli(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    store(tmp_path, [service()], [task()])
    target = capture(fake_cli, policy)
    assert isinstance(
        wait(target, policy=policy, timeout=2, poll_interval=0.01), Success
    )
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls == [
        ["service", "inspect", "service-a"],
        ["service", "inspect", "service-a"],
        ["service", "ps", "--quiet", "--no-trunc", "service-a"],
        ["inspect", "--type", "task", "task-a"],
        ["service", "inspect", "service-a"],
    ]


@pytest.mark.parametrize("mutation", ["stale", "missing", "duplicate", "history"])
def test_insufficient_current_task_evidence_never_succeeds(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path, mutation: str
) -> None:
    current = task()
    tasks = [current]
    if mutation == "stale":
        current["Spec"]["ContainerSpec"]["Mounts"][0]["Future"]["A"] = 9
    elif mutation == "missing":
        del current["Spec"]
    elif mutation == "duplicate":
        tasks.append(task("task-b"))
    else:
        current["DesiredState"] = "shutdown"
    store(tmp_path, [service()], tasks)
    result = wait(
        capture(fake_cli, policy), policy=policy, timeout=0.2, poll_interval=1
    )
    assert isinstance(result, Deadline)


@pytest.mark.parametrize(
    "state,expected",
    [
        ("paused", "update_failed"),
        ("rollback_started", "update_failed"),
        ("rollback_paused", "update_failed"),
        ("rollback_completed", "update_failed"),
        ("failed", "update_failed"),
        ("mystery", "malformed"),
    ],
)
def test_failed_update_states_are_terminal(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    state: str,
    expected: str,
) -> None:
    snapshot = service()
    snapshot["UpdateStatus"] = {"State": state}
    store(tmp_path, [snapshot], [task()])
    result = wait(
        capture(fake_cli, policy), policy=policy, timeout=2, poll_interval=0.01
    )
    assert (
        isinstance(result, Failure)
        and result.reason == expected
        and result.state == (None if state == "mystery" else state)
    )


@pytest.mark.parametrize("state", [None, "completed", "updating"])
def test_update_progress_state(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path, state: str | None
) -> None:
    snapshot = service()
    if state is not None:
        snapshot["UpdateStatus"] = {"State": state}
    store(tmp_path, [snapshot], [task()])
    result = wait(
        capture(fake_cli, policy),
        policy=policy,
        timeout=0.4 if state == "updating" else 3,
        poll_interval=1,
    )
    assert isinstance(result, Deadline if state == "updating" else Success)


@pytest.mark.parametrize(
    "mode",
    [
        {"Global": {}},
        {"ReplicatedJob": {"MaxConcurrent": 1}},
        {"GlobalJob": {}},
        {},
        {"Replicated": {}},
        {"Replicated": {"Replicas": -1}},
    ],
)
def test_unsupported_modes_fail_closed(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    mode: dict[str, Any],
) -> None:
    snapshot = service()
    snapshot["Spec"]["Mode"] = mode
    store(tmp_path, [snapshot], [])
    result = capture_service(fake_cli, "service-a", policy=policy, timeout=2)
    assert isinstance(result, Failure) and result.reason == "unsupported"


def test_zero_and_fixed_stack_membership(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    store(
        tmp_path,
        [service(replicas=0), service("service-b")],
        [task(service_id="service-b")],
    )
    target = StackTarget(
        services=(capture(fake_cli, policy), capture(fake_cli, policy, "service-b"))
    )
    assert isinstance(
        wait(target, policy=policy, timeout=2, poll_interval=0.01), Success
    )


def test_version_change_is_superseded(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    snapshot = service()
    store(tmp_path, [snapshot], [task()])
    target = capture(fake_cli, policy)
    snapshot["Version"]["Index"] = 6
    store(tmp_path, [snapshot], [task()])
    result = wait(target, policy=policy, timeout=2, poll_interval=0.01)
    assert isinstance(result, Failure) and result.reason == "superseded"


def test_cancellation_reaps_hung_fake_cli(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store(tmp_path, [service()], [task()])
    target = capture(fake_cli, policy)
    (tmp_path / "hang").touch()
    cancel = Event()
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen
    launched = Event()

    def track(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        process = popen(args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds)
        processes.append(process)
        if "ps" in args:
            launched.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", track)
    results = []
    worker = Thread(
        target=lambda: results.append(
            wait(target, policy=policy, timeout=5, poll_interval=1, cancel=cancel)
        )
    )
    worker.start()
    assert launched.wait(2)
    end = time.monotonic() + 2
    while not (tmp_path / "started").exists() and time.monotonic() < end:
        Event().wait(0.01)
    assert (tmp_path / "started").exists()
    cancel.set()
    worker.join(3)
    assert not worker.is_alive()
    assert len(results) == 1 and isinstance(results[0], Cancelled)
    assert processes[-1].returncode is not None
    assert processes[-1].stdout is not None and processes[-1].stdout.closed
    assert processes[-1].stderr is not None and processes[-1].stderr.closed
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls[-1][:2] == ["service", "ps"]


def test_deadline_reaps_hung_child(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store(tmp_path, [service()], [task()])
    target = capture(fake_cli, policy)
    (tmp_path / "hang").touch()
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def track(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        process = popen(args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", track)
    result = wait(target, policy=policy, timeout=2, poll_interval=1)
    assert isinstance(result, Deadline)
    assert result.detail is None
    assert (tmp_path / "started").exists()
    assert processes[-1].returncode is not None
    assert processes[-1].stdout is not None and processes[-1].stdout.closed
    assert processes[-1].stderr is not None and processes[-1].stderr.closed
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls[-1][:2] == ["service", "ps"]


def test_stack_waits_for_every_fixed_service(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    store(tmp_path, [service(), service("service-b")], [task()])
    target = StackTarget(
        services=(capture(fake_cli, policy), capture(fake_cli, policy, "service-b"))
    )
    assert isinstance(
        wait(target, policy=policy, timeout=0.3, poll_interval=1), Deadline
    )


def test_template_change_without_version_change_is_superseded(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    snapshot = service()
    store(tmp_path, [snapshot], [task()])
    target = capture(fake_cli, policy)
    snapshot["Spec"]["TaskTemplate"]["ContainerSpec"]["Mounts"][0]["Future"]["A"] = 8
    store(tmp_path, [snapshot], [task()])
    result = wait(target, policy=policy, timeout=2, poll_interval=0.01)
    assert isinstance(result, Failure) and result.reason == "superseded"


def test_rollout_sequence_reaches_completed(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    updating = service()
    updating["UpdateStatus"] = {"State": "updating"}
    completed = service()
    completed["UpdateStatus"] = {"State": "completed"}
    store(tmp_path, [completed], [task()])
    data = json.loads((tmp_path / "data.json").read_text())
    data["sequences"] = {"service-a": [completed, updating, completed]}
    (tmp_path / "data.json").write_text(json.dumps(data))
    result = wait(
        capture(fake_cli, policy), policy=policy, timeout=3, poll_interval=0.01
    )
    assert isinstance(result, Success)
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert sum(call[:2] == ["service", "ps"] for call in calls) == 2


def test_changed_mode_is_superseded(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    snapshot = service()
    store(tmp_path, [snapshot], [])
    target = capture(fake_cli, policy)
    snapshot["Spec"]["Mode"] = {"Global": {}}
    store(tmp_path, [snapshot], [])
    result = wait(target, policy=policy, timeout=2, poll_interval=0.01)
    assert isinstance(result, Failure) and result.reason == "superseded"


def test_mutations_do_not_invoke_observation_or_rollback(
    fake_cli: DockerClient, tmp_path: Path
) -> None:
    store(tmp_path, [], [])
    fake_cli.stack.deploy("sample", [])
    fake_cli.service.update("service-a", detach=True)
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls == [
        ["stack", "deploy", "--resolve-image", "always", "sample"],
        ["service", "update", "--detach", "service-a"],
    ]


def test_fake_clock_deadline_stops_polling(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pywhales._observation as observation_module
    import pywhales.convergence._wait as wait_module

    store(tmp_path, [service()], [])
    target = capture(fake_cli, policy)
    current = [100.0]
    polls = []

    def sleep(seconds: float) -> None:
        current[0] += seconds

    def observe(_: ServiceTarget) -> ServiceObservation:
        polls.append(current[0])
        return ServiceObservation(True, False, False, False, False, False)

    clock = SimpleNamespace(monotonic=lambda: current[0], sleep=sleep)
    monkeypatch.setattr(wait_module, "time", clock)
    monkeypatch.setattr(observation_module, "time", clock)
    monkeypatch.setattr(wait_module, "check_service", observe)
    result = wait(target, policy=policy, timeout=2, poll_interval=1)
    assert isinstance(result, Deadline)
    assert polls == [100.0, 101.0]


@pytest.mark.parametrize("counter", [item.name for item in fields(DeadlineDetail)])
@pytest.mark.parametrize("value", [-1, 2, True, 0.5])
def test_deadline_counts_reject_invalid_values(
    counter: str, value: int | float
) -> None:
    detail = DeadlineDetail(1)
    if counter == "captured_services" and type(value) is int and value == 2:
        assert replace(detail, captured_services=2).captured_services == 2
    else:
        with pytest.raises(ValueError):
            replace(detail, **{counter: value})


def test_deadline_detail_is_immutable_and_optional() -> None:
    detail = DeadlineDetail(2, missing_ready_slots=1, stale_templates=1)
    outcome = Deadline(detail)
    for attribute in fields(detail):
        with pytest.raises(FrozenInstanceError):
            setattr(detail, attribute.name, 2)
    for attribute in fields(outcome):
        with pytest.raises(FrozenInstanceError):
            setattr(outcome, attribute.name, None)
    assert Deadline().detail is None
    assert all(type(value) is int for value in asdict(detail).values())


@pytest.mark.parametrize("subclass", [False, True])
def test_deadline_rejects_payload_bearing_detail(subclass: bool) -> None:
    @dataclass(frozen=True, slots=True)
    class PayloadDetail(DeadlineDetail):
        wire: dict[str, str] = field(default_factory=dict)

    payload = {"secret": "credential-payload"}
    detail = PayloadDetail(1, wire=payload) if subclass else payload
    with pytest.raises(ValueError):
        replace(Deadline(), detail=detail)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("empty", DeadlineDetail(1, missing_ready_slots=1)),
        ("duplicate", DeadlineDetail(1, duplicate_slots=1)),
        ("stale", DeadlineDetail(1, missing_ready_slots=1, stale_templates=1)),
        ("nonrunning", DeadlineDetail(1, missing_ready_slots=1, non_running_tasks=1)),
        ("missing", DeadlineDetail(1, missing_ready_slots=1, invalid_task_shape=1)),
        ("slot", DeadlineDetail(1, missing_ready_slots=1, invalid_task_shape=1)),
        ("updating", DeadlineDetail(1, ongoing_updates=1)),
        ("history", DeadlineDetail(1, missing_ready_slots=1)),
        (
            "overlap",
            DeadlineDetail(
                1,
                missing_ready_slots=1,
                duplicate_slots=1,
                stale_templates=1,
                non_running_tasks=1,
                invalid_task_shape=1,
                ongoing_updates=1,
            ),
        ),
    ],
)
def test_each_deadline_reason_counts_services_not_tasks(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    mutation: str,
    expected: DeadlineDetail,
) -> None:
    snapshot, current = service(), task()
    tasks = [current]
    if mutation == "empty":
        tasks = []
    elif mutation == "duplicate":
        tasks.extend([task("task-b"), task("task-c")])
    elif mutation == "stale":
        current["Spec"]["ContainerSpec"]["Image"] = "credential-payload"
    elif mutation == "nonrunning":
        current["Status"]["State"] = "starting"
    elif mutation == "missing":
        del current["Spec"]
    elif mutation == "slot":
        current["Slot"] = 2
    elif mutation == "updating":
        snapshot["UpdateStatus"] = {"State": "updating"}
    elif mutation == "history":
        current["DesiredState"] = "shutdown"
        current["Slot"] = -1
        current["Status"]["State"] = "failed"
        current["Spec"]["ContainerSpec"]["Image"] = "credential-payload"
    elif mutation == "overlap":
        snapshot["UpdateStatus"] = {"State": "updating"}
        current["Slot"] = 2
        current["Status"]["State"] = "starting"
        current["Spec"]["ContainerSpec"]["Image"] = "credential-payload"
        tasks.append({**current, "ID": "task-b", "Spec": None})
    store(tmp_path, [snapshot], tasks)
    result = wait(capture(fake_cli, policy), policy=policy, timeout=2, poll_interval=10)
    assert isinstance(result, Deadline) and result.detail == expected
    assert "credential-payload" not in repr(result)
    assert "service-a" not in repr(result) and "task-a" not in repr(result)


def test_mixed_stack_summary_counts_only_affected_captured_services(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    updating = service("service-b")
    updating["UpdateStatus"] = {"State": "updating"}
    stale = task("task-b", "service-b")
    stale["Spec"]["ForceUpdate"] = 99
    history = task("history", "service-c", slot=-1)
    history["DesiredState"] = "shutdown"
    del history["Spec"]
    store(
        tmp_path,
        [service(), updating, service("service-c", replicas=0), service("service-d")],
        [task(), stale, history],
    )
    target = StackTarget(
        services=tuple(
            capture(fake_cli, policy, f"service-{suffix}") for suffix in "abcd"
        )
    )
    result = wait(target, policy=policy, timeout=3, poll_interval=10)
    assert isinstance(result, Deadline)
    assert result.detail == DeadlineDetail(
        4, missing_ready_slots=2, stale_templates=1, ongoing_updates=1
    )


@pytest.mark.parametrize(
    "stage",
    ["first", "later-member", "first-recheck", "later-recheck", "latest-member"],
)
def test_partial_cycles_never_replace_complete_detail_and_hung_children_are_reaped(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    store(tmp_path, [service(), service("service-b")], [task("task-b", "service-b")])
    target = StackTarget(
        services=(capture(fake_cli, policy), capture(fake_cli, policy, "service-b"))
    )
    data = json.loads((tmp_path / "data.json").read_text())
    later = stage.startswith(("later", "latest"))
    recheck = stage.endswith("recheck")
    data["task_sequences"] = {"service-a": [[], [task()]] if later else [[task()]]}
    command = (
        ["service", "inspect", "service-b"]
        if recheck
        else ["service", "ps", "--quiet", "--no-trunc", "service-b"]
    )
    occurrence = (4 if later else 3) if recheck else (2 if later else 1)
    expected = DeadlineDetail(2, missing_ready_slots=1) if later else None
    if stage == "latest-member":
        stale = task()
        stale["Spec"]["ForceUpdate"] = 99
        data["task_sequences"] = {"service-a": [[], [stale], []]}
        occurrence = 3
        expected = DeadlineDetail(2, missing_ready_slots=1, stale_templates=1)
    data["hang_at"] = {"args": command, "occurrence": occurrence}
    (tmp_path / "data.json").write_text(json.dumps(data))
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def track(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        process = popen(args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", track)
    result = wait(target, policy=policy, timeout=3, poll_interval=0.01)
    assert isinstance(result, Deadline)
    assert result.detail == expected
    assert (tmp_path / "started").exists()
    assert all(process.returncode is not None for process in processes)
    assert all(
        process.stdout is not None and process.stdout.closed for process in processes
    )
    assert all(
        process.stderr is not None and process.stderr.closed for process in processes
    )
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls[-1] == command and calls.count(command) == occurrence


@pytest.mark.parametrize("state", ["updating", "paused", "mystery", "superseded"])
def test_final_generation_recheck_precedes_publication_and_success(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    state: str,
) -> None:
    snapshot, changed = service(), service()
    if state == "superseded":
        changed["Version"]["Index"] += 1
    else:
        changed["UpdateStatus"] = {"State": state}
    store(tmp_path, [snapshot], [task()])
    target = capture(fake_cli, policy)
    data = json.loads((tmp_path / "data.json").read_text())
    data["sequences"] = {"service-a": [snapshot, snapshot, changed]}
    (tmp_path / "data.json").write_text(json.dumps(data))
    result = wait(target, policy=policy, timeout=2, poll_interval=10)
    if state == "updating":
        assert isinstance(result, Deadline) and result.detail == DeadlineDetail(
            1, ongoing_updates=1
        )
    else:
        assert isinstance(result, Failure)
        assert (
            result.reason
            == {
                "paused": "update_failed",
                "mystery": "malformed",
                "superseded": "superseded",
            }[state]
        )


def test_expiry_at_final_checkpoint_discards_candidate(
    fake_cli: DockerClient,
    policy: InspectionPolicy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pywhales.convergence._wait as wait_module
    from pywhales.exceptions import ObservationTimeout

    store(tmp_path, [service()], [])
    target = capture(fake_cli, policy)
    check = wait_module.check_observation
    checkpoints = 0

    def expire(state: ObservationState | None = None) -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 3:
            raise ObservationTimeout()
        check(state)

    monkeypatch.setattr(wait_module, "check_observation", expire)
    result = wait(target, policy=policy, timeout=2, poll_interval=10)
    assert isinstance(result, Deadline) and result.detail is None
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls[-1][:2] == ["service", "ps"]


def test_final_stack_rechecks_continue_after_an_ongoing_update(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    first, second = service(), service("service-b")
    store(tmp_path, [first, second], [task(), task("task-b", "service-b")])
    target = StackTarget(
        services=(capture(fake_cli, policy), capture(fake_cli, policy, "service-b"))
    )
    updating = {**first, "UpdateStatus": {"State": "updating"}}
    superseded = {**second, "Version": {"Index": 8}}
    data = json.loads((tmp_path / "data.json").read_text())
    data["sequences"] = {
        "service-a": [first, first, updating],
        "service-b": [second, second, superseded],
    }
    (tmp_path / "data.json").write_text(json.dumps(data))
    result = wait(target, policy=policy, timeout=3, poll_interval=10)
    assert isinstance(result, Failure) and result.reason == "superseded"
    assert result.service_id == "service-b"


@pytest.mark.parametrize("limit", ["max_argv_bytes", "max_response_bytes"])
def test_observation_limits_are_failures_not_deadline_detail(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path, limit: str
) -> None:
    from pywhales.exceptions import ObservationLimitError

    store(tmp_path, [service()], [task()])
    target = capture(fake_cli, policy)
    bounded = replace(policy, **{limit: 1})
    result = wait(target, policy=bounded, timeout=2, poll_interval=10)
    assert isinstance(result, Failure) and result.reason == "observation"
    assert isinstance(result.cause, ObservationLimitError)


def test_capture_deadline_has_no_complete_cycle_detail(
    fake_cli: DockerClient, policy: InspectionPolicy, tmp_path: Path
) -> None:
    store(tmp_path, [service()], [])
    data = json.loads((tmp_path / "data.json").read_text())
    data["hang_at"] = {"args": ["service", "inspect", "service-a"], "occurrence": 1}
    (tmp_path / "data.json").write_text(json.dumps(data))
    result = capture_service(fake_cli, "service-a", policy=policy, timeout=2)
    assert isinstance(result, Deadline) and result.detail is None
    assert (tmp_path / "started").exists()
