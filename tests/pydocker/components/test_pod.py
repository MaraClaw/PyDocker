import datetime as dt
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pydocker import DockerClient, Pod
from pydocker.components.pod.models import PodInspectResult
from pydocker.components.top import PodTopResult, TopParseError
from pydocker.exceptions import NoSuchPod
from pydocker.test_utils import get_all_jsons, random_name


@patch("pydocker.components.pod.cli_wrapper.run")
@pytest.mark.parametrize(
    "descriptors",
    [["pid", "seccomp", "args"], ["pid,seccomp", "args"], "pid,seccomp,args"],
)
def test_top_preserves_descriptor_order_when_podman_fields_are_selected(
    run_mock: Mock, descriptors: str | Sequence[str]
) -> None:
    client = DockerClient(client_call=[sys.executable, "engine"], host="ssh://podman")
    output = "PID SECCOMP COMMAND\n1 filter python  worker.py --label two words"
    run_mock.return_value = output

    result = client.pod.top("workload", descriptors)

    assert isinstance(result, PodTopResult)
    assert (result.headings, result.rows, result.raw_output) == (
        ("PID", "SECCOMP", "COMMAND"),
        (("1", "filter", "python  worker.py --label two words"),),
        output,
    )
    run_mock.assert_called_once_with(
        [
            Path(sys.executable),
            "engine",
            "--host",
            "ssh://podman",
            "pod",
            "top",
            "workload",
            "pid",
            "seccomp",
            "args",
        ]
    )


@patch(
    "pydocker.components.pod.cli_wrapper.run",
    return_value="USER PID PPID %CPU ELAPSED TTY TIME COMMAND\nroot 1 0 0.000 2h5m38.737s ? 0s sleep 300",
)
def test_top_preserves_default_table_when_descriptors_are_omitted(
    run_mock: Mock,
) -> None:
    client = DockerClient(client_call=[sys.executable])

    result = client.pod.top("workload")

    assert result.headings == (
        "USER",
        "PID",
        "PPID",
        "%CPU",
        "ELAPSED",
        "TTY",
        "TIME",
        "COMMAND",
    )
    assert result.rows == (
        ("root", "1", "0", "0.000", "2h5m38.737s", "?", "0s", "sleep 300"),
    )


@patch("pydocker.components.pod.cli_wrapper.run")
@pytest.mark.parametrize(
    ("descriptors", "output"),
    [
        (["args", "pid"], "COMMAND PID\nsleep 300 1"),
        (["pid", "args", "%C"], "PID COMMAND %CPU\n1 sleep 300 0.000"),
        (["stime", "pid"], "STIME PID\n2019-12-09 10:50:36 +0100 CET 1"),
        (["pid", "args"], "PID COMMAND\n1"),
        ([], ""),
    ],
)
def test_top_rejects_ambiguous_pod_table_when_parsing_is_requested(
    run_mock: Mock, descriptors: list[str], output: str
) -> None:
    client = DockerClient(client_call=[sys.executable])
    run_mock.return_value = output

    with pytest.raises(TopParseError, match="raw=True"):
        client.pod.top("workload", descriptors)


@patch(
    "pydocker.components.pod.cli_wrapper.run", return_value="COMMAND PID\nsleep 300 1\n"
)
def test_top_exposes_raw_pod_table_when_args_is_nonfinal(run_mock: Mock) -> None:
    client = DockerClient(client_call=[sys.executable])

    result = client.pod.top("workload", "args,pid", raw=True)

    assert result == "COMMAND PID\nsleep 300 1\n"


@patch("pydocker.utils.subprocess.run")
@pytest.mark.parametrize("raw", [False, True])
def test_top_retains_missing_pod_exception_when_cli_fails(
    run_mock: Mock, raw: bool
) -> None:
    client = DockerClient(client_call=[sys.executable])
    run_mock.return_value = subprocess.CompletedProcess(
        [], 125, b"", b"Error: no such pod: absent"
    )

    with pytest.raises(NoSuchPod) as error:
        client.pod.top("absent", raw=raw)

    assert error.value.return_code == 125
    assert error.value.docker_command[-3:] == ["pod", "top", "absent"]


@patch("pydocker.utils.subprocess.run")
def test_top_uses_pod_object_id_when_reference_is_immutable(run_mock: Mock) -> None:
    client = DockerClient(client_call=[sys.executable])
    pod = Pod(client.client_config, "immutable-id", is_immutable_id=True)
    run_mock.return_value = subprocess.CompletedProcess(
        [], 0, b"PID COMMAND\n1 sleep 300\n", b""
    )

    result = client.pod.top(pod, ["pid", "args"])

    assert result.rows == (("1", "sleep 300"),)
    assert run_mock.call_args.args[0] == [
        sys.executable,
        "pod",
        "top",
        "immutable-id",
        "pid",
        "args",
    ]


