"""Grouped resource inspection with deferred cache publication.

Validate and stage every group before updating original handles. Publication is
sequential, not a transaction across the daemon or concurrent cache readers.
"""

from collections.abc import Callable, Sequence
from functools import partial
from typing import assert_never

from pywhales.client_config import ParsingError, ReloadableObjectFromJson
from pywhales.components.config.cli_wrapper import Config, ConfigCLI
from pywhales.components.container.cli_wrapper import Container, ContainerCLI
from pywhales.components.context.cli_wrapper import Context, ContextCLI
from pywhales.components.image.cli_wrapper import Image, ImageCLI
from pywhales.components.network.cli_wrapper import Network, NetworkCLI
from pywhales.components.node.cli_wrapper import Node, NodeCLI
from pywhales.components.plugin.cli_wrapper import Plugin, PluginCLI
from pywhales.components.secret.cli_wrapper import Secret, SecretCLI
from pywhales.components.service.cli_wrapper import Service, ServiceCLI
from pywhales.components.task.cli_wrapper import Task, TaskCLI
from pywhales.components.volume.cli_wrapper import Volume, VolumeCLI

type SupportedResource = (
    Container
    | Image
    | Network
    | Volume
    | Node
    | Service
    | Config
    | Secret
    | Task
    | Plugin
    | Context
)


def _identity(resource: SupportedResource) -> str:
    """Return the stored identity without refreshing the resource.

    Args:
        resource: Handle whose immutable identity has already been resolved.

    Returns:
        The nonempty stored identity.

    Raises:
        ParsingError: If the stored identity is missing or empty.
    """
    identity = resource._get_immutable_id()
    if identity is None or identity == "":
        raise ParsingError("Missing immutable inspect identity")
    return identity


def _inspect_group(
    resources: Sequence[SupportedResource],
) -> Sequence[SupportedResource]:
    """Inspect a nonempty group sharing a resource kind and config instance.

    Args:
        resources: Handles grouped by exact kind and originating config identity.

    Returns:
        Newly inspected handles in requested order, leaving originals unchanged.
    """
    first = resources[0]
    references = [_identity(resource) for resource in resources]
    config = first.client_config
    match first:
        case Container():
            return ContainerCLI(config).inspect(references)
        case Image():
            return ImageCLI(config).inspect(references)
        case Network():
            return NetworkCLI(config).inspect(references)
        case Volume():
            return VolumeCLI(config).inspect(references)
        case Node():
            return NodeCLI(config).inspect(references)
        case Service():
            return ServiceCLI(config).inspect(references)
        case Config():
            return ConfigCLI(config).inspect(references)
        case Secret():
            return SecretCLI(config).inspect(references)
        case Task():
            return TaskCLI(config).inspect(references)
        case Plugin():
            return PluginCLI(config).inspect(references)
        case Context():
            return ContextCLI(config).inspect(references)
        case unreachable:
            assert_never(unreachable)


def _publication[InspectResultT, ReferenceT: str | None](
    original: ReloadableObjectFromJson[InspectResultT, ReferenceT],
    staged: ReloadableObjectFromJson[InspectResultT, ReferenceT],
) -> Callable[[], None]:
    """Prepare a cache update after verifying a staged snapshot's identity.

    Args:
        original: Handle that will receive the snapshot when the callback runs.
        staged: Inspected handle containing the proposed snapshot.

    Returns:
        A callback that installs the snapshot and updates its refresh timestamp.

    Raises:
        ParsingError: If the snapshot is absent or its identity differs from the
            original handle's stored identity.
    """
    snapshot = staged._inspect_result
    if snapshot is None:
        raise ParsingError("Missing staged inspect snapshot")
    if getattr(snapshot, original._id_in_inspect) != original._get_immutable_id():
        raise ParsingError(
            "Inspect identity does not match immutable resource identity"
        )
    return partial(original._set_inspect_result, snapshot)


def _prepare_publication(
    original: SupportedResource, staged: SupportedResource
) -> Callable[[], None]:
    """Match resource kinds and prepare a deferred snapshot update.

    Args:
        original: Existing resource handle to refresh.
        staged: Corresponding newly inspected handle.

    Returns:
        A validated publication callback that has not yet changed the original.

    Raises:
        TypeError: If the handles have different or unsupported resource kinds.
        ParsingError: If the staged snapshot or its identity is invalid.
    """
    match original, staged:
        case Container() as target, Container() as inspected:
            return _publication(target, inspected)
        case Image() as target, Image() as inspected:
            return _publication(target, inspected)
        case Network() as target, Network() as inspected:
            return _publication(target, inspected)
        case Volume() as target, Volume() as inspected:
            return _publication(target, inspected)
        case Node() as target, Node() as inspected:
            return _publication(target, inspected)
        case Service() as target, Service() as inspected:
            return _publication(target, inspected)
        case Config() as target, Config() as inspected:
            return _publication(target, inspected)
        case Secret() as target, Secret() as inspected:
            return _publication(target, inspected)
        case Task() as target, Task() as inspected:
            return _publication(target, inspected)
        case Plugin() as target, Plugin() as inspected:
            return _publication(target, inspected)
        case Context() as target, Context() as inspected:
            return _publication(target, inspected)
        case _:
            raise TypeError("Inspect returned an unexpected resource kind")


def bulk_reload(docker_objects: Sequence[object]) -> None:
    """Inspect supported resources and publish their caches after all groups pass.

    Validate every input before resolving any CLI executable. Group by exact
    config object identity and resource kind, inspecting each distinct handle
    once. Separate handles for the same resource each receive a snapshot.
    Inspection or validation failure leaves original caches and timestamps
    unchanged. Successful publication updates handles sequentially and does not
    provide a daemon transaction or atomic visibility to concurrent readers.

    Args:
        docker_objects: Container, image, network, volume, node, service, config,
            secret, task, plugin, or context handles. Empty input is a no-op.

    Raises:
        TypeError: If any input kind is unsupported or inspection returns a
            mismatched resource kind.
        ParsingError: If an identity or snapshot is missing or inconsistent.
        ValueError: If inspection returns a different number of handles.
        ClientNotFoundError: If a group's configured executable cannot be found.
        DockerException: If a group's inspect command fails.
    """
    groups: dict[tuple[int, type[SupportedResource]], list[SupportedResource]] = {}
    seen: set[int] = set()
    for resource in docker_objects:
        if not isinstance(
            resource,
            Container
            | Image
            | Network
            | Volume
            | Node
            | Service
            | Config
            | Secret
            | Task
            | Plugin
            | Context,
        ):
            raise TypeError("Unsupported resource kind for bulk reload")
        _identity(resource)
        if id(resource) in seen:
            continue
        seen.add(id(resource))
        key = id(resource.client_config), type(resource)
        groups.setdefault(key, []).append(resource)

    publications: list[Callable[[], None]] = []
    for resources in groups.values():
        snapshots: dict[str, object] = {}
        staged_resources = _inspect_group(resources)
        for original, staged in zip(resources, staged_resources, strict=True):
            identity = _identity(staged)
            if identity in snapshots and snapshots[identity] != staged._inspect_result:
                raise ParsingError("Inconsistent inspect snapshot")
            snapshots[identity] = staged._inspect_result
            publications.append(_prepare_publication(original, staged))
    for publish in publications:
        publish()
