"""Swarm node inspection and administration through a manager's Docker CLI."""

from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import assert_never, overload, override

from pydantic import JsonValue

import pywhales.components.task.cli_wrapper
from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components._swarm_inspect import parse_swarm_inspect_results
from pywhales.components.node.models import (
    NodeDescription,
    NodeInspectResult,
    NodeManagerStatus,
    NodeSpec,
    NodeStatus,
    NodeVersion,
)
from pywhales.utils import custom_parse_object_as, run, to_list


class Node(ReloadableObjectFromJson[NodeInspectResult]):
    """Reloadable Swarm node handle retaining its originating client settings."""

    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        super().__init__(client_config, "id", reference, is_immutable_id)

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "node", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> NodeInspectResult:
        return NodeInspectResult.model_validate(json_object)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def version(self) -> NodeVersion | None:
        return self._get_inspect_result().version

    @property
    def created_at(self) -> datetime | None:
        return self._get_inspect_result().created_at

    @property
    def updated_at(self) -> datetime | None:
        return self._get_inspect_result().updated_at

    @property
    def spec(self) -> NodeSpec | None:
        return self._get_inspect_result().spec

    @property
    def description(self) -> NodeDescription | None:
        return self._get_inspect_result().description

    @property
    def status(self) -> NodeStatus | None:
        return self._get_inspect_result().status

    @property
    def manager_status(self) -> NodeManagerStatus | None:
        return self._get_inspect_result().manager_status

    @override
    def __repr__(self) -> str:
        description = self.description
        spec = self.spec
        hostname = description.hostname if description is not None else None
        role = spec.role if spec is not None else None
        return (
            f"pywhales.Node(id='{self.id[:12]}', hostname='{hostname}', role='{role}')"
        )

    def update(
        self,
        availability: str | None = None,
        labels_add: Mapping[str, str] = MappingProxyType({}),
        rm_labels: Sequence[str] = (),
        role: str | None = None,
    ) -> None:
        """Update this Swarm node.

        See [`docker.node.update`](../sub-commands/node.md#update) for more information
        about the arguments.
        """
        NodeCLI(self.client_config).update(
            self, availability, labels_add, rm_labels, role
        )

    def ps(self) -> list[pywhales.components.task.cli_wrapper.Task]:
        """Return task handles listed by Docker for this node."""
        return NodeCLI(self.client_config).ps(self)


ValidNode = Node | str


class NodeCLI(DockerCLICaller):
    """Node administration commands requiring access to a Swarm manager."""

    def demote(self, x: ValidNode | Sequence[ValidNode]) -> None:
        """Demote one or more Swarm managers to workers.

        Args:
            x: Node IDs, hostnames, or handles. An empty list is a no-op.
        """
        full_cmd = Command([*self.docker_cmd, "node", "demote"])
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)

    @overload
    def inspect(self, x: str) -> Node: ...

    @overload
    def inspect(self, x: builtins.list[str]) -> builtins.list[Node]: ...

    def inspect(self, x: str | builtins.list[str]) -> Node | builtins.list[Node]:
        """Inspect one or more Swarm nodes by ID or hostname.

        Args:
            x: One ID or hostname, or a list of references. An empty list does not
                invoke the CLI.

        Returns:
            One handle for a string, otherwise handles in request order with
            duplicates retained as distinct handles. All snapshots are validated
            before any returned handle is hydrated.

        Raises:
            ParsingError: If response identities cannot be associated unambiguously
                with the requests or snapshots are inconsistent.
        """
        references = to_list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "node", "inspect", *references])
        parser = Node(self.client_config, references[0], is_immutable_id=True)
        snapshots = parse_swarm_inspect_results(parser, json_str, references)
        resources = []
        for identity, snapshot in snapshots:
            resource = Node(self.client_config, identity, is_immutable_id=True)
            resource._set_inspect_result(snapshot)
            resources.append(resource)
        match x:
            case str():
                return resources[0]
            case builtins.list():
                return resources
            case unreachable:
                assert_never(unreachable)

    def list(self) -> builtins.list[Node]:
        """List nodes in the selected Swarm.

        Returns:
            Node handles in CLI output order.
        """
        full_cmd = Command([*self.docker_cmd, "node", "list", "--quiet"])
        all_ids = run(full_cmd).splitlines()
        return [Node(self.client_config, x, is_immutable_id=True) for x in all_ids]

    def promote(self, x: ValidNode | Sequence[ValidNode]) -> None:
        """Promote one or more Swarm nodes to managers.

        Args:
            x: Node IDs, hostnames, or handles. An empty list is a no-op.
        """
        full_cmd = Command([*self.docker_cmd, "node", "promote"])
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)

    def ps(
        self, x: ValidNode | Sequence[ValidNode] | None = None
    ) -> builtins.list[pywhales.components.task.cli_wrapper.Task]:
        """List Swarm tasks associated with one or more nodes.

        Args:
            x: One or more nodes (can be id, name or `pywhales.Node` object.).
                If the argument is not provided, it defaults to the current node.
                An empty list means an empty list will also be returned.

        Returns:
            Task handles in CLI output order, including any task history Docker lists.
        """
        if x == []:
            return []
        elif x is None:
            # this is the default, on the command line, nothing is provided, and then
            # it automatically defaults to the current node.
            command_args = []
        else:
            command_args = to_list(x)
        full_cmd = [
            *self.docker_cmd,
            "node",
            "ps",
            "--quiet",
            "--no-trunc",
            *command_args,
        ]
        ids = run(full_cmd).splitlines()
        return [
            pywhales.components.task.cli_wrapper.Task(
                self.client_config, id_, is_immutable_id=True
            )
            for id_ in ids
        ]

    def remove(self, x: ValidNode | Sequence[ValidNode], force: bool = False) -> None:
        """Remove one or more nodes from the Swarm.

        Args:
            x: One node or a list of nodes. You can use the id or the hostname of a node.
                You can also use a `pywhales.Node`.
            force: Force node removal when True.
        """
        full_cmd = Command([*self.docker_cmd, "node", "remove"])
        full_cmd.add_flag("--force", force)
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)

    def update(
        self,
        node: ValidNode,
        availability: str | None = None,
        labels_add: Mapping[str, str] = MappingProxyType({}),
        rm_labels: Sequence[str] = (),
        role: str | None = None,
    ) -> None:
        """Update a Swarm node.

        Args:
            node: The node to update, you can use a string or a `pywhales.Node`
                object.
            availability: Set active, pause, or drain; None leaves it unchanged.
            labels_add: Labels to add or update; the default adds none.
            rm_labels: Labels to remove from the node.
            role: Set worker or manager; None leaves it unchanged.
        """
        full_cmd = Command([*self.docker_cmd, "node", "update"])

        full_cmd.add_simple_arg("--availability", availability)
        for label_name, label_value in labels_add.items():
            full_cmd += ["--label-add", f"{label_name}={label_value}"]

        for label in rm_labels:
            full_cmd += ["--label-rm", label]

        full_cmd.add_simple_arg("--role", role)
        full_cmd.append(node)
        run(full_cmd)
