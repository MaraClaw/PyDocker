import datetime as dt
from pathlib import Path

import pytest

from pydocker import DockerClient


@pytest.mark.usefixtures("swarm_mode")
def test_swarm_update_auto_lock_managers(
    docker_client: DockerClient, tmp_path: Path
) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("hello")
    my_secret = docker_client.secret.create("my_secret", secret_file)
    date_after_creation = dt.datetime.now(tz=dt.UTC)
    print("my_secret: ", my_secret.id)
    my_secret2 = docker_client.secret.inspect(my_secret.id)

    assert my_secret == my_secret2
    assert my_secret.spec is not None
    assert my_secret.spec.name == "my_secret"
    assert my_secret.spec.labels == {}
    assert my_secret.created_at is not None
    assert my_secret.created_at <= date_after_creation

    assert my_secret2.spec is not None
    assert my_secret2.spec.name == "my_secret"
    assert my_secret2.spec.labels == {}
    assert my_secret2.created_at is not None
    assert my_secret2.created_at <= date_after_creation

    my_secret3 = docker_client.secret.inspect("my_secret")

    assert my_secret == my_secret3
    assert my_secret3.spec is not None
    assert my_secret3.spec.name == "my_secret"
    assert my_secret3.spec.labels == {}
    assert my_secret3.created_at is not None
    assert my_secret3.created_at <= date_after_creation

    docker_client.secret.remove(my_secret.id)
