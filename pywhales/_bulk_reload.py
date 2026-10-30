"""Grouped resource inspection with deferred cache publication.

Validate and stage every group before updating original handles. Publication is
sequential, not a transaction across the daemon or concurrent cache readers.
"""

from collections.abc import Callable, Sequence
from functools import partial
from typing import TYPE_CHECKING, assert_never

from pywhales.client_config import ClientConfig, ParsingError, ReloadableObject
from pywhales.components.buildx.cli_wrapper import Builder, list_builder_snapshots
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

if TYPE_CHECKING:
    from pywhales.inventory import InspectionPolicy

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
    | Builder
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


def _inspect_builders(config: ClientConfig, names: Sequence[str]) -> list[Builder]:
    """Resolve builder names from one strictly parsed listing, in request order.

    Builders have no batch inspect command, so no name reaches argv. Names match
    exactly, and every duplicate request gets its own detached handle.

    Args:
        config: Originating config shared by the whole group.
        names: Requested builder names.

    Returns:
        Newly hydrated handles, one per requested name.

    Raises:
        ParsingError: If the listing is invalid or lacks a requested name.
    """
    snapshots = list_builder_snapshots(config)
    builders: list[Builder] = []
    for name in names:
        snapshot = snapshots.get(name)
        if snapshot is None:
            detail = f" {name!r}" if config.raw_diagnostics else ""
            raise ParsingError(f"Builder{detail} is absent from the builder listing")
        builder = Builder(config, name, is_immutable_id=True)
        builder._set_inspect_result(snapshot.model_copy(deep=True))
        builders.append(builder)
    return builders


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
        case Builder():
            return _inspect_builders(config, references)
        case unreachable:
            assert_never(unreachable)


def _publication[InspectResultT, ReferenceT: str | None](
    original: ReloadableObject[InspectResultT, ReferenceT],
    staged: ReloadableObject[InspectResultT, ReferenceT],
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
        case Builder() as target, Builder() as inspected:
            return _publication(target, inspected)
        case _:
            raise TypeError("Inspect returned an unexpected resource kind")


def bulk_reload(
    docker_objects: Sequence[object], *, policy: InspectionPolicy | None = None
) -> None:
    """Inspect supported resources and publish their caches after all groups pass.

    Validate every input before resolving any CLI executable. Group by exact
    config object identity and resource kind, inspecting each distinct handle
    once. Separate handles for the same resource each receive a snapshot.
    Inspection or validation failure leaves original caches and timestamps
    unchanged. Successful publication updates handles sequentially and does not
    provide a daemon transaction or atomic visibility to concurrent readers.

    Args:
        docker_objects: Container, image, network, volume, node, service, config,
            secret, task, plugin, context, or builder handles. Empty input is a
            no-op. Each builder group runs one listing and sends no names to argv.
        policy: Optional inspection limits, intersected with an active observation.

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
            | Context
            | Builder,
        ):
            raise TypeError("Unsupported resource kind for bulk reload")
        _identity(resource)
        if id(resource) in seen:
            continue
        seen.add(id(resource))
        key = id(resource.client_config), type(resource)
        groups.setdefault(key, []).append(resource)

    from pywhales._observation import check_observation, get_observation, observation

    current = get_observation()
    selected_policy = policy
    if selected_policy is None and current is not None:
        selected_policy = current.policy
    if groups and selected_policy is not None:
        from pywhales._inventory_staging import stage_resources

        with observation(selected_policy) as state:
            originals = [resource for group in groups.values() for resource in group]
            staged = stage_resources(originals, state.policy)
            publications = [
                _prepare_publication(original, snapshot)
                for original, snapshot in zip(originals, staged, strict=True)
            ]
            check_observation(state)
        for publish in publications:
            publish()
        return

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
