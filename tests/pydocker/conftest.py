from collections.abc import Callable, Sequence
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from docs import docs_utils
from pydocker import Builder, Container, Volume
from pydocker.components.system.models import SystemInfo
from pydocker.exceptions import DockerException, NoSuchImage


@pytest.fixture
def daemon(mocker: MockerFixture) -> MagicMock:
    client = MagicMock()
    mocker.patch.object(docs_utils, "docker", client)
    for kind in (
        "network",
        "node",
        "service",
        "config",
        "volume",
        "builder",
        "image",
        "plugin",
        "container",
    ):
        resource = MagicMock(name=kind)
        resource.id = f"{kind}-id"
        resource.name = f"{kind}-name"
        resource.remove.return_value = None
        resource.__enter__.return_value = resource
        resource.__exit__.side_effect = lambda *args, resource=resource: (
            resource.remove()
        )
        client.attach_mock(resource, f"example_{kind}")
    client.system.info.return_value = SystemInfo.model_validate(
        {"Swarm": {"LocalNodeState": "inactive"}}
    )
    client.node.list.return_value = [client.example_node]
    for kind in ("network", "service", "config", "volume"):
        getattr(client, kind).create.return_value = getattr(client, f"example_{kind}")
    client.buildx.create.return_value = client.example_builder
    client.run.return_value = client.example_container
    client.image.inspect.side_effect = NoSuchImage(["docker", "image", "inspect"], 1)
    client.image.pull.return_value = client.example_image
    client.image.exists.return_value = True
    client.plugin.list.return_value = []
    client.plugin.install.return_value = client.example_plugin
    client.plugin.inspect.side_effect = DockerException(
        ["docker", "plugin", "inspect"], 1
    )

    def inventory(
        command: Sequence[str], reference: Callable[[str], Builder | Container | Volume]
    ) -> dict[str, MagicMock]:
        resources = getattr(client, command[0]).list()
        return {resource.name: resource for resource in resources}

    def names(command: Sequence[str]) -> list[str]:
        if command[0] == "image":
            return (
                ["ubuntu:latest", "busybox:latest"]
                if client.image.exists.return_value
                else []
            )
        return [resource.name for resource in client.plugin.list()]

    mocker.patch.object(docs_utils, "_example_inventory", side_effect=inventory)
    mocker.patch.object(docs_utils, "_example_names", side_effect=names)
    mocker.patch("subprocess.run", side_effect=AssertionError("external command"))
    return client
