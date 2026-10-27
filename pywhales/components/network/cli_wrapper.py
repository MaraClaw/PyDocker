from __future__ import annotations

import builtins
import json
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType, TracebackType
from typing import (
    Any,
    Literal,
    Self,
    overload,
    override,
)

from pydantic import JsonValue

import pywhales.components.container.cli_wrapper
from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ParsingError,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components.network.models import (
    NetworkContainer,
    NetworkInspectResult,
    NetworkIPAM,
)
from pywhales.enums import ResourceScope, ResourceScopeInput
from pywhales.exceptions import NoSuchNetwork
from pywhales.utils import custom_parse_object_as, format_mapping_for_cli, run, to_list

type NetworkListFilter = (
    tuple[Literal["driver"], str]
    | tuple[Literal["id"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["label!"], str]
    | tuple[Literal["name"], str]
    | tuple[Literal["scope"], ResourceScopeInput]
    | tuple[Literal["type"], Literal["custom", "builtin"]]
    | tuple[Literal["until"], str]
    | tuple[Literal["dangling"], str]
)


class Network(ReloadableObjectFromJson[NetworkInspectResult]):
    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        super().__init__(client_config, "id", reference, is_immutable_id)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        with resource_cleanup(exc_val, "Network body and cleanup both failed"):
            try:
                self.remove()
            except NoSuchNetwork:
                return

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "network", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> NetworkInspectResult:
        return NetworkInspectResult.model_validate(json_object)

    @property
    def name(self) -> str | None:
        return self._get_inspect_result().name

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def created(self) -> datetime | None:
        return self._get_inspect_result().created

    @property
    def scope(self) -> ResourceScope | str | None:
        return self._get_inspect_result().scope

    @property
    def driver(self) -> str | None:
        return self._get_inspect_result().driver

    @property
    def enable_ipv6(self) -> bool | None:
        return self._get_inspect_result().enable_ipv6

    @property
    def ipam(self) -> NetworkIPAM | None:
        return self._get_inspect_result().ipam

    @property
    def internal(self) -> bool | None:
        return self._get_inspect_result().internal

    @property
    def attachable(self) -> bool | None:
        return self._get_inspect_result().attachable

    @property
    def ingress(self) -> bool | None:
        return self._get_inspect_result().ingress

    @property
    def containers(self) -> dict[str, NetworkContainer] | None:
        return self._get_inspect_result().containers

    @property
    def options(self) -> dict[str, JsonValue] | None:
        return self._get_inspect_result().options

    @property
    def labels(self) -> dict[str, str] | None:
        return self._get_inspect_result().labels

    @property
    def config_from(self) -> dict[str, JsonValue] | None:
        return self._get_inspect_result().config_from

    @property
    def config_only(self) -> bool | None:
        return self._get_inspect_result().config_only

    @override
    def __repr__(self) -> str:
        return f"pywhales.Network(id='{self.id[:12]}', name={self.name})"

    def exists(self) -> bool:
        """Returns `True` if the network exists and `False` if it doesn't exist.

        If it doesn't exist, that most likely means it was removed.
        """
        return NetworkCLI(self.client_config).exists(self.id)

    def remove(self) -> None:
        """Removes this Docker network.

        Rather than removing it manually, you can use a context manager to
        make sure the network is deleted even if an exception is raised.

        ```python
        from pywhales import docker

        with docker.network.create("some_name") as my_net:
            docker.run(
                "busybox",
                ["ping", "idonotexistatall.com"],
                networks=[my_net],
                remove=True,
            )
            # an exception will be raised because the container will fail
            # but the network will be removed anyway.
        ```

        """
        NetworkCLI(self.client_config).remove(self)


ValidNetwork = Network | str


class NetworkCLI(DockerCLICaller):
    def connect(
        self,
        network: ValidNetwork,
        container: pywhales.components.container.cli_wrapper.ValidContainer,
        alias: str | None = None,
        driver_options: Sequence[str] = (),
        ip: str | None = None,
        ip6: str | None = None,
        links: Sequence[pywhales.components.container.cli_wrapper.ValidContainer] = (),
    ) -> None:
        """Connect a container to a network

        Parameters:
            network: The network to connect to
            container: The container to connect
            alias: Add network-scoped alias for the container
            driver_options: Driver options for the network
            ip: IPv4 address (e.g., `"172.30.100.104"`)
            ip6: IPv6 address (e.g., `"2001:db8::33"`)
            links: Add links to other containers
        """
        full_cmd = Command([*self.docker_cmd, "network", "connect"])
        full_cmd.add_simple_arg("--alias", alias)
        full_cmd.add_args_iterable_or_single("--driver-opt", driver_options)
        full_cmd.add_simple_arg("--ip", ip)
        full_cmd.add_simple_arg("--ip6", ip6)
        full_cmd.add_args_iterable_or_single("--link", links)
        full_cmd += [network, container]
        run(full_cmd)

    def create(
        self,
        name: str,
        attachable: bool = False,
        driver: str | None = None,
        gateway: str | None = None,
        subnet: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        options: Sequence[str] = (),
    ) -> Network:
        """Creates a Docker network.

        Parameters:
            name: The name of the network

        # Returns
            A `pywhales.Network`.
        """
        full_cmd = Command([*self.docker_cmd, "network", "create"])
        full_cmd.add_flag("--attachable", attachable)
        full_cmd.add_simple_arg("--driver", driver)
        full_cmd.add_simple_arg("--gateway", gateway)
        full_cmd.add_simple_arg("--subnet", subnet)
        full_cmd.add_args_iterable_or_single("--label", format_mapping_for_cli(labels))
        full_cmd.add_args_iterable_or_single("--opt", options)
        full_cmd.append(name)
        return Network(self.client_config, run(full_cmd), is_immutable_id=True)

    def disconnect(
        self,
        network: ValidNetwork,
        container: pywhales.components.container.cli_wrapper.ValidContainer,
        force: bool = False,
    ) -> None:
        """Disconnect a container from a network

        Parameters:
            network: The name of the network.
            container: The container to disconnect.
        """
        full_cmd = Command([*self.docker_cmd, "network", "disconnect"])
        full_cmd.add_flag("--force", force)
        full_cmd += [network, container]
        run(full_cmd)

    def exists(
        self,
        network: ValidNetwork,
    ) -> bool:
        """Check if a network exists

        Parameters:
            network: The name of the network.
        """
        try:
            self.inspect(str(network))
        except NoSuchNetwork:
            return False
        else:
            return True

    @overload
    def inspect(self, x: str) -> Network: ...

    @overload
    def inspect(self, x: Iterable[str]) -> builtins.list[Network]: ...

    def inspect(self, x: str | Iterable[str]) -> Network | builtins.list[Network]:
        """Returns a `pywhales.Network` object from a string (id or network name).

        Parameters:
            x: One id or network name or a list of ids or network names.

        # Returns
            One or a list of `pywhales.Network`.
        """
        if isinstance(x, str):
            return Network(self.client_config, x)
        references = list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "network", "inspect", *references])
        parser = Network(self.client_config, references[0], is_immutable_id=True)
        snapshots = parser._parse_inspect_results(json_str, references)
        networks = []
        for reference, snapshot in zip(references, snapshots, strict=True):
            if snapshot.id is None or not (
                reference
                and (snapshot.id.startswith(reference) or snapshot.name == reference)
            ):
                raise ParsingError(
                    f"Network inspect identity does not match {reference!r}"
                )
            network = Network(self.client_config, snapshot.id, is_immutable_id=True)
            network._set_inspect_result(snapshot)
            networks.append(network)
        return networks

    def list(
        self, filters: Iterable[NetworkListFilter] | Mapping[str, Any] = ()
    ) -> builtins.list[Network]:
        """List all the networks available.

        Parameters:
            filters: Filters to apply when listing networks.

        # Returns
            List of `pywhales.Network`.
        """
        filter_items = filters.items() if isinstance(filters, Mapping) else filters
        if isinstance(filters, Mapping):
            warnings.warn(
                "Passing filters as a mapping is deprecated, replace with an "
                "iterable of tuples instead, as so:\n"
                f"filters={list(filter_items)}",
                DeprecationWarning,
                stacklevel=2,
            )
        full_cmd = Command([*self.docker_cmd, "network", "ls", "--no-trunc", "--quiet"])
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))

        ids = run(full_cmd).splitlines()
        return [Network(self.client_config, id_, is_immutable_id=True) for id_ in ids]

    def prune(
        self, filters: Iterable[NetworkListFilter] | Mapping[str, Any] = ()
    ) -> None:
        """Remove Docker networks which are not used by any containers.

        Parameters:
            filters: Filters to apply when finding networks to prune.
        """
        filter_items = filters.items() if isinstance(filters, Mapping) else filters
        if isinstance(filters, Mapping):
            warnings.warn(
                "Passing filters as a mapping is deprecated, replace with an "
                "iterable of tuples instead, as so:\n"
                f"filters={list(filter_items)}",
                DeprecationWarning,
                stacklevel=2,
            )
        full_cmd = Command([*self.docker_cmd, "network", "prune", "--force"])
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))
        run(full_cmd)

    def remove(self, networks: ValidNetwork | Sequence[ValidNetwork]) -> None:
        """Removes a Docker network

        Parameters:
            networks: One or more networks.
        """
        if networks == []:
            return
        full_cmd = Command([*self.docker_cmd, "network", "remove"])
        full_cmd += to_list(networks)
        run(full_cmd)
