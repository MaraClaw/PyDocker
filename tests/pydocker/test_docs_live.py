from collections.abc import Callable
from typing import Final
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from docs import docs_utils
from pydocker.components.system.models import SystemInfo
from pydocker.exceptions import DockerException

DEMOS: Final[dict[str, Callable[[], str]]] = {
    "networks": docs_utils.generate_code_demo_networks,
    "nodes": docs_utils.generate_code_demo_nodes,
    "services": docs_utils.generate_code_demo_services,
    "configs": docs_utils.generate_code_demo_configs,
    "volumes": docs_utils.generate_code_demo_volumes,
    "builders": docs_utils.generate_code_demo_builders,
    "images": docs_utils.generate_code_demo_images,
    "plugins": docs_utils.generate_code_demo_plugins,
    "containers": docs_utils.generate_code_demo_containers,
}


@pytest.mark.parametrize(
    "scenario", [(demo, failure) for demo in DEMOS for failure in (False, True)]
)
def test_live_demo_releases_its_acquisitions_when_rendering_finishes(
    scenario: tuple[str, bool], daemon: MagicMock, mocker: MockerFixture
) -> None:
    demo, format_fails = scenario
    provider = DEMOS[demo]
    if format_fails:
        mocker.patch.object(docs_utils, "write_code", side_effect=LookupError)

    if format_fails:
        with pytest.raises(LookupError):
            provider()
    else:
        output = provider()
        assert "In [" in output
        assert "type =" in output
        assert f"{demo[:-1]}." in output

    resources = {
        "networks": ["network", "container"],
        "nodes": [],
        "services": ["service"],
        "configs": ["config"],
        "volumes": ["volume"],
        "builders": ["builder"],
        "images": [],
        "plugins": [],
        "containers": ["container"],
    }
    for kind in resources[demo]:
        kwargs = {"force": True} if kind == "container" else {}
        getattr(daemon, f"example_{kind}").remove.assert_called_once_with(**kwargs)
    if demo in ("networks", "containers", "images", "services"):
        image_name = "busybox" if demo == "services" else "ubuntu"
        daemon.image.pull.assert_called_once_with(image_name)
        daemon.image.remove.assert_called_once_with(image_name, force=True, prune=False)
    if demo == "plugins":
        daemon.plugin.remove.assert_called_once_with(
            "mochoa/s3fs-volume-plugin", force=True
        )
    if demo in ("nodes", "services", "configs"):
        daemon.swarm.init.assert_called_once_with()
        daemon.swarm.leave.assert_called_once_with(force=True)
    if demo in ("networks", "containers"):
        assert daemon.run.call_args.kwargs["pull"] == "never"


@pytest.mark.parametrize(
    "scenario",
    [
        (demo, failure)
        for demo in (
            "networks",
            "containers",
            "images",
            "plugins",
            "nodes",
            "services",
            "configs",
        )
        for failure in (False, True)
    ],
)
def test_live_demo_preserves_borrowed_state_when_it_already_exists(
    scenario: tuple[str, bool], daemon: MagicMock, mocker: MockerFixture
) -> None:
    demo, format_fails = scenario
    daemon.image.inspect.side_effect = None
    daemon.image.inspect.return_value = daemon.example_image
    daemon.example_plugin.name = "mochoa/s3fs-volume-plugin:latest"
    daemon.plugin.list.return_value = [daemon.example_plugin]
    daemon.plugin.inspect.side_effect = None
    daemon.plugin.inspect.return_value = daemon.example_plugin
    daemon.system.info.return_value = SystemInfo.model_validate(
        {"Swarm": {"LocalNodeState": "active"}}
    )
    provider = DEMOS[demo]
    if format_fails:
        mocker.patch.object(docs_utils, "write_code", side_effect=LookupError)

    if format_fails:
        with pytest.raises(LookupError):
            provider()
    else:
        provider()

    daemon.image.pull.assert_not_called()
    daemon.image.remove.assert_not_called()
    daemon.example_image.remove.assert_not_called()
    daemon.plugin.install.assert_not_called()
    daemon.plugin.remove.assert_not_called()
    daemon.example_plugin.remove.assert_not_called()
    daemon.swarm.init.assert_not_called()
    daemon.swarm.leave.assert_not_called()


@pytest.mark.parametrize(
    "demo", ["networks", "containers", "services", "configs", "nodes"]
)
def test_live_demo_cleans_prior_acquisitions_when_next_provider_fails(
    demo: str, daemon: MagicMock
) -> None:
    failure = DockerException(["docker", "provider"], 1)
    if demo in ("networks", "containers"):
        daemon.run.side_effect = failure
    elif demo == "services":
        daemon.service.create.side_effect = failure
    elif demo == "configs":
        daemon.config.create.side_effect = failure
    else:
        daemon.node.list.side_effect = failure
    provider = DEMOS[demo]

    with pytest.raises(DockerException) as raised:
        provider()

    assert raised.value is failure
    if demo in ("networks", "containers", "services"):
        daemon.image.remove.assert_called_once()
    if demo == "networks":
        daemon.example_network.remove.assert_called_once_with()
    if demo in ("services", "configs", "nodes"):
        daemon.swarm.leave.assert_called_once_with(force=True)


@pytest.mark.parametrize("demo", ["images", "plugins"])
def test_live_demo_does_not_acquire_when_inventory_is_unavailable(
    demo: str, daemon: MagicMock
) -> None:
    failure = DockerException(["docker", "inventory"], 1)
    daemon.image.inspect.side_effect = failure
    daemon.plugin.list.side_effect = failure
    daemon.plugin.inspect.side_effect = failure
    provider = DEMOS[demo]

    with pytest.raises(DockerException) as raised:
        provider()

    assert raised.value is failure
    daemon.image.pull.assert_not_called()
    daemon.plugin.install.assert_not_called()


def test_live_network_and_config_use_fresh_names_when_host_has_named_resources(
    daemon: MagicMock,
) -> None:
    docs_utils.generate_code_demo_networks()
    docs_utils.generate_code_demo_configs()
    docs_utils.generate_code_demo_volumes()
    docs_utils.generate_code_demo_builders()

    assert daemon.network.create.call_args.args[0] != "my-network"
    assert daemon.config.create.call_args.args[0] != "my_config"
    assert daemon.volume.create.call_args.args[0].startswith("pydocker-docs-")
    assert daemon.buildx.create.call_args.kwargs["name"].startswith("pydocker-docs-")
