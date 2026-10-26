from __future__ import annotations

import builtins
import json
import os
import tempfile
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import (
    Any,
    Literal,
    Self,
    overload,
    override,
)

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
from pywhales.components.volume.models import VolumeInspectResult
from pywhales.exceptions import NoSuchVolume
from pywhales.test_utils import random_name
from pywhales.utils import ValidPath, run, to_list

type VolumeListFilter = (
    tuple[Literal["driver"], str]
    | tuple[Literal["opt"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["label!"], str]
    | tuple[Literal["name"], str]
    | tuple[Literal["scope"], Literal["swarm", "global", "local"]]
    | tuple[Literal["after", "since"], str]
    | tuple[Literal["until"], str]
    | tuple[Literal["dangling"], str]
)


class Volume(ReloadableObjectFromJson[VolumeInspectResult]):
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
        try:
            self.remove()
        except NoSuchVolume:
            return
        except BaseException as cleanup_error:
            if exc_val is None:
                raise
            raise BaseExceptionGroup(
                "Volume body and cleanup both failed", [exc_val, cleanup_error]
            ) from None

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, Any]:
        json_str = run([*self.docker_cmd, "volume", "inspect", reference])
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: dict[str, Any]) -> VolumeInspectResult:
        return VolumeInspectResult(**json_object)

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
    def status(self) -> dict[str, Any] | None:
        return self._get_inspect_result().status

    @property
    def labels(self) -> dict[str, str] | None:
        return self._get_inspect_result().labels

    @property
    def scope(self) -> str | None:
        return self._get_inspect_result().scope

    @property
    def options(self) -> dict[str, str] | None:
        return self._get_inspect_result().options

    @override
    def __repr__(self) -> str:
        return f"pywhales.Volume(name='{self.name}', driver={self.driver})"

    def remove(self) -> None:
        """Removes this volume"""
        VolumeCLI(self.client_config).remove(self)

    def clone(
        self,
        new_volume_name: str | None = None,
        driver: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        options: Mapping[str, str] = MappingProxyType({}),
    ) -> Volume:
        """Creates a new volume and copy all the data inside.

        See the [`docker.volume.clone`](../sub-commands/volume.md#clone) command for
        information about the arguments.
        """
        return VolumeCLI(self.client_config).clone(
            self, new_volume_name, driver, labels, options
        )

    def exists(self) -> bool:
        """Returns `True` if the docker volume exists and `False` if it doesn't exists.

        If it doesn't exists, it most likely mean that it was removed.

         See the `docker.volume.exists` command for information about the arguments.
        """
        return VolumeCLI(self.client_config).exists(self.name)


ValidVolume = Volume | str
VolumePath = tuple[Volume | str, ValidPath]


class VolumeCLI(DockerCLICaller):
    def create(
        self,
        volume_name: str | None = None,
        driver: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        options: Mapping[str, str] = MappingProxyType({}),
    ) -> Volume:
        """Creates a volume

        Parameters:
            volume_name: The volume name, if not provided, a long random
                string will be used instead.
            driver: Specify volume driver name (default "local")
            labels: Set metadata for a volume
            options: Set driver specific options
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
                )
            volume = Volume(self.client_config, snapshot.name, is_immutable_id=True)
            volume._set_inspect_result(snapshot)
            volumes.append(volume)
        return volumes

    def exists(self, x: str) -> bool:
        """Returns `True` if the volume exists. `False` otherwise.

         It's just calling `docker.volume.inspect(...)` and verifies that it doesn't throw
         a `pywhales.exceptions.NoSuchVolume`.

        # Returns
            A `bool`
        """
        try:
            self.inspect(x)
        except NoSuchVolume:
            return False
        else:
            return True

    def list(
        self, filters: Iterable[VolumeListFilter] | Mapping[str, Any] = ()
    ) -> builtins.list[Volume]:
        """List volumes

        Parameters:
            filters: See the [Docker documentation page about filtering
                ](https://docs.docker.com/engine/reference/commandline/volume_ls/#filtering).
                An example `filters=[("dangling", "true"), ("driver", "local")]`.

        # Returns
            `List[pywhales.Volume]`
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
        full_cmd = Command([*self.docker_cmd, "volume", "list", "--quiet"])
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))

        volumes_names = run(full_cmd).splitlines()

        return [
            Volume(self.client_config, x, is_immutable_id=True) for x in volumes_names
        ]

    def prune(
        self,
        filters: Iterable[VolumeListFilter] | Mapping[str, Any] = (),
        all: bool = False,
    ) -> None:
        """Remove volumes

        Parameters:
            filters: See the [Docker documentation page about filtering
                ](https://docs.docker.com/engine/reference/commandline/volume_ls/#filtering).
                An example `filters=[("dangling", "true"), ("driver", "local")]`.
            all: Remove all unused volumes, not just anonymous ones.
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
        full_cmd = Command([*self.docker_cmd, "volume", "prune", "--force"])
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))

        run(full_cmd)

    def remove(self, x: ValidVolume | Sequence[ValidVolume]) -> None:
        """Removes one or more volumes

        Parameters:
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
        """Clone a volume.

        Parameters:
            source: The volume to clone
            new_volume_name: The new volume name. If not given, a random name is chosen.
            driver: Specify volume driver name (default "local")
            labels: Set metadata for a volume
            options: Set driver specific options

        # Returns
            A `pywhales.Volume`, the new volume.
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

        Parameters:
            source: If `source` is a directory/file inside a Docker volume,
                a tuple `(my_volume, path_in_volume)` must be provided. The volume
                can be a `pywhales.Volume` or a volume name as `str`. The path
                can be a `pathlib.Path` or a `str`. If `source` is  a local directory,
                a `pathlib.Path` or `str` should be provided. End the source path with
                `/.` if you want to copy the directory content in another directory.
            destination: Same as `source`.
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
