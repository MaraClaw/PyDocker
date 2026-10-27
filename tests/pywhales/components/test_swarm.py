from datetime import timedelta

import pytest

from pywhales import DockerClient
from pywhales.components.system.models import SwarmSpec
from pywhales.exceptions import NotASwarmManager


def get_cluster_spec(docker_client: DockerClient) -> SwarmSpec:
    swarm = docker_client.system.info().swarm
    assert swarm is not None
    assert swarm.cluster is not None
    assert swarm.cluster.spec is not None
    return swarm.cluster.spec


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_update_retention_limit(docker_client: DockerClient) -> None:
    docker_client.swarm.update(task_history_limit=4)
    orchestration = get_cluster_spec(docker_client).orchestration
    assert orchestration is not None
    assert orchestration.task_history_retention_limit == 4


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_change_certificate_expiry(docker_client: DockerClient) -> None:
    ca = docker_client.swarm.ca(certificate_expiry=timedelta(days=1), rotate=True)
    ca_config = get_cluster_spec(docker_client).ca_config
    assert ca_config is not None
    assert ca_config.node_cert_expiry is not None
    node_cert_expiry = timedelta(microseconds=ca_config.node_cert_expiry / 1000)
    assert node_cert_expiry == timedelta(days=1)
    assert ca is not None
    assert "BEGIN CERTIFICATE" in ca


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_update_auto_lock_managers(docker_client: DockerClient) -> None:
    encryption_config = get_cluster_spec(docker_client).encryption_config
    assert encryption_config is not None
    assert encryption_config.auto_lock_managers is not True
    docker_client.swarm.update(autolock=True)
    encryption_config = get_cluster_spec(docker_client).encryption_config
    assert encryption_config is not None
    assert encryption_config.auto_lock_managers is True


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_unlock_key(docker_client: DockerClient) -> None:
    docker_client.swarm.update(autolock=True)
    first_key = docker_client.swarm.unlock_key()
    # make sure is doesn't change
    assert first_key == docker_client.swarm.unlock_key()

    # make sure it changes:
    assert first_key != docker_client.swarm.unlock_key(rotate=True)


def test_swarm_join_token_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.swarm.join_token("manager")

    assert e.value.stderr is not None
    assert "not a swarm manager" in e.value.stderr.lower()


def test_update_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.swarm.update(autolock=True)

    assert e.value.stderr is not None
    assert "not a swarm manager" in e.value.stderr.lower()


def test_unlock_key_not_swarm_manager(docker_client: DockerClient) -> None:
    with pytest.raises(NotASwarmManager) as e:
        docker_client.swarm.unlock_key()

    assert e.value.stderr is not None
    assert "not a swarm manager" in e.value.stderr.lower()
