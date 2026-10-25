from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any, overload, override

import pydocker.components.task.cli_wrapper
from pydocker.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pydocker.components.node.models import (
    NodeDescription,
    NodeInspectResult,
    NodeManagerStatus,
    NodeSpec,
    NodeStatus,
    NodeVersion,
)
from pydocker.utils import run, to_list


class Node(ReloadableObjectFromJson[NodeInspectResult]):
    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        super().__init__(client_config, "id", reference, is_immutable_id)

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, Any]:
        json_str = run([*self.docker_cmd, "node", "inspect", reference])
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: dict[str, Any]) -> NodeInspectResult:
        return NodeInspectResult(**json_object)

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
            f"pydocker.Node(id='{self.id[:12]}', hostname='{hostname}', role='{role}')"
        )

    def update(
        self,
        availability: str | None = None,
        labels_add: Mapping[str, str] = MappingProxyType({}),
        rm_labels: Sequence[str] = (),
        role: str | None = None,
    ) -> None:
        """Updates this Swarm node.

        See [`docker.node.update`](../sub-commands/node.md#update) for more information
        about the arguments.
        """
        NodeCLI(self.client_config).update(
            self, availability, labels_add, rm_labels, role
        )

    def ps(self) -> list[pydocker.components.task.cli_wrapper.Task]:
        """Returns the list of tasks running on this node

        # Returns
            A `List[pydocker.Task]` object.

        """
        return NodeCLI(self.client_config).ps(self)


ValidNode = Node | str


class NodeCLI(DockerCLICaller):
    def demote(self, x: ValidNode | Sequence[ValidNode]) -> None:
        """Demote one or more nodes from manager in the swarm

        Parameters:
            x: One or a list of nodes.
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
        """Returns a `pydocker.Node` object from a string
        (id or hostname of the node)

        Parameters:
            x: One id or hostname or a list of ids or hostnames

        # Returns
            One or a list of `pydocker.Node`
        """
        if isinstance(x, str):
            return Node(self.client_config, x)
        else:
            return [Node(self.client_config, reference) for reference in x]

    def list(self) -> builtins.list[Node]:
        """Returns the list of nodes in this swarm.

        # Returns
            A `List[pydocker.Node]`
        """
        full_cmd = Command([*self.docker_cmd, "node", "list", "--quiet"])
        all_ids = run(full_cmd).splitlines()
        return [Node(self.client_config, x, is_immutable_id=True) for x in all_ids]

    def promote(self, x: ValidNode | Sequence[ValidNode]) -> None:
        """Promote one or more nodes to manager in the swarm

        Parameters:
            x: One or a list of nodes.
        """
        full_cmd = Command([*self.docker_cmd, "node", "promote"])
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)

    def ps(
        self, x: ValidNode | Sequence[ValidNode] | None = None
    ) -> builtins.list[pydocker.components.task.cli_wrapper.Task]:
        """Returns the list of swarm tasks running on one or more nodes.

        ```python
        from pydocker import docker

        tasks = docker.node.ps("my-node-name")
        print(tasks[0].desired_state)
        # running
        ```

        Parameters:
            x: One or more nodes (can be id, name or `pydocker.Node` object.).
                If the argument is not provided, it defaults to the current node.
                An empty list means an empty list will also be returned.

        # Returns
            `List[pydocker.Task]`
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
            pydocker.components.task.cli_wrapper.Task(
                self.client_config, id_, is_immutable_id=True
            )
            for id_ in ids
        ]

    def remove(self, x: ValidNode | Sequence[ValidNode], force: bool = False) -> None:
        """Remove one or more nodes from the swarm

        Parameters:
            x: One node or a list of nodes. You can use the id or the hostname of a node.
                You can also use a `pydocker.Node`.
            force: Force remove a node from the swarm
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
        """Updates a Swarm node.

        Parameters:
            node: The node to update, you can use a string or a `pydocker.Node`
                object.
            availability: Availability of the node ("active"|"pause"|"drain")
            labels_add: Remove a node label if exists
            rm_labels: Labels to remove from the node.
            role: Role of the node ("worker"|"manager")
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
