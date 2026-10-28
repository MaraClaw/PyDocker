"""Daemon-volume handles and filesystem transfers using temporary Docker resources."""

from __future__ import annotations

import builtins
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import (
    Literal,
    Self,
    overload,
    override,
)

from pydantic import JsonValue

import pywhales.components.buildx.cli_wrapper
import pywhales.components.container.cli_wrapper
import pywhales.components.image.cli_wrapper
from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ParsingError,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components.volume.models import VolumeInspectResult
from pywhales.enums import ResourceScope, ResourceScopeInput
from pywhales.exceptions import NoSuchVolume
from pywhales.test_utils import random_name
from pywhales.utils import ValidPath, custom_parse_object_as, run, to_list

type VolumeListFilter = (
    tuple[Literal["driver"], str]
    | tuple[Literal["opt"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["label!"], str]
    | tuple[Literal["name"], str]
    | tuple[Literal["scope"], ResourceScopeInput]
    | tuple[Literal["after", "since"], str]
    | tuple[Literal["until"], str]
    | tuple[Literal["dangling"], str]
)


class Volume(ReloadableObjectFromJson[VolumeInspectResult]):
    """Reloadable Docker volume identified by name.

    Context-manager exit attempts removal, ignoring an already absent volume.
    Other cleanup failures are grouped with any active body exception.
    """

    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        super().__init__(client_config, "name", reference, is_immutable_id)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        with resource_cleanup(exc_val, "Volume body and cleanup both failed"):
            try:
                self.remove()
            except NoSuchVolume:
                return

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "volume", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> VolumeInspectResult:
        return VolumeInspectResult.model_validate(json_object)

    @property
    def name(self) -> str:
        return self._get_immutable_id()

    @property
    def driver(self) -> str | None:
        return self._get_inspect_result().driver

    @property
    def mountpoint(self) -> Path | None:
        return self._get_inspect_result().mountpoint

    @property
    def created_at(self) -> datetime | None:
        return self._get_inspect_result().created_at

    @property
    def status(self) -> dict[str, JsonValue] | None:
        return self._get_inspect_result().status

    @property
    def labels(self) -> dict[str, str] | None:
        return self._get_inspect_result().labels

    @property
    def scope(self) -> ResourceScope | str | None:
        return self._get_inspect_result().scope

    @property
    def options(self) -> dict[str, str] | None:
        return self._get_inspect_result().options

    @override
    def __repr__(self) -> str:
        return f"pywhales.Volume(name='{self.name}', driver={self.driver})"

    def remove(self) -> None:
        """Remove this volume through the configured Docker CLI."""
        VolumeCLI(self.client_config).remove(self)

    def clone(
        self,
        new_volume_name: str | None = None,
        driver: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        options: Mapping[str, str] = MappingProxyType({}),
    ) -> Volume:
        """Copy this volume's contents into a new or existing destination volume.

        See the [`docker.volume.clone`](../sub-commands/volume.md#clone) command for
        information about the arguments.
        """
        return VolumeCLI(self.client_config).clone(
            self, new_volume_name, driver, labels, options
        )

    def exists(self) -> bool:
        """Return whether this volume can still be inspected."""
        return VolumeCLI(self.client_config).exists(self.name)


ValidVolume = Volume | str
VolumePath = tuple[Volume | str, ValidPath]


class VolumeCLI(DockerCLICaller):
    """Volume commands whose copy helpers additionally require Buildx support."""

    def create(
        self,
        volume_name: str | None = None,
        driver: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        options: Mapping[str, str] = MappingProxyType({}),
    ) -> Volume:
        """Create and inspect a Docker volume.

        Args:
            volume_name: Requested name, or None to let Docker generate one.
            driver: Volume driver, or None to use Docker's default.
            labels: Metadata labels; the default adds none.
            options: Driver-specific options; the default adds none.

        Returns:
            An inspected handle to the volume Docker created or returned.
        """
        full_cmd = Command([*self.docker_cmd, "volume", "create"])

        full_cmd.add_simple_arg("--driver", driver)
        for key, value in labels.items():
            full_cmd += ["--label", f"{key}={value}"]
        for key, value in options.items():
            full_cmd += ["--opt", f"{key}={value}"]

        if volume_name is not None:
            full_cmd.append(volume_name)

        return Volume(self.client_config, run(full_cmd))

    @overload
    def inspect(self, x: str) -> Volume: ...

    @overload
    def inspect(self, x: Iterable[str]) -> builtins.list[Volume]: ...

    def inspect(self, x: str | Iterable[str]) -> Volume | builtins.list[Volume]:
        """Inspect one or more Docker volumes by name.

        Args:
            x: One volume name or an iterable of names. An empty iterable returns
                without invoking the CLI.

        Returns:
            One handle for a string, otherwise handles in request order with
            duplicates retained as distinct handles.

        Raises:
            ParsingError: If a batch response has invalid shape, inconsistent
                duplicate snapshots, or names that do not match the requests.
        """
        if isinstance(x, str):
            return Volume(self.client_config, x)
        references = list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "volume", "inspect", *references])
        parser = Volume(self.client_config, references[0], is_immutable_id=True)
        snapshots = parser._parse_inspect_results(json_str, references)
        volumes = []
        for reference, snapshot in zip(references, snapshots, strict=True):
            if snapshot.name is None or snapshot.name != reference:
                raise ParsingError(
                    f"Volume inspect identity does not match {reference!r}"
                    if self.client_config.raw_diagnostics
                    else "Volume inspect identity does not match the requested reference"
                )
            volume = Volume(self.client_config, snapshot.name, is_immutable_id=True)
            volume._set_inspect_result(snapshot)
            volumes.append(volume)
        return volumes

    def exists(self, x: str) -> bool:
        """Return whether a volume can be inspected.

        Args:
            x: Volume name to inspect.

        Returns:
            False when Docker reports NoSuchVolume; True on successful inspection.
            Other inspection failures propagate.
        """
        try:
            self.inspect(x)
        except NoSuchVolume:
            return False
        else:
            return True

    def list(self, filters: Iterable[VolumeListFilter] = ()) -> builtins.list[Volume]:
        """List Docker volumes.

        Args:
            filters: See the [Docker documentation page about filtering
                ](https://docs.docker.com/engine/reference/commandline/volume_ls/#filtering).
                An example `filters=[("dangling", "true"), ("driver", "local")]`.

        Returns:
            Volume handles in CLI output order.
        """
        if isinstance(filters, Mapping):
            raise TypeError("filters must be an iterable of pairs, not a mapping")
        full_cmd = Command([*self.docker_cmd, "volume", "list", "--quiet"])
        full_cmd.add_args_iterable(
            "--filter", (f"{key}={value}" for key, value in filters)
        )

        volumes_names = run(full_cmd).splitlines()

        return [
            Volume(self.client_config, x, is_immutable_id=True) for x in volumes_names
        ]

    def prune(
        self,
        filters: Iterable[VolumeListFilter] = (),
        all: bool = False,
    ) -> None:
        """Remove unused Docker volumes without a confirmation prompt.

        Args:
            filters: See the [Docker documentation page about filtering
                ](https://docs.docker.com/engine/reference/commandline/volume_ls/#filtering).
                An example `filters=[("dangling", "true"), ("driver", "local")]`.
            all: Remove all unused volumes, not just anonymous ones.
        """
        if isinstance(filters, Mapping):
            raise TypeError("filters must be an iterable of pairs, not a mapping")
        full_cmd = Command([*self.docker_cmd, "volume", "prune", "--force"])
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable(
            "--filter", (f"{key}={value}" for key, value in filters)
        )

        run(full_cmd)

    def remove(self, x: ValidVolume | Sequence[ValidVolume]) -> None:
        """Remove one or more volumes.

        Args:
            x: A volume or a list of volumes. An empty list as argument means
                nothing is done.
        """

        full_cmd = Command([*self.docker_cmd, "volume", "remove"])
        if x == []:
            return
        for v in to_list(x):
            full_cmd.append(str(v))

        run(full_cmd)

    def clone(
        self,
        source: ValidVolume,
        new_volume_name: str | None = None,
        driver: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        options: Mapping[str, str] = MappingProxyType({}),
    ) -> Volume:
        """Copy a volume's data into a newly created or existing named volume.

        Data passes through a temporary directory on the Python host using two
        copy operations. Buildx and the selected Docker daemon are required.
        A copy failure does not remove the destination volume.

        Args:
            source: Source volume name or handle.
            new_volume_name: Destination name, or None to let Docker generate one.
            driver: Destination volume driver, or None to use Docker's default.
            labels: Destination metadata labels; the default adds none.
            options: Destination driver options; the default adds none.

        Returns:
            The destination volume handle after both copies complete.
        """
        new_volume = self.create(new_volume_name, driver, labels, options)
        with tempfile.TemporaryDirectory() as temp_dir:
            self.copy((source, "."), temp_dir)
            self.copy(str(temp_dir) + "/.", (new_volume, ""))
        return new_volume

    def copy(
        self,
        source: ValidPath | VolumePath,
        destination: ValidPath | VolumePath,
    ) -> None:
        """Copy files/folders between a volume and the local filesystem.

        This builds a temporary image with Buildx and creates a container mounting
        the volume. The helper container and image are removed after a successful
        copy; failures can leave them behind. Use one volume endpoint and one
        local endpoint, with the local path resolved on the Python host.

        Args:
            source: If `source` is a directory/file inside a Docker volume,
                a tuple `(my_volume, path_in_volume)` must be provided. The volume
                can be a `pywhales.Volume` or a volume name as `str`. The path
                can be a `pathlib.Path` or a `str`. If `source` is  a local directory,
                a `pathlib.Path` or `str` should be provided. End the source path with
                `/.` if you want to copy the directory content in another directory.
            destination: Destination path, using the same endpoint notation.

        Raises:
            ValueError: If neither endpoint is a volume/path tuple.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            content = "FROM scratch\nCOPY Dockerfile /\nCMD /Dockerfile"
            (temp_dir / "Dockerfile").write_text(content)
            buildx = pywhales.components.buildx.cli_wrapper.BuildxCLI(
                self.client_config
            )
            image_name = random_name()
            dummy_image = buildx.build(
                temp_dir, tags=image_name, progress=False, load=True
            )
            assert isinstance(dummy_image, pywhales.components.image.cli_wrapper.Image)

        container = pywhales.components.container.cli_wrapper.ContainerCLI(
            self.client_config
        )
        volume_in_container = Path("/volume")
        if isinstance(source, tuple):
            volume_name = str(source[0])

            dummy_container = container.create(
                dummy_image, volumes=[(volume_name, volume_in_container)]
            )
            target = (
                (str(destination[0]), destination[1])
                if isinstance(destination, tuple)
                else destination
            )
            container.copy(
                (dummy_container, os.path.join(volume_in_container, source[1])),
                target,
            )
        elif isinstance(destination, tuple):
            volume_name = str(destination[0])
            dummy_container = container.create(
                dummy_image, volumes=[(volume_name, volume_in_container)]
            )
            container.copy(
                source,
                (dummy_container, os.path.join(volume_in_container, destination[1])),
            )
        else:
            raise ValueError("source or destination should be a tuple.")
        dummy_container.remove()
        pywhales.components.image.cli_wrapper.ImageCLI(self.client_config).remove(
            image_name
        )


VolumeDefinition = (
    tuple[Volume | ValidPath, ValidPath] | tuple[Volume | ValidPath, ValidPath, str]
)
