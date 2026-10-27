"""Swarm config handles and CLI operations requiring a manager daemon."""

from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Self, assert_never, overload, override

from pydantic import JsonValue

from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components._swarm_inspect import parse_swarm_inspect_results
from pywhales.components.config.models import (
    ConfigInspectResult,
    ConfigSpec,
    DockerObjectVersion,
)
from pywhales.utils import custom_parse_object_as, format_mapping_for_cli, run, to_list


class Config(ReloadableObjectFromJson[ConfigInspectResult]):
    """Reloadable Swarm config handle using its originating client settings.

    A context manager attempts removal on exit, including when its body fails.
    If both body and cleanup fail, both exceptions are preserved in a group.
    """

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
        with resource_cleanup(exc_val, "Config body and cleanup both failed"):
            self.remove()

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "config", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> ConfigInspectResult:
        return ConfigInspectResult.model_validate(json_object)

    def remove(self) -> None:
        """Remove this config.

        A context manager attempts this removal even if its body raises.
        """
        ConfigCLI(self.client_config).remove(self)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def version(self) -> DockerObjectVersion | None:
        return self._get_inspect_result().version

    @property
    def created_at(self) -> datetime | None:
        return self._get_inspect_result().created_at

    @property
    def updated_at(self) -> datetime | None:
        return self._get_inspect_result().updated_at

    @property
    def spec(self) -> ConfigSpec | None:
        return self._get_inspect_result().spec


ValidConfig = Config | str


class ConfigCLI(DockerCLICaller):
    """Swarm config commands using the originating client's connection settings."""

    def create(
        self,
        name: str,
        file: str | Path,
        labels: Mapping[str, str] = MappingProxyType({}),
        template_driver: str | None = None,
    ) -> Config:
        """Create a Swarm config from a file.

        See [the docker docs](https://docs.docker.com/engine/swarm/configs/)
        for more information about swarm configs.

        Args:
            name: The config name.
            file: Local file whose contents become the config.
            labels: Metadata labels; the default adds none.
            template_driver: Template driver, or None to omit the option.

        Returns:
            A handle to the newly created config.
        """
        full_cmd = Command([*self.docker_cmd, "config", "create"])
        full_cmd.add_args_iterable_or_single("--label", format_mapping_for_cli(labels))
        full_cmd.add_simple_arg("--template-driver", template_driver)
        full_cmd += [name, file]
        return Config(self.client_config, run(full_cmd), is_immutable_id=True)

    @overload
    def inspect(self, x: str) -> Config: ...

    @overload
    def inspect(self, x: builtins.list[str]) -> builtins.list[Config]: ...

    def inspect(self, x: str | builtins.list[str]) -> Config | builtins.list[Config]:
        """Inspect one or more Swarm configs by name or ID.

        Args:
            x: A name or ID, or a list of references. An empty list does not invoke
                the CLI.

        Returns:
            One handle for a string, otherwise handles in request order with
            duplicates retained as distinct handles. All snapshots are validated
            before any returned handle is hydrated.

        Raises:
            ParsingError: If the response cannot be associated unambiguously with
                the requested references or contains inconsistent snapshots.
        """
        references = to_list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "config", "inspect", *references])
        parser = Config(self.client_config, references[0], is_immutable_id=True)
        snapshots = parse_swarm_inspect_results(parser, json_str, references)
        resources = []
        for identity, snapshot in snapshots:
            resource = Config(self.client_config, identity, is_immutable_id=True)
            resource._set_inspect_result(snapshot)
            resources.append(resource)
        match x:
            case str():
                return resources[0]
            case builtins.list():
                return resources
            case unreachable:
                assert_never(unreachable)

    def list(
        self, filters: Mapping[str, str] = MappingProxyType({})
    ) -> builtins.list[Config]:
        """List configs available in the Swarm.

        Args:
            filters: Docker filter names and values; the default selects all configs.

        Returns:
            Config handles in CLI output order.
        """
        full_cmd = Command([*self.docker_cmd, "config", "list", "--quiet"])
        full_cmd.add_args_iterable_or_single(
            "--filter", format_mapping_for_cli(filters)
        )
        output = run(full_cmd)
        ids = output.splitlines()
        return [Config(self.client_config, id_, is_immutable_id=True) for id_ in ids]

    def remove(self, x: ValidConfig | Sequence[ValidConfig]) -> None:
        """Remove one or more configs.

        Args:
            x: Config names, IDs, or handles.
                An empty list means the function call does nothing.
        """
        full_cmd = Command([*self.docker_cmd, "config", "rm"])
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)
