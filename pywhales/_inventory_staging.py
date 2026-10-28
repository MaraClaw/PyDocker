"""Ordered, bounded resource inspection without publishing original caches."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import get_args

from pywhales._bulk_reload import _identity
from pywhales._environment import (
    finalize_environment,
    frozen_environment_scope,
    resolve_environment,
)
from pywhales._execution_policy import get_execution_policy
from pywhales._inventory_dispatch import inspect_group, make_resource, resource_kind
from pywhales._launch_limits import check_launch
from pywhales._observation import check_observation, observation
from pywhales.client_config import ClientConfig, ParsingError
from pywhales.components._swarm_inspect import (
    SwarmInspectResult,
    associate_swarm_inspect_results,
)
from pywhales.exceptions import ObservationLimitError
from pywhales.inventory import (
    InspectionPolicy,
    InventoryKind,
    InventoryRecord,
    InventoryRequest,
    InventoryResource,
)


@dataclass(frozen=True, slots=True)
class _Occurrence:
    index: int
    reference: str
    exact: bool


@dataclass(frozen=True, slots=True)
class _Group:
    kind: InventoryKind
    config: ClientConfig
    occurrences: list[_Occurrence]


def _normalize(items: Sequence[InventoryRequest | InventoryResource]) -> list[_Group]:
    groups: dict[tuple[int, InventoryKind], _Group] = {}
    index = 0
    for item in items:
        if isinstance(item, InventoryRequest):
            if item.kind not in get_args(InventoryKind.__value__):
                raise ValueError("Unsupported inventory kind")
            if not isinstance(item.client_config, ClientConfig):
                raise TypeError("Invalid client configuration")
            if (
                not isinstance(item.references, tuple)
                or not item.references
                or not all(
                    isinstance(reference, str) and reference
                    for reference in item.references
                )
            ):
                raise ValueError("Inventory references must be nonempty strings")
            kind, config, references, exact = (
                item.kind,
                item.client_config,
                item.references,
                False,
            )
        elif isinstance(item, InventoryResource.__value__):
            identity = _identity(item)
            if not isinstance(identity, str) or not identity:
                raise ParsingError("Missing immutable inspect identity")
            kind, config, references, exact = (
                resource_kind(item),
                item.client_config,
                (identity,),
                True,
            )
        else:
            raise TypeError("Unsupported inventory item")
        group = groups.setdefault((id(config), kind), _Group(kind, config, []))
        for reference in references:
            group.occurrences.append(_Occurrence(index, reference, exact))
            index += 1
    return list(groups.values())


def _prefix(group: _Group, arguments: Sequence[object]) -> list[str]:
    command = [str(argument) for argument in arguments]
    if group.kind == "task":
        return [*command, "inspect", "--type", "task"]
    return [*command, group.kind, "inspect"]


def _chunks(
    group: _Group,
    prefix: list[str],
    environment: Mapping[str, str],
    policy: InspectionPolicy,
) -> list[list[_Occurrence]]:
    chunks: list[list[_Occurrence]] = []
    chunk: list[_Occurrence] = []
    for occurrence in group.occurrences:
        candidate: list[_Occurrence] = [*chunk, occurrence]
        if len(candidate) <= policy.max_references:
            try:
                check_launch(
                    [*prefix, *(entry.reference for entry in candidate)],
                    environment,
                    policy,
                )
            except ObservationLimitError:
                if not chunk:
                    raise
            else:
                chunk = candidate
                continue
        chunks.append(chunk)
        chunk = [occurrence]
    if chunk:
        chunks.append(chunk)
    return chunks


def _stage(
    items: Sequence[InventoryRequest | InventoryResource], policy: InspectionPolicy
) -> tuple[list[InventoryRecord], list[InventoryResource]]:
    groups = _normalize(items)
    with observation(policy) as state:
        plans: list[
            tuple[_Group, list[str], Mapping[str, str], list[list[_Occurrence]]]
        ] = []
        for group in groups:
            check_observation(state)
            command = group.config.docker_cmd
            try:
                execution_policy = get_execution_policy(command)
                environment = resolve_environment(
                    execution_policy.environment,
                    frozen=execution_policy.frozen_environment,
                )
            except TypeError, ValueError:
                raise ObservationLimitError("environment", None, None) from None
            prefix = _prefix(group, command)
            environment = finalize_environment(environment, prefix)
            for occurrence in group.occurrences:
                check_launch([*prefix, occurrence.reference], environment, state.policy)
            plans.append(
                (
                    group,
                    prefix,
                    environment,
                    _chunks(group, prefix, environment, state.policy),
                )
            )
        records: dict[int, InventoryRecord] = {}
        resources: dict[int, InventoryResource] = {}
        for group, prefix, environment, chunks in plans:
            aliases: dict[str, str] = {}
            snapshots = {}
            swarm_models: list[SwarmInspectResult] = []
            group_ids: list[str] = []
            for chunk in chunks:
                check_observation(state)
                check_launch(
                    [*prefix, *(entry.reference for entry in chunk)],
                    environment,
                    state.policy,
                )
                request = [
                    make_resource(group.kind, group.config, entry.reference)
                    for entry in chunk
                ]
                with frozen_environment_scope(group.config, environment):
                    inspected = inspect_group(request)
                if len(inspected) != len(chunk):
                    raise ParsingError("Unexpected inspect result count")
                for entry, resource in zip(chunk, inspected, strict=True):
                    if resource_kind(resource) != group.kind:
                        raise TypeError("Inspect returned an unexpected resource kind")
                    identity = _identity(resource)
                    payload = resource._inspect_result
                    if (
                        payload is None
                        or getattr(payload, resource._id_in_inspect) != identity
                    ):
                        raise ParsingError(
                            "Missing or inconsistent staged inspect identity"
                        )
                    if entry.exact and entry.reference != identity:
                        raise ParsingError(
                            "Inspect identity does not match immutable resource identity"
                        )
                    if (
                        entry.reference in aliases
                        and aliases[entry.reference] != identity
                    ):
                        raise ParsingError("Inconsistent inspect identity")
                    if identity in snapshots and snapshots[identity] != payload:
                        raise ParsingError("Inconsistent inspect snapshot")
                    aliases[entry.reference] = identity
                    snapshots[identity] = payload
                    group_ids.append(identity)
                    if isinstance(payload, SwarmInspectResult.__value__):
                        swarm_models.append(payload)
                    records[entry.index] = InventoryRecord(
                        kind=group.kind,
                        id=identity,
                        payload=payload.model_copy(deep=True),
                    )
                    resources[entry.index] = deepcopy(
                        resource, {id(group.config): group.config}
                    )
            if swarm_models:
                resolved = associate_swarm_inspect_results(
                    swarm_models,
                    [entry.reference for entry in group.occurrences],
                    raw_diagnostics=group.config.raw_diagnostics,
                )
                if [identity for identity, _ in resolved] != group_ids:
                    raise ParsingError("Inspect associations differ across chunks")
        check_observation(state)
        return (
            [records[index] for index in range(len(records))],
            [resources[index] for index in range(len(resources))],
        )


def stage_resources(
    resources: Sequence[InventoryResource], policy: InspectionPolicy
) -> list[InventoryResource]:
    """Return newly hydrated handles in input order without changing originals."""
    return _stage(resources, policy)[1]
