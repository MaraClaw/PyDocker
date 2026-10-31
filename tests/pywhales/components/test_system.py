"""System model and event-stream regressions with destructive live prune cases.

Live cases require a disposable daemon because system pruning affects shared
daemon state. Event lifecycle cases use real Python children behind a patched
Docker transport rather than a daemon.
"""

import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ParsingError
from pywhales.components.system.cli_wrapper import DiskFreeResult
from pywhales.components.system.models import DockerEvent, SystemInfo
from pywhales.exceptions import DockerException, NoSuchContainer
from pywhales.test_utils import get_all_jsons, random_name
from pywhales.utils import stream_stdout_and_stderr


@pytest.mark.parametrize("with_percent", [False, True])
def test_disk_free_parses_all_resource_groups(with_percent: bool) -> None:
    records = [
        {
            "Type": "Images",
            "TotalCount": "4",
            "Active": "2",
            "Size": "1.5GB",
            "Reclaimable": "500MB",
        },
        {
            "Type": "Containers",
            "TotalCount": "3",
            "Active": "1",
            "Size": "20MB",
            "Reclaimable": "10MB",
        },
        {
            "Type": "Local Volumes",
            "TotalCount": "2",
            "Active": "0",
            "Size": "8MB",
            "Reclaimable": "4MB",
        },
        {
            "Type": "Build Cache",
            "TotalCount": "5",
            "Active": "1",
            "Size": "2GB",
            "Reclaimable": "1GB",
        },
    ]
    if with_percent:
        for record in records:
            record["Reclaimable"] += " (50%)"
    result = DiskFreeResult("\n".join(json.dumps(record) for record in records))

    for item, expected in zip(
        (result.images, result.containers, result.volumes, result.build_cache),
        (
            (4, 2, 1_500_000_000, 500_000_000),
            (3, 1, 20_000_000, 10_000_000),
            (2, 0, 8_000_000, 4_000_000),
            (5, 1, 2_000_000_000, 1_000_000_000),
        ),
        strict=True,
    ):
        assert (item.total_count, item.active, item.size, item.reclaimable) == expected
        assert item.reclaimable_percent == (50.0 if with_percent else 100.0)
        assert isinstance(item.size, int)
        assert isinstance(item.total_count, int)


def test_disk_free_executes_json_format_and_returns_summary() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["fake-docker", "prefix"]
    output = "\n".join(
        json.dumps(
            {
                "Type": kind,
                "TotalCount": "1",
                "Active": "0",
                "Size": "2MB",
                "Reclaimable": "1MB (50%)",
            }
        )
        for kind in ("Images", "Containers", "Local Volumes", "Build Cache")
    )
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, output.encode(), b""),
    ) as transport:
        result = client.system.disk_free()

    assert transport.call_args.args[0] == [
        "fake-docker",
        "prefix",
        "system",
        "df",
        "--format",
        "{{json .}}",
    ]
    assert result.volumes.size == 2_000_000
    assert result.build_cache.reclaimable_percent == 50.0


def test_info_executes_json_format_and_parses_schema() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["fake-docker", "prefix"]
    output = b'{"ID":"node-id","Containers":3,"Plugins":{"Volume":["local"]},"NCPU":4}'
    with patch(
        "subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, b"")
    ) as transport:
        result = client.system.info()

    assert transport.call_args.args[0] == [
        "fake-docker",
        "prefix",
        "system",
        "info",
        "--format",
        "{{json .}}",
    ]
    assert isinstance(result, SystemInfo)
    assert (result.id, result.containers, result.n_cpu) == ("node-id", 3, 4)
    assert result.plugins is not None
    assert result.plugins.volume == ["local"]
    assert result.images is None


@pytest.mark.parametrize(
    "output",
    [b"not-json", b'{"Containers":"not-a-count"}'],
)
def test_info_rejects_malformed_output(output: bytes) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["fake-docker"]
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, b""),
        ),
        pytest.raises(ParsingError),
    ):
        client.system.info()


def test_disk_free(ctr_client: DockerClient) -> None:
    ctr_client.pull("busybox")
    ctr_client.pull("busybox:1")
    docker_items_summary = ctr_client.system.disk_free()
    images = docker_items_summary.images
    assert images.total_count is not None
    assert images.total_count >= 1
    assert images.size is not None
    assert images.size > 2000


def test_info(ctr_client: DockerClient) -> None:
    info = ctr_client.system.info()
    assert info.plugins is not None
    assert info.plugins.volume is not None
    assert "local" in info.plugins.volume


