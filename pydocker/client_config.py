import json
import os
import shutil
import tempfile
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, override

import pydantic

from . import utils
from .utils import ValidPath, run, to_list

CACHE_VALIDITY_PERIOD: float = 0.01


class ParsingError(Exception):
    pass


class ClientNotFoundError(Exception):
    pass


class Command(list[object]):
    def add_simple_arg(self, name: str, value: object) -> None:
        if value is not None:
            self.extend([name, value])

    def add_flag(self, name: str, value: bool) -> None:
        if value:
            self.append(name)

    def add_args_iterable(self, arg_name: str, values: Iterable[object]) -> None:
        for value in values:
            self.extend([arg_name, value])

    def add_args_iterable_or_single(
        self, arg_name: str, iterable_or_single: object
    ) -> None:
        for value in to_list(iterable_or_single):
            self.extend([arg_name, value])

    def add_args_mapping(
        self, arg_name: str, mapping: Mapping[str, object], *, separator: str = "="
    ) -> None:
        self.add_args_iterable(
            arg_name, utils.format_mapping_for_cli(mapping, separator)
        )

    @override
    def __add__[ElementT](self, other: list[ElementT]) -> Command:
        return Command(super().__add__(other))


@dataclass
class ClientConfig:
    config: ValidPath | None = None
    context: str | None = None
    debug: bool | None = None
    host: str | None = None
    log_level: str | None = None
    tls: bool | None = None
    tlscacert: ValidPath | None = None
    tlscert: ValidPath | None = None
    tlskey: ValidPath | None = None
    tlsverify: bool | None = None
    client_binary_path: ValidPath | None = None
    compose_files: list[ValidPath] = field(default_factory=list)
    compose_profiles: list[str] = field(default_factory=list)
    compose_env_file: ValidPath | None = None
    compose_env_files: Sequence[ValidPath] = field(default_factory=list)
    compose_project_name: str | None = None
    compose_project_directory: ValidPath | None = None
    compose_compatibility: bool | None = None
    client_call: list[str] = field(default_factory=lambda: ["docker"])
    client_type: Literal["docker", "podman", "nerdctl", "unknown"] = "unknown"
    _client_call_with_path: list[Path | str] | None = None

    def get_client_call_with_path(self) -> list[Path | str]:
        if self._client_call_with_path is None:
            self._client_call_with_path = [
                Path(self._get_docker_path()),
                *self.client_call[1:],
            ]

        return self._client_call_with_path

    def _get_docker_path(self) -> str:
        which_result = shutil.which(self.client_call[0])
        if which_result is None:
            raise ClientNotFoundError(
                f"The binary '{self.client_call[0]}' could not be found on your PATH. "
                "Please ensure that your PATH has the directory of the binary you're looking for. "
                "You can use `print(os.environ['PATH'])` to verify what directories are in your PATH."
            )
        return which_result

    @property
    def docker_cmd(self) -> Command:
        result = Command(self.get_client_call_with_path())

        if self.config is not None:
            result += ["--config", self.config]

        if self.context is not None:
            result += ["--context", self.context]

        if self.debug is True:
            result.append("--debug")

        if self.host is not None:
            result += ["--host", self.host]

        if self.log_level is not None:
            result += ["--log-level", self.log_level]

        if self.tls is True:
            result.append("--tls")

        if self.tlscacert is not None:
            result += ["--tlscacert", self.tlscacert]

        if self.tlscert is not None:
            result += ["--tlscert", self.tlscert]

        if self.tlskey is not None:
            result += ["--tlskey", self.tlskey]

        if self.tlsverify is True:
            result.append("--tlsverify")

        return result

    @property
    def docker_compose_cmd(self) -> Command:
        base_cmd = Command([*self.docker_cmd, "compose"])
        base_cmd.add_args_iterable_or_single("--file", self.compose_files)
        base_cmd.add_args_iterable_or_single("--profile", self.compose_profiles)
        if self.compose_env_files:
            if self.compose_env_file is not None:
                warnings.warn(
                    "You can't set both `compose_env_file` and `compose_env_files`. Files used in `compose_env_files` will be used.",
                    stacklevel=2,
                )
            base_cmd.add_args_iterable("--env-file", self.compose_env_files)
        elif self.compose_env_file is not None:
            warnings.warn(
                "`compose_env_file` is deprecated. Use `compose_env_files` instead.",
                stacklevel=2,
            )
            base_cmd.add_simple_arg("--env-file", self.compose_env_file)

        base_cmd.add_simple_arg("--project-name", self.compose_project_name)
        base_cmd.add_simple_arg("--project-directory", self.compose_project_directory)
        base_cmd.add_flag("--compatibility", self.compose_compatibility is True)
        return base_cmd


