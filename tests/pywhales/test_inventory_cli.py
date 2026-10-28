import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest

from pywhales import (
    DockerClient,
    InspectionPolicy,
    InventoryRequest,
    Network,
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
    capture_inventory,
    observation,
)
from pywhales.client_config import ClientConfig, ParsingError, bulk_reload
from pywhales.components.network.models import NetworkInspectResult


@pytest.fixture
def fake_cli(tmp_path: Path) -> ClientConfig:
    script = tmp_path / "docker.py"
    script.write_text(
        """import json
import pathlib
import sys
import threading

root = pathlib.Path(__file__).parent
log = root / 'launches.jsonl'
with log.open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if (root / 'oversize').exists():
    sys.stdout.write('X' * 40)
    sys.stdout.flush()
    sys.stderr.write('Y' * 40)
    sys.stderr.flush()
    threading.Event().wait()
if sys.argv[1:3] == ['network', 'ls']:
    print('alpha-id\\nbeta-id')
elif sys.argv[1:3] == ['network', 'inspect']:
    count = len(log.read_text().splitlines())
    changed = (root / 'conflict').exists() and count > 1
    result = []
    for reference in sys.argv[3:]:
        identity = 'alpha-id' if reference in ('alias', 'alpha-id') else reference
        name = 'alias' if identity == 'alpha-id' else 'beta'
        result.append({'Id': identity, 'Name': name, 'Labels': {'version': 'changed' if changed else 'fresh'}})
    print(json.dumps(result))
else:
    sys.exit(2)
"""
    )
    return ClientConfig(client_call=[sys.executable, str(script)])


@pytest.fixture
def policy() -> InspectionPolicy:
    return InspectionPolicy(
        max_references=1, max_argv_bytes=65536, max_response_bytes=65536
    )


def test_capture_preserves_order_and_detaches_each_occurrence(
    fake_cli: ClientConfig, policy: InspectionPolicy, tmp_path: Path
) -> None:
    handle = Network(fake_cli, "alpha-id", is_immutable_id=True)
    original = NetworkInspectResult.model_validate(
        {"Id": "alpha-id", "Name": "alias", "Labels": {"version": "old"}}
    )
    handle._set_inspect_result(original)
    request = InventoryRequest(
        kind="network", references=("alias", "beta-id"), client_config=fake_cli
    )

    snapshot = capture_inventory([request, handle], policy=policy)

    assert [record.id for record in snapshot.records] == [
        "alpha-id",
        "beta-id",
        "alpha-id",
    ]
    assert all(record.kind == "network" for record in snapshot.records)
    assert snapshot.started_at.tzinfo == UTC
    assert snapshot.started_at <= snapshot.finished_at
    first, _, repeated = snapshot.records
    assert isinstance(first.payload, NetworkInspectResult)
    assert isinstance(repeated.payload, NetworkInspectResult)
    assert original.labels is not None
    assert first.payload.labels is not None
    original.labels["version"] = "cache-mutated"
    assert first.payload.labels == {"version": "fresh"}
    first.payload.labels["version"] = "record-mutated"
    assert repeated.payload.labels == {"version": "fresh"}
    assert handle._inspect_result is original
    launches = [
        json.loads(line)
        for line in (tmp_path / "launches.jsonl").read_text().splitlines()
    ]
    assert launches == [
        ["network", "inspect", "alias"],
        ["network", "inspect", "beta-id"],
        ["network", "inspect", "alpha-id"],
    ]


@pytest.mark.parametrize("operation", ["capture", "bulk"])
def test_conflicting_later_chunk_preserves_original_caches(
    fake_cli: ClientConfig, policy: InspectionPolicy, tmp_path: Path, operation: str
) -> None:
    (tmp_path / "conflict").touch()
    handles = [Network(fake_cli, "alpha-id", is_immutable_id=True) for _ in range(2)]
    for handle in handles:
        handle._set_inspect_result(
            NetworkInspectResult.model_validate(
                {"Id": "alpha-id", "Name": "alias", "Labels": {"version": "old"}}
            )
        )
    before = [
        (handle._inspect_result, handle._last_refreshed_time) for handle in handles
    ]

    with pytest.raises(ParsingError):
        if operation == "capture":
            capture_inventory(handles, policy=policy)
        else:
            bulk_reload(handles, policy=policy)

    for handle, (model, timestamp) in zip(handles, before, strict=True):
        assert handle._inspect_result is model
        assert handle._last_refreshed_time == timestamp
    assert len((tmp_path / "launches.jsonl").read_text().splitlines()) == 2


