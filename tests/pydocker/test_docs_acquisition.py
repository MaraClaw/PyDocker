from collections.abc import Callable
from typing import Final
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from docs import docs_utils
from pydocker.components.system.models import SystemInfo
from pydocker.exceptions import DockerException

DEMOS: Final[dict[str, Callable[[], str]]] = {
    "networks": docs_utils.generate_code_demo_networks,
    "services": docs_utils.generate_code_demo_services,
    "volumes": docs_utils.generate_code_demo_volumes,
    "builders": docs_utils.generate_code_demo_builders,
    "images": docs_utils.generate_code_demo_images,
    "plugins": docs_utils.generate_code_demo_plugins,
    "containers": docs_utils.generate_code_demo_containers,
}


@pytest.mark.parametrize(
    "scenario",
    [
        (demo, acquired)
        for demo in ("images", "plugins", "containers", "volumes", "builders")
        for acquired in (False, True)
    ],
)
def test_live_demo_reclaims_partial_acquisition_when_metadata_loading_fails(
    scenario: tuple[str, bool], daemon: MagicMock
) -> None:
    demo, acquired = scenario
    failure = LookupError("after acquisition")
    if demo == "images":
        daemon.image.pull.side_effect = failure
        daemon.image.exists.return_value = acquired
        release = daemon.image.remove
    else:
        component = {
            "plugins": "plugin",
            "containers": "container",
            "volumes": "volume",
            "builders": "buildx",
        }[demo]
        kind = "builder" if demo == "builders" else demo[:-1]
        resource = getattr(daemon, f"example_{kind}")
        api = getattr(daemon, component)

        def fail_after_creation(name: str) -> None:
            resource.name = name
            api.list.return_value = [resource] if acquired else []
            raise failure

        if demo == "containers":

            def fail_after_run(*args: object, name: str, **kwargs: object) -> None:
                fail_after_creation(name)

            daemon.run.side_effect = fail_after_run
        else:
            method = api.install if demo == "plugins" else api.create
            method.side_effect = fail_after_creation
        release = daemon.plugin.remove if demo == "plugins" else resource.remove

    with pytest.raises(LookupError, match="after acquisition"):
        DEMOS[demo]()

    if acquired:
        release.assert_called_once()
    else:
        release.assert_not_called()
    if demo == "containers":
        daemon.image.remove.assert_called_once()


@pytest.mark.parametrize("demo", ["volumes", "builders", "containers"])
def test_live_demo_does_not_remove_reused_named_resources_when_name_exists(
    demo: str, daemon: MagicMock, mocker: MockerFixture
) -> None:
    identifier = UUID(int=0)
    mocker.patch.object(docs_utils, "uuid4", return_value=identifier)
    kind = "builder" if demo == "builders" else demo[:-1]
    api = daemon.buildx if demo == "builders" else getattr(daemon, kind)
    resource = getattr(daemon, f"example_{kind}")
    resource.name = f"pydocker-docs-{identifier.hex}"
    api.list.return_value = [resource]

    DEMOS[demo]()

    api.create.assert_not_called()
    if demo == "containers":
        daemon.run.assert_not_called()
    resource.remove.assert_not_called()


@pytest.mark.parametrize("state", ["active", "pending", "locked", "error", None])
def test_swarm_demo_preserves_membership_when_manager_operations_fail(
    state: str | None, daemon: MagicMock
) -> None:
    daemon.system.info.return_value = SystemInfo.model_validate(
        {"Swarm": {"LocalNodeState": state}}
    )
    daemon.node.list.side_effect = DockerException(["docker", "node", "ls"], 1)

    with pytest.raises(DockerException):
        docs_utils.generate_code_demo_nodes()

    daemon.swarm.init.assert_not_called()
    daemon.swarm.leave.assert_not_called()


def test_swarm_demo_does_not_leave_when_initialization_fails(daemon: MagicMock) -> None:
    daemon.swarm.init.side_effect = DockerException(["docker", "swarm", "init"], 1)

    with pytest.raises(DockerException):
        docs_utils.generate_code_demo_nodes()

    daemon.swarm.leave.assert_not_called()


@pytest.mark.parametrize("demo", ["networks", "services"])
def test_live_demo_releases_dependents_first_when_cleaning_up(
    demo: str, daemon: MagicMock
) -> None:
    DEMOS[demo]()

    names = [call[0] for call in daemon.mock_calls]
    order = (
        ["example_container.remove", "example_network.remove", "image.remove"]
        if demo == "networks"
        else ["example_service.remove", "image.remove", "swarm.leave"]
    )
    positions = [names.index(name) for name in order]
    assert positions == sorted(positions)


def test_live_volume_demo_formats_useful_values_when_requested(
    daemon: MagicMock,
) -> None:
    output = docs_utils.generate_code_demo_volumes()

    assert output.startswith(
        "In [4]: super_print(volume.name)\ntype = <class 'str'>, value = volume-name\n"
    )