@patch("pydocker.components.pod.cli_wrapper.run")
@pytest.mark.parametrize(
    ("descriptors", "output", "rows"),
    [
        (["pid"], "PID", ()),
        (["pid", "%a"], "PID COMMAND\n1 sleep  300", (("1", "sleep  300"),)),
        (
            ["pid", "stime"],
            "PID STIME\n1 2019-12-09 10:50:36 +0100 CET",
            (("1", "2019-12-09 10:50:36 +0100 CET"),),
        ),
    ],
)
def test_top_preserves_selected_cells_when_pod_fields_are_command_final(
    run_mock: Mock,
    descriptors: list[str],
    output: str,
    rows: tuple[tuple[str, ...], ...],
) -> None:
    client = DockerClient(client_call=[sys.executable])
    run_mock.return_value = output

    result = client.pod.top("workload", descriptors)

    assert result.rows == rows


@pytest.mark.parametrize("json_file", get_all_jsons("pods"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    PodInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


def test_create_simple(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name) as pod:
        assert pod.exists()
        assert pod.name == pod_name
        pod_state = pod.state
        assert pod_state is not None
        assert pod_state.lower() == "created"
        assert pod.created is not None
        assert (
            dt.datetime.now().astimezone() - dt.timedelta(minutes=1)
            < pod.created
            < dt.datetime.now().astimezone()
        )
    assert not pod.exists()


def test_start_simple(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name) as pod:
        pod.start()
        pod_state = pod.state
        assert pod_state is not None
        assert pod_state.lower() == "running"
        assert pod.created is not None
        assert (
            dt.datetime.now().astimezone() - dt.timedelta(minutes=1)
            < pod.created
            < dt.datetime.now().astimezone()
        )
    assert not pod.exists()


def test_stop_simple(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name) as pod:
        pod.start()
        pod_state = pod.state
        assert pod_state is not None
        assert pod_state.lower() == "running"
        pod.stop()
        pod_state = pod.state
        assert pod_state is not None
        assert pod_state.lower() == "exited"
        assert pod.created is not None
        assert (
            dt.datetime.now().astimezone() - dt.timedelta(minutes=1)
            < pod.created
            < dt.datetime.now().astimezone()
        )
    assert not pod.exists()


def test_inspect(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name) as pod:
        inspected_pod = podman_client.pod.inspect(pod_name)
        assert inspected_pod == pod
        assert inspected_pod.name == pod_name


def test_list_multiple(podman_client: DockerClient) -> None:
    pod1_name = random_name()
    pod2_name = random_name()
    with podman_client.pod.create(pod1_name), podman_client.pod.create(pod2_name):
        listed_pods = podman_client.pod.list()
        listed_pod_names = [p.name for p in listed_pods]
        assert pod1_name in listed_pod_names
        assert pod2_name in listed_pod_names


def test_list_filters(podman_client: DockerClient) -> None:
    pod1_name = random_name()
    pod2_name = random_name()
    select_label = random_name()
    with (
        podman_client.pod.create(pod1_name),
        podman_client.pod.create(pod2_name, labels={select_label: ""}),
    ):
        listed_pods = podman_client.pod.list(filters=[("label", select_label)])
        assert [p.name for p in listed_pods] == [pod2_name]


def test_list_filters_old_signature(podman_client: DockerClient) -> None:
    """Check backward compatibility of the DockerClient.pod.list() API."""
    pod_name = random_name()
    expected_warning = (
        r"Passing filters as a mapping is deprecated, replace with an iterable "
        r"of tuples instead, as so:\n"
        r"filters=\[\(.*\)\]"
    )
    with podman_client.pod.create(pod_name):
        with pytest.warns(DeprecationWarning, match=expected_warning):
            listed_pods = podman_client.pod.list(filters={"name": pod_name})
        assert [p.name for p in listed_pods] == [pod_name]


def test_does_not_exist(podman_client: DockerClient) -> None:
    assert podman_client.pod.exists("pod-that-does-not-exist") is False


def test_create_with_container(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, infra=True) as pod:
        ubuntu_container = podman_client.container.create("ubuntu", pod=pod)
        assert pod.num_containers == 2
        assert pod.containers is not None
        assert ubuntu_container.id in [ctr.id for ctr in pod.containers]
    assert not pod.exists()
    assert not ubuntu_container.exists()


def test_start_with_container(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, infra=True) as pod:
        ubuntu_container = podman_client.container.create(
            "ubuntu", ["sleep", "infinity"], pod=pod
        )
        assert ubuntu_container.state is not None
        assert ubuntu_container.state.running is False
        pod.start()
        ubuntu_container.reload()
        assert ubuntu_container.state.running is True
    assert not pod.exists()
    assert not ubuntu_container.exists()


def test_kill_with_container(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, infra=True) as pod:
        ubuntu_container = podman_client.container.create(
            "ubuntu", ["sleep", "infinity"], pod=pod
        )
        assert ubuntu_container.state is not None
        assert ubuntu_container.state.running is False
        pod.start()
        ubuntu_container.reload()
        assert ubuntu_container.state.running is True
        pod.kill()
        assert ubuntu_container.state.running is False
    assert not pod.exists()
    assert not ubuntu_container.exists()


def test_create_devices_arg(podman_client: DockerClient) -> None:
    # Should skip if podman version is below 4.0 (need to implement
    # DockerClient.version).
    pod_name = random_name()
    with podman_client.pod.create(pod_name, devices=["/dev/net/tun"]) as pod:
        output = podman_client.container.run(
            "ubuntu", ["find", "/dev/", "-name", "tun"], pod=pod
        )
        assert "/dev/net/tun" in output


def test_create_dns_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, dns=["1.2.3.4"]) as pod:
        output = podman_client.container.run(
            "ubuntu", ["cat", "/etc/resolv.conf"], pod=pod
        )
        assert "1.2.3.4" in output


def test_create_exit_policy_arg(podman_client: DockerClient) -> None:
    # Should skip if podman version is below 4.2 (need to implement
    # DockerClient.version).
    pod_name = random_name()
    with podman_client.pod.create(pod_name, exit_policy="stop") as pod:
        assert pod.state == "Created"
        podman_client.container.run("ubuntu", ["true"], pod=pod)
        assert pod.state == "Exited"


def test_create_hostname_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, hostname="myhost") as pod:
        output = podman_client.container.run(
            "ubuntu", ["cat", "/etc/hostname"], pod=pod
        )
        assert output == "myhost"