def test_response_limit_reaps_real_child(
    fake_cli: ClientConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "oversize").touch()
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=65536, max_response_bytes=64
    )
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def track(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        child = real_popen(
            args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds
        )
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", track)
    request = InventoryRequest(
        kind="network", references=("private-reference",), client_config=fake_cli
    )
    with pytest.raises(ObservationLimitError) as error:
        capture_inventory([request], policy=policy)

    assert error.value.reason == "response"
    assert error.value.limit == 64
    assert error.value.measured is not None and error.value.measured > 64
    assert "private-reference" not in str(error.value)
    assert len(children) == 1
    assert children[0].poll() is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed
    assert len((tmp_path / "launches.jsonl").read_text().splitlines()) == 1


def test_empty_capture_and_bulk_do_not_resolve_or_launch(
    policy: InspectionPolicy,
) -> None:
    with (
        patch("shutil.which") as lookup,
        patch("subprocess.Popen") as launch,
        patch("subprocess.run") as run,
    ):
        snapshot = capture_inventory([], policy=policy)
        bulk_reload([], policy=policy)
    assert snapshot.records == ()
    lookup.assert_not_called()
    launch.assert_not_called()
    run.assert_not_called()


def test_equal_distinct_configs_launch_separate_groups(
    fake_cli: ClientConfig, tmp_path: Path
) -> None:
    policy = InspectionPolicy(
        max_references=2, max_argv_bytes=65536, max_response_bytes=65536
    )
    other = ClientConfig(client_call=list(fake_cli.client_call))
    assert other == fake_cli and other is not fake_cli
    requests = [
        InventoryRequest(kind="network", references=(reference,), client_config=config)
        for reference, config in (("alpha-id", fake_cli), ("beta-id", other))
    ]
    snapshot = capture_inventory(requests, policy=policy)
    assert [record.id for record in snapshot.records] == ["alpha-id", "beta-id"]
    launches = [
        json.loads(line)
        for line in (tmp_path / "launches.jsonl").read_text().splitlines()
    ]
    assert launches == [
        ["network", "inspect", "alpha-id"],
        ["network", "inspect", "beta-id"],
    ]


def test_observation_applies_to_list_and_inspect(
    fake_cli: ClientConfig, policy: InspectionPolicy, tmp_path: Path
) -> None:
    client = DockerClient(client_call=fake_cli.client_call)
    with observation(policy):
        networks = client.network.list()
        inspected = client.network.inspect([network.id for network in networks])
    assert [network.id for network in inspected] == ["alpha-id", "beta-id"]
    launches = [
        json.loads(line)
        for line in (tmp_path / "launches.jsonl").read_text().splitlines()
    ]
    assert launches == [
        ["network", "ls", "--no-trunc", "--quiet"],
        ["network", "inspect", "alpha-id", "beta-id"],
    ]


@pytest.mark.parametrize("cancelled", [True, False])
def test_observation_cancellation_and_deadline_fail_without_launch(
    policy: InspectionPolicy, tmp_path: Path, cancelled: bool
) -> None:
    client = DockerClient(client_call=[sys.executable, str(tmp_path / "missing.py")])
    cancel = Event()
    if cancelled:
        cancel.set()
    error_type = ObservationCancelled if cancelled else ObservationTimeout
    deadline = None if cancelled else monotonic() - 1
    with (
        patch("subprocess.Popen") as launch,
        pytest.raises(error_type) as error,
        observation(policy, deadline=deadline, cancel=cancel),
    ):
        client.network.list()
    assert str(tmp_path) not in str(error.value)
    launch.assert_not_called()
