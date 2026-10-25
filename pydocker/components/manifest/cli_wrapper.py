import json
from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self, override

from pydocker.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pydocker.components.buildx.imagetools.models import ImageVariantManifest
from pydocker.components.manifest.models import ManifestListInspectResult
from pydocker.utils import run, to_list


class ManifestList(ReloadableObjectFromJson[ManifestListInspectResult]):
    def __init__(
        self,
        client_config: ClientConfig,
        reference: str,
        is_immutable_id: bool = False,
        insecure: bool = False,
    ) -> None:
        self.reference = reference
        self.insecure = insecure
        super().__init__(client_config, "name", reference, is_immutable_id)

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
        cmd = Command([*self.docker_cmd, "manifest", "inspect", reference])
        cmd.add_flag("--insecure", self.insecure)
        json_str = run(cmd)
        return json.loads(json_str)

    @override
    def _parse_json_object(
        self, json_object: dict[str, Any]
    ) -> ManifestListInspectResult:
        json_object["name"] = self.reference
        return ManifestListInspectResult(**json_object)

    @property
    def name(self) -> str | None:
        return self._get_inspect_result().name

    @property
    def schema_version(self) -> int | None:
        return self._get_inspect_result().schema_version

    @property
    def media_type(self) -> str | None:
        return self._get_inspect_result().media_type

    @property
    def manifests(self) -> list[ImageVariantManifest] | None:
        return self._get_inspect_result().manifests

    @override
    def __repr__(self) -> str:
        return f"pydocker.ManifestList(name='{self.name}')"

    def remove(self) -> None:
        """Removes this Docker manifest list.

        Rather than removing it manually, you can use a context manager to
        make sure the manifest list is deleted even if an exception is raised.
        """
        ManifestCLI(self.client_config).remove(self)


ValidManifestList = ManifestList | str


class ManifestCLI(DockerCLICaller):
    def annotate(
        self,
        name: str,
        manifest: str,
        arch: str | None = None,
        os: str | None = None,
        os_features: list[str] | None = None,
        os_version: str | None = None,
        variant: str | None = None,
    ) -> None:
        """Annotates a Docker manifest list.

        Parameters:
            name: The name of the manifest list
            manifest: The individual manifest to annotate
            arch: The manifest's architecture
            os: The manifest's operating system
            os_features: The manifest's operating system features
            os_version: The manifest's operating system version
            variant: The manifest's architecture variant
        """
        full_cmd = Command([*self.docker_cmd, "manifest", "annotate"])
        full_cmd.add_simple_arg("--arch", arch)
        full_cmd.add_simple_arg("--os", os)
        full_cmd.add_simple_arg("--os-features", os_features)
        full_cmd.add_simple_arg("--os-version", os_version)
        full_cmd.add_simple_arg("--variant", variant)
        full_cmd.append(name)
        full_cmd.append(manifest)
        run(full_cmd)

    def create(
        self,
        name: str,
        manifests: list[str],
        ammend: bool = False,
        insecure: bool = False,
    ) -> ManifestList:
        """Creates a Docker manifest list.

        Parameters:
            name: The name of the manifest list
            manifests: The list of manifests to add to the manifest list

        # Returns
            A `pydocker.ManifestList`.
        """
        full_cmd = Command([*self.docker_cmd, "manifest", "create"])
        full_cmd.add_flag("--amend", ammend)
        full_cmd.add_flag("--insecure", insecure)
        full_cmd.append(name)
        full_cmd += to_list(manifests)
        return ManifestList(
            self.client_config, run(full_cmd)[22:], is_immutable_id=True
        )

    def inspect(self, x: str, insecure: bool = False) -> ManifestList:
        """Returns a Docker manifest list object."""
        return ManifestList(self.client_config, x, insecure=insecure)

    def push(self, x: str, purge: bool = False, quiet: bool = False) -> None:
        """Push a manifest list to a repository.

        # Options
            purge: Remove the local manifest list after push
        """
        # this is just to raise a correct exception if the manifest list doesn't exist
        self.inspect(x)

        full_cmd = Command([*self.docker_cmd, "manifest", "push"])
        full_cmd.add_flag("--purge", purge)
        full_cmd.append(x)
        run(full_cmd, capture_stdout=quiet, capture_stderr=quiet)

    def remove(
        self, manifest_lists: ValidManifestList | Sequence[ValidManifestList]
    ) -> None:
        """Removes a Docker manifest list or lists.

        Parameters:
            manifest_lists: One or more manifest lists.
        """
        if manifest_lists == []:
            return
        full_cmd = Command([*self.docker_cmd, "manifest", "rm"])
        full_cmd += to_list(manifest_lists)
        run(full_cmd)