def test_create_infra_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, infra=False) as pod:
        podman_client.container.run(
            "ubuntu", ["sleep", "infinity"], pod=pod, detach=True, stop_timeout=0
        )
        assert pod.num_containers == 1


def test_create_labels_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, labels={"foo": "bar"}) as pod:
        assert pod.labels == {"foo": "bar"}


def test_create_no_hosts_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, no_hosts=True) as pod:
        output = podman_client.container.run("ubuntu", ["cat", "/etc/hosts"], pod=pod)
        assert output == ""


def test_create_pod_id_file_arg(podman_client: DockerClient, tmp_path: Path) -> None:
    pod_name = random_name()
    pod_id_file = tmp_path / "id"
    with podman_client.pod.create(pod_name, pod_id_file=pod_id_file) as pod:
        assert pod.id == pod_id_file.read_text().strip()


def test_create_replace_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    pod1 = podman_client.pod.create(pod_name)
    pod1_id = pod1.id
    with podman_client.pod.create(pod_name, replace=True) as pod2:
        assert not pod1.exists()
        assert pod1_id != pod2.id


def test_create_share_arg(podman_client: DockerClient) -> None:
    pod_name = random_name()
    with podman_client.pod.create(pod_name, share=[], infra=True) as pod:
        pod.start()  # start the infra container
        assert not bool(pod.shared_namespaces)
        output = podman_client.container.run(
            "ubuntu", ["readlink", "/proc/self"], pod=pod
        )
        assert output == "1"
    with podman_client.pod.create(pod_name, share=["pid", "ipc"]) as pod:
        pod.start()  # start the infra container (PID 1)
        shared_namespaces = pod.shared_namespaces
        assert shared_namespaces is not None
        assert set(shared_namespaces) == {"pid", "ipc"}
        output = podman_client.container.run(
            "ubuntu", ["readlink", "/proc/self"], pod=pod
        )
        assert re.fullmatch(r"\d+", output) and output != "1"


def test_create_shm_size_arg(podman_client: DockerClient) -> None:
    # Should skip if podman version is below 4.2 (need to implement
    # DockerClient.version).
    pod_name = random_name()
    with podman_client.pod.create(pod_name, shm_size=1_024_000) as pod:
        output = podman_client.container.run("ubuntu", ["findmnt", "/dev/shm"], pod=pod)
        assert "size=1000k" in output


@pytest.mark.parametrize(
    "method",
    ["inspect", "kill", "logs", "remove", "restart", "start", "stop"],
)
def test_functions_nosuchpod(method: str, podman_client: DockerClient) -> None:
    with pytest.raises(NoSuchPod):
        getattr(podman_client.pod, method)("pod-that-does-not-exist")
