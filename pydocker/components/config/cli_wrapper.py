from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Self, overload, override

from pydocker.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pydocker.components.config.models import (
    ConfigInspectResult,
    ConfigSpec,
    DockerObjectVersion,
)
from pydocker.utils import format_mapping_for_cli, run, to_list


class Config(ReloadableObjectFromJson[ConfigInspectResult]):
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
        self.remove()

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, Any]:
        json_str = run([*self.docker_cmd, "config", "inspect", reference])
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: dict[str, Any]) -> ConfigInspectResult:
        return ConfigInspectResult(**json_object)

    def remove(self) -> None:
        """Remove this config.

        Note that you can also use a `pydocker.Config` as a context manager
        to ensure it's removed even if an exception occurs.
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
    def create(
        self,
        name: str,
        file: str | Path,
        labels: Mapping[str, str] = MappingProxyType({}),
        template_driver: str | None = None,
    ) -> Config:
        """Create a config from a file

        See [the docker docs](https://docs.docker.com/engine/swarm/configs/)
        for more information about swarm configs.

        Parameters:
            name: The config name.
            file: Tbe file to be used as config.
            labels: The labels to add to the config
            template_driver: The template driver

        # Returns
            A `pydocker.Config` object.
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
        """Returns a `pydocker.Config` object based on its name or id.

        # Argument
            x: An id or name or a list of ids/names.

        # Returns
            A `pydocker.Config` if a string was passed as argument. A
            `List[pydocker.Config]` if a list of strings was passed as argument.
        """
        if isinstance(x, str):
            return Config(self.client_config, x)
        else:
            return [Config(self.client_config, reference) for reference in x]

    def list(
        self, filters: Mapping[str, str] = MappingProxyType({})
    ) -> builtins.list[Config]:
        """List all config available in the swarm.

        Parameters:
            filters: If you want to filter the results based on a given condition.
                For example, `docker.config.list(filters=dict(label="my_label=hello"))`.

        # Returns
            A `List[pydocker.Config]`.
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

        Parameters:
            x: One or a list of configs. Valid values are the id of the config or
                a `pydocker.Config` object.
                An empty list means the function call does nothing.
        """
        full_cmd = Command([*self.docker_cmd, "config", "rm"])
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)
