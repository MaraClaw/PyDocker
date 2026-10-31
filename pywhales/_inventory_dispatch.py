"""Resource-kind dispatch for detached inventory inspection."""

from collections.abc import Sequence
from typing import assert_never

from pywhales._bulk_reload import _inspect_group
from pywhales.client_config import ClientConfig
from pywhales.components.buildx.cli_wrapper import Builder
from pywhales.components.config.cli_wrapper import Config
from pywhales.components.container.cli_wrapper import Container
from pywhales.components.context.cli_wrapper import Context
from pywhales.components.image.cli_wrapper import Image
from pywhales.components.manifest.cli_wrapper import ManifestList
from pywhales.components.network.cli_wrapper import Network
from pywhales.components.node.cli_wrapper import Node
from pywhales.components.plugin.cli_wrapper import Plugin
from pywhales.components.secret.cli_wrapper import Secret
from pywhales.components.service.cli_wrapper import Service
from pywhales.components.task.cli_wrapper import Task
from pywhales.components.volume.cli_wrapper import Volume
from pywhales.inventory import InventoryKind, InventoryResource


def resource_kind(resource: InventoryResource) -> InventoryKind:
    match resource:
        case Container():
            return "container"
        case Image():
            return "image"
        case Network():
            return "network"
        case Volume():
            return "volume"
        case Node():
            return "node"
        case Service():
            return "service"
        case Config():
            return "config"
        case Secret():
            return "secret"
        case Task():
            return "task"
        case Plugin():
            return "plugin"
        case Context():
            return "context"
        case Builder():
            return "builder"
        case ManifestList():
            return "manifest_list"
        case unreachable:
            assert_never(unreachable)


def make_resource(
    kind: InventoryKind, config: ClientConfig, reference: str, *, insecure: bool = False
) -> InventoryResource:
    match kind:
        case "container":
            return Container(config, reference, is_immutable_id=True)
        case "image":
            return Image(config, reference, is_immutable_id=True)
        case "network":
            return Network(config, reference, is_immutable_id=True)
        case "volume":
            return Volume(config, reference, is_immutable_id=True)
        case "node":
            return Node(config, reference, is_immutable_id=True)
        case "service":
            return Service(config, reference, is_immutable_id=True)
        case "config":
            return Config(config, reference, is_immutable_id=True)
        case "secret":
            return Secret(config, reference, is_immutable_id=True)
        case "task":
            return Task(config, reference, is_immutable_id=True)
        case "plugin":
            return Plugin(config, reference, is_immutable_id=True)
        case "context":
            return Context(config, reference, is_immutable_id=True)
        case "builder":
            return Builder(config, reference, is_immutable_id=True)
        case "manifest_list":
            return ManifestList(
                config, reference, is_immutable_id=True, insecure=insecure
            )
        case unreachable:
            assert_never(unreachable)


def inspect_group(
    resources: Sequence[InventoryResource],
) -> Sequence[InventoryResource]:
    return _inspect_group(resources)
