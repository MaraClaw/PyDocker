import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any

import pytest

from pywhales import DockerClient
from pywhales.convergence import (
    Cancelled,
    Deadline,
    Failure,
    ServiceTarget,
    StackTarget,
    Success,
    capture_service,
    wait,
)
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
if args[:2] == ["service", "inspect"]:
    sequence = data.get("sequences", {}).get(args[2])
    calls = [json.loads(line) for line in (root / "calls").read_text().splitlines()]
    count = sum(call[:2] == ["service", "inspect"] and call[2] == args[2] for call in calls)
    print(json.dumps([sequence[min(count - 1, len(sequence) - 1)] if sequence else data["services"][args[2]]]))
elif args[:2] == ["service", "ps"]:
    if (root / "hang").exists():
        (root / "started").touch()
        threading.Event().wait()
    print("\\n".join(task["ID"] for task in data["tasks"] if task["ServiceID"] == args[-1]))
elif args[:3] == ["inspect", "--type", "task"]:
    print(json.dumps([task for task in data["tasks"] if task["ID"] == args[3]]))
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
    result = wait(target, policy=policy, timeout=0.5, poll_interval=1)
    assert isinstance(result, Deadline)
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

    def observe(_: ServiceTarget) -> tuple[bool, Failure | None]:
        polls.append(current[0])
        return False, None

    clock = SimpleNamespace(monotonic=lambda: current[0], sleep=sleep)
    monkeypatch.setattr(wait_module, "time", clock)
    monkeypatch.setattr(observation_module, "time", clock)
    monkeypatch.setattr(wait_module, "_check_service", observe)
    result = wait(target, policy=policy, timeout=2, poll_interval=1)
    assert isinstance(result, Deadline)
    assert polls == [100.0, 101.0]