def test_events(docker_client: DockerClient) -> None:
    name = random_name()
    docker_client.run("hello-world", remove=True, name=name)
    # Takes some time for events to register
    sleep(1)
    timestamp_1 = datetime.now()
    # Second run to generate more events
    docker_client.run("hello-world", remove=True, name=name)
    # Takes some time for events to register
    sleep(1)
    timestamp_2 = datetime.now()
    events = list(
        docker_client.system.events(until=timestamp_2, filters={"container": name})
    )
    # Check that we capture all the events from container create to destroy
    assert len(events) == 10
    actions = set()
    for event in events:
        actions.add(event.action)
    assert actions == {"create", "attach", "start", "die", "destroy"}
    events = list(
        docker_client.system.events(
            since=timestamp_1, until=timestamp_2, filters={"container": name}
        )
    )
    # Check that we only capture the events from the second docker run command
    assert len(events) == 5


def test_events_with_repeated_filter_pairs(docker_client: DockerClient) -> None:
    name = random_name()
    docker_client.run("hello-world", remove=True, name=name)
    # Takes some time for events to register
    sleep(1)
    timestamp = datetime.now()
    # Repeated event filters are alternatives, so start and die both match.
    pairs = [("container", name), ("event", "start"), ("event", "die")]
    events = list(docker_client.system.events(until=timestamp, filters=pairs))
    assert {event.action for event in events} == {"start", "die"}
    generated = list(
        docker_client.system.events(until=timestamp, filters=(pair for pair in pairs))
    )
    assert [event.action for event in generated] == [event.action for event in events]
    only_start = docker_client.system.events(
        until=timestamp, filters=[("container", name), ("event", "start")]
    )
    assert {event.action for event in only_start} == {"start"}


def test_events_no_arguments(docker_client: DockerClient) -> None:
    # The removal of the container will happen while we are waiting for an event
    container_name = random_name()
    docker_client.run(
        "busybox",
        ["sh", "-c", "sleep 3 && exit 1"],
        name=container_name,
        remove=True,
        detach=True,
    )
    for event in docker_client.system.events():
        assert isinstance(event, DockerEvent)
        break
    time.sleep(3)
    assert not docker_client.container.exists(container_name)


@pytest.mark.parametrize("json_file", get_all_jsons("system_info"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    SystemInfo(**json.loads(json_as_txt))
    # we could do more checks here if needed


def test_parsing_events() -> None:
    json_file = Path(__file__).parent / "jsons/events/0.json"
    events = json.loads(json_file.read_text())["events"]
    for event in events:
        parsed: DockerEvent = DockerEvent(**event)
        assert parsed.time is not None
        assert parsed.time.date() == date(2020, 12, 28)


def test_prune_prunes_image(ctr_client: DockerClient) -> None:
    # TODO: Test dangling image
    for container in ctr_client.container.list(filters=[("ancestor", "busybox")]):
        ctr_client.container.remove(container, force=True)
    image = ctr_client.pull("busybox")
    assert image in ctr_client.image.list()

    # image not pruned because not dangling
    ctr_client.system.prune()
    assert image in ctr_client.image.list()

    # image not pruned because it does not have dne label
    ctr_client.system.prune(all=True, filters={"label": "dne"})
    assert image in ctr_client.image.list()

    # image not pruned because it is not 1000000 hours old
    ctr_client.system.prune(all=True, filters={"until": "1000000h"})
    assert image in ctr_client.image.list()

    # image not pruned because it does not have dne label and is not 1000000 hours old
    ctr_client.system.prune(all=True, filters={"label": "dne", "until": "1000000h"})
    assert image in ctr_client.image.list()

    # image pruned
    ctr_client.system.prune(all=True)
    assert image not in ctr_client.image.list()


def test_prune_prunes_container(ctr_client: DockerClient) -> None:
    stopped_container = ctr_client.run("hello-world", remove=False, detach=True)
    running_container = ctr_client.run(
        "ubuntu", ["sleep", "infinity"], remove=False, detach=True
    )

    assert stopped_container in ctr_client.container.list(all=True)
    assert running_container in ctr_client.container.list()

    ctr_client.system.prune()

    assert stopped_container not in ctr_client.container.list(all=True)
    assert running_container in ctr_client.container.list()
    ctr_client.container.remove(running_container, force=True)


def test_prune_prunes_network(ctr_client: DockerClient) -> None:
    network_name = random_name()
    my_net = ctr_client.network.create(network_name)
    assert my_net in ctr_client.network.list()
    ctr_client.system.prune()
    assert my_net not in ctr_client.network.list()


def test_prune_prunes_volumes(ctr_client: DockerClient) -> None:
    some_volume = ctr_client.volume.create(driver="local")
    ctr_client.run(
        "ubuntu",
        ["touch", "/dodo/dada"],
        volumes=[(some_volume, "/dodo")],
        remove=False,
    )
    assert some_volume in ctr_client.volume.list()

    ctr_client.system.prune()
    assert some_volume in ctr_client.volume.list()

    ctr_client.system.prune(volumes=True)
    assert some_volume not in ctr_client.volume.list()


def test_prune_raises_exception_on_invalid_arguments(
    docker_client: DockerClient,
) -> None:
    """Propagate Docker's rejection of an until filter combined with volumes."""
    with pytest.raises(DockerException):
        docker_client.system.prune(volumes=True, filters={"until": "1000000h"})


def test_prune_accepts_repeatable_filter_pairs(ctr_client: DockerClient) -> None:
    for container in ctr_client.container.list(filters=[("ancestor", "busybox")]):
        ctr_client.container.remove(container, force=True)
    image = ctr_client.pull("busybox")
    assert image in ctr_client.image.list()

    # image not pruned because it has neither of the two labels
    ctr_client.system.prune(all=True, filters=[("label", "dne"), ("label", "dne2")])
    assert image in ctr_client.image.list()

    # image not pruned because it is not 1000000 hours old
    ctr_client.system.prune(
        all=True, filters=[("until", "1000000h"), ("label!", "dne")]
    )
    assert image in ctr_client.image.list()

    # image pruned because it has neither of the two protection labels
    ctr_client.system.prune(
        all=True, filters=(pair for pair in [("label!", "dne"), ("label!", "dne2")])
    )
    assert image not in ctr_client.image.list()


def test_prune_pair_filters_raise_exception_on_invalid_arguments(
    docker_client: DockerClient,
) -> None:
    """Propagate Docker's rejection of an until filter combined with volumes."""
    with pytest.raises(DockerException):
        docker_client.system.prune(volumes=True, filters=[("until", "1000000h")])


@pytest.mark.parametrize("consume", [False, True])
def test_owned_system_events_cancel_idle_child(consume: bool) -> None:
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                'print(\'{"Type":"container","Action":"start"}\', flush=True); input()',
            ],
            pipe_stdin=True,
        ) as raw,
        patch(
            "pywhales.components.system.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ) as start,
    ):
        stream = DockerClient().system.events(
            since=timedelta(seconds=30),
            until=datetime(2026, 1, 2),
            filters={"container": "owned"},
        )
        with stream:
            if consume:
                event = next(stream)
                assert isinstance(event, DockerEvent)
                assert event.action == "start"
            stream.close()
            stream.close()

        assert list(stream) == []
        assert raw._process.poll() is not None
        assert all(not thread.is_alive() for thread in raw._threads)
        assert list(start.call_args.args[0])[-6:] == [
            "--since",
            "30.0s",
            "--until",
            "2026-01-02T00:00:00",
            "--filter",
            "container=owned",
        ]


