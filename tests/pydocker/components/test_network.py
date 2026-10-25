import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pydocker import DockerClient, Network
from pydocker.components.container.models import ContainerEndpointIPAMConfig
from pydocker.components.network.cli_wrapper import NetworkInspectResult
from pydocker.exceptions import DockerException, NoSuchNetwork
from pydocker.test_utils import get_all_jsons, random_name


def test_batch_inspect_network_aliases_and_typed_snapshot() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    identity = "a" * 64
    snapshot = {"Id": identity, "Name": "named", "Driver": "bridge"}
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess(
                [], 0, json.dumps([snapshot] * 3).encode(), b""
            ),
        ) as command,
        patch("pydocker.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        networks = client.network.inspect(["named", identity, identity[:12]])
        assert [network.id for network in networks] == [identity] * 3
        assert all(network.driver == "bridge" for network in networks)
        assert all(
            isinstance(network._inspect_result, NetworkInspectResult)
            for network in networks
        )
        assert command.call_count == 1


@pytest.mark.parametrize("missing", [False, True], ids=["removed", "already-missing"])
def test_network_cleanup_preserves_removal(missing: bool) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    network = Network(client.client_config, "resource", is_immutable_id=True)
    response = CompletedProcess(
        [], 1 if missing else 0, b"", b"network resource not found" if missing else b""
    )
    with patch("subprocess.run", return_value=response) as command, network:
        pass
    assert command.call_args.args[0] == ["docker-test", "network", "remove", "resource"]
    assert command.call_count == 1
    if missing:
        with (
            patch("subprocess.run", return_value=response),
            pytest.raises(NoSuchNetwork),
        ):
            network.remove()


@pytest.mark.parametrize("json_file", get_all_jsons("networks"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    NetworkInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


@pytest.mark.parametrize("ctr_client", ["docker", "podman"], indirect=True)
def test_network_create_remove(ctr_client: DockerClient) -> None:
    my_name = random_name()
    with ctr_client.network.create(my_name) as my_network:
        assert my_network.name == my_name


@pytest.mark.parametrize("ctr_client", ["docker", "podman"], indirect=True)
def test_network_create_with_labels(ctr_client: DockerClient) -> None:
    my_name = random_name()
    labels = {"hello": "world", "meme": "meme-label"}
    with ctr_client.network.create(my_name, labels=labels) as my_network:
        assert my_network.name == my_name
        assert my_network.labels is not None
        for key, value in labels.items():
            assert my_network.labels[key] == value


@pytest.mark.parametrize("ctr_client", ["docker", "podman"], indirect=True)
def test_context_manager(ctr_client: DockerClient) -> None:
    my_net: Network | None = None
    with (
        pytest.raises(DockerException),
        ctr_client.network.create(random_name()) as created_net,
    ):
        my_net = created_net
        ctr_client.run(
            "busybox",
            ["ping", "idonotexistatall.com"],
            networks=[created_net],
            remove=True,
        )
        # an exception will be raised because the container will fail
        # but the network will be removed anyway.

    assert my_net is not None
    assert my_net not in ctr_client.network.list()


@pytest.mark.parametrize(
    "ctr_client",
    ["docker", pytest.param("podman", marks=pytest.mark.xfail)],
    indirect=True,
)
def test_network_connect_disconnect(ctr_client: DockerClient) -> None:
    with (
        ctr_client.network.create(random_name()) as my_net,
        ctr_client.container.run(
            "busybox:1", ["sleep", "infinity"], detach=True
        ) as my_container,
    ):
        ctr_client.network.connect(my_net, my_container)
        ctr_client.network.disconnect(my_net, my_container)


@pytest.mark.parametrize("ctr_client", ["docker", "podman"], indirect=True)
def test_remove_nothing(ctr_client: DockerClient) -> None:
    all_neworks = set(ctr_client.network.list())
    ctr_client.network.remove([])
    assert all_neworks == set(ctr_client.network.list())


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_service_create(docker_client: DockerClient) -> None:
    with (
        docker_client.network.create(
            random_name(), attachable=True, driver="overlay"
        ) as my_net,
        docker_client.service.create(
            "busybox", ["sleep", "infinity"], network=my_net.name
        ),
    ):
        assert my_net.containers is not None
        assert len(my_net.containers) == 2  # 1 container + 1 endpoint


@pytest.mark.parametrize("ctr_client", ["docker", "podman"], indirect=True)
def test_network_exists(ctr_client: DockerClient) -> None:
    network_name = random_name()
    assert not ctr_client.network.exists(network_name)
    with ctr_client.network.create(network_name) as network:
        assert ctr_client.network.exists(network_name)
        assert ctr_client.network.exists(network)
        assert network.exists()

    # Outside with clause, network should be removed and no longer exist
    assert not ctr_client.network.exists(network)
    assert not network.exists()


@pytest.mark.parametrize("ctr_client", ["docker", "podman"], indirect=True)
def test_list_filters(ctr_client: DockerClient) -> None:
    name = random_name()
    with ctr_client.network.create(name) as network:
        networks_listed = ctr_client.network.list(filters=[("name", name)])
    assert networks_listed == [network]


def test_list_filters_old_signature(docker_client: DockerClient) -> None:
    """Check backward compatibility of the DockerClient.network.list() API."""
    name = random_name()
    expected_warning = (
        r"Passing filters as a mapping is deprecated, replace with an iterable "
        r"of tuples instead, as so:\n"
        r"filters=\[\(.*\)\]"
    )
    with (
        docker_client.network.create(name) as network,
        pytest.warns(DeprecationWarning, match=expected_warning),
    ):
        networks_listed = docker_client.network.list(filters={"name": name})
    assert networks_listed == [network]


def test_correct_ipam_config(docker_client: DockerClient) -> None:
    """Test that we can create a network with a specific IPAM configuration."""
    with (
        docker_client.network.create(random_name(), subnet="172.20.0.0/16") as net,
        docker_client.container.run(
            "busybox",
            ["sleep", "infinity"],
            detach=True,
            networks=[net],
            ip="172.20.0.5",
        ) as ctr,
    ):
        inspect = docker_client.container.inspect(ctr)
        assert inspect.network_settings is not None
        networks = inspect.network_settings.networks
        assert networks is not None
        assert net.name is not None
        assert networks[net.name].ip_address == "172.20.0.5"
        assert networks[net.name].ipam_config == ContainerEndpointIPAMConfig(
            ipv4_address="172.20.0.5", ipv6_address=None, link_local_ips=None
        )


def test_disable_dns(podman_client: DockerClient) -> None:
    with podman_client.network.create(random_name(), disable_dns=False) as net:
        assert net.dns_enabled is True
        assert net.name is not None
        inspect_result = podman_client.network.inspect(net.name)
        assert inspect_result.dns_enabled is True

    with podman_client.network.create(random_name(), disable_dns=True) as net:
        assert net.dns_enabled is False
        assert net.name is not None
        inspect_result = podman_client.network.inspect(net.name)
        assert inspect_result.dns_enabled is False