class DockerCLICaller:
    def __init__(self, client_config: ClientConfig) -> None:
        self.client_config = client_config

    @property
    def docker_cmd(self) -> Command:
        return self.client_config.docker_cmd

    @property
    def docker_compose_cmd(self) -> Command:
        return self.client_config.docker_compose_cmd


class ReloadableObject[InspectResultT, ReferenceT: str | None = str](DockerCLICaller):
    def __init__(
        self,
        client_config: ClientConfig,
        id_in_inspect: str,
        reference_or_id: ReferenceT,
        is_immutable_id: bool = False,
    ) -> None:
        super().__init__(client_config)
        self._last_refreshed_time = datetime.min
        self._inspect_result: InspectResultT | None = None
        self._id_in_inspect = id_in_inspect
        if is_immutable_id:
            self._immutable_id: ReferenceT = reference_or_id
        else:
            inspect_result = self._fetch_and_parse_inspect_result(reference_or_id)
            self._set_inspect_result(inspect_result)
            self._immutable_id = getattr(inspect_result, id_in_inspect)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReloadableObject):
            return False
        return (
            self._get_immutable_id() == other._get_immutable_id()
            and self.client_config == other.client_config
        )

    @override
    def __str__(self) -> str:
        reference = self._get_immutable_id()
        if reference is None:
            raise TypeError("__str__ returned non-string (type NoneType)")
        return reference

    def _needs_reload(self) -> bool:
        return (datetime.now() - self._last_refreshed_time) >= timedelta(
            seconds=CACHE_VALIDITY_PERIOD
        )

    def reload(self) -> None:
        self._reload()

    def _reload(self) -> InspectResultT:
        inspect_result = self._fetch_and_parse_inspect_result(self._immutable_id)
        self._set_inspect_result(inspect_result)
        return inspect_result

    def _fetch_and_parse_inspect_result(self, reference: ReferenceT) -> InspectResultT:
        raise NotImplementedError

    def _get_inspect_result(self) -> InspectResultT:
        if self._inspect_result is None or self._needs_reload():
            return self._reload()
        return self._inspect_result

    def _set_inspect_result(self, inspect_result: InspectResultT) -> None:
        self._inspect_result = inspect_result
        self._last_refreshed_time = datetime.now()

    def _get_immutable_id(self) -> ReferenceT:
        return self._immutable_id

    @override
    def __hash__(self) -> int:
        # maybe we can do better.
        return hash(self._get_immutable_id())


class ReloadableObjectFromJson[InspectResultT, ReferenceT: str | None = str](
    ReloadableObject[InspectResultT, ReferenceT]
):
    def _fetch_inspect_result_json(self, reference: ReferenceT) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_json_object(self, json_object: dict[str, Any]) -> InspectResultT:
        raise NotImplementedError

    @override
    def _fetch_and_parse_inspect_result(self, reference: ReferenceT) -> InspectResultT:
        json_object = self._fetch_inspect_result_json(reference)
        try:
            return self._parse_json_object(json_object)
        except pydantic.ValidationError as err:
            fd, json_response_file = tempfile.mkstemp(suffix=".json", text=True)
            with os.fdopen(fd, "w") as f:
                json.dump(json_object, f, indent=2)

            raise ParsingError(
                f"There was an error parsing the json response from the Docker daemon. \n"
                f"This is a bug with pydocker itself. Please head to \n"
                f"https://github.com/MaraClaw/PyDocker/issues \n"
                f"and open an issue. You should copy this error message and \n"
                f"the json response from the Docker daemon. The json response was put \n"
                f"in {json_response_file} because it's a bit too big to be printed \n"
                f"on the screen. Make sure that there are no sensitive data in the \n"
                f"json file before copying it in the github issue."
            ) from err


def bulk_reload[InspectResultT, ReferenceT: str | None](
    docker_objects: Sequence[ReloadableObjectFromJson[InspectResultT, ReferenceT]],
) -> None:
    assert len({x.client_config for x in docker_objects}) == 1
    all_ids = [x._get_immutable_id() for x in docker_objects]
    full_cmd = [*docker_objects[0].docker_cmd, "inspect", *all_ids]
    json_str = run(full_cmd)
    for json_obj, docker_object in zip(
        json.loads(json_str), docker_objects, strict=False
    ):
        docker_object._set_inspect_result(docker_object._parse_json_object(json_obj))