@pytest.mark.parametrize("payload", ["not-json", '{"time":"invalid"}'])
def test_owned_system_events_release_child_on_parse_error(payload: str) -> None:
    with (
        stream_stdout_and_stderr(
            [sys.executable, "-c", f"print({payload!r}, flush=True); input()"],
            pipe_stdin=True,
        ) as raw,
        patch(
            "pywhales.components.system.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        stream = DockerClient().system.events()

        with pytest.raises(ParsingError):
            next(stream)

        assert raw._process.poll() is not None
        assert all(not thread.is_alive() for thread in raw._threads)
        assert list(stream) == []


def test_owned_system_events_preserve_body_error() -> None:
    with (
        stream_stdout_and_stderr(
            [sys.executable, "-c", "input()"], pipe_stdin=True
        ) as raw,
        patch(
            "pywhales.components.system.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        stream = DockerClient().system.events()
        error = RuntimeError("event consumer failed")

        with pytest.raises(RuntimeError) as raised, stream:
            raise error

        assert raised.value is error
        assert raw._process.poll() is not None


@pytest.mark.parametrize("exit_code", [0, 8])
def test_owned_system_events_preserve_eof_and_failure(exit_code: int) -> None:
    with (
        stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                'import sys; print(\'{"Action":"start"}\'); '
                "sys.stderr.write('No such container: missing\\n'); "
                f"sys.exit({exit_code})",
            ]
        ) as raw,
        patch(
            "pywhales.components.system.cli_wrapper.stream_stdout_and_stderr",
            return_value=raw,
        ),
    ):
        stream = DockerClient().system.events()
        output: list[DockerEvent] = []

        if exit_code:
            with pytest.raises(NoSuchContainer) as raised:
                output.extend(stream)
            assert raised.value.return_code == exit_code
            assert raised.value.stderr == "No such container: missing\n"
        else:
            output.extend(stream)

        assert [event.action for event in output] == ["start"]
        assert raw._process.poll() == exit_code
        assert all(not thread.is_alive() for thread in raw._threads)
