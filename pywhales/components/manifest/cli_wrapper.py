"""Docker CLI manifest-list handles, local assembly, and registry publication."""

import json
from collections.abc import Iterable
from types import TracebackType
from typing import Self, override

from pydantic import JsonValue

from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components.buildx.imagetools.models import ImageVariantManifest
from pywhales.components.manifest.models import ManifestListInspectResult
from pywhales.utils import custom_parse_object_as, run, to_list, validate_timeout


class ManifestList(ReloadableObjectFromJson[ManifestListInspectResult]):
    """Reloadable manifest-list handle retaining its inspection reference.

    Context-manager exit attempts to remove the local manifest list. A failure
    in both the body and cleanup is reported as an exception group.
    """

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
        with resource_cleanup(exc_val, "ManifestList body and cleanup both failed"):
            self.remove()

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        cmd = Command([*self.docker_cmd, "manifest", "inspect", reference])
        cmd.add_flag("--insecure", self.insecure)
        json_str = run(cmd)
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> ManifestListInspectResult:
        json_object["name"] = self.reference
        return ManifestListInspectResult.model_validate(json_object)

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
        return f"pywhales.ManifestList(name='{self.name}')"

    def remove(self) -> None:
        """Remove this local Docker manifest list.

        A context manager attempts this removal even if its body raises.
        """
        ManifestCLI(self.client_config).remove(self)


ValidManifestList = ManifestList | str


class ManifestCLI(DockerCLICaller):
    """Manifest-list commands using local CLI state and registry access."""

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
        """Annotate an individual manifest in a local Docker manifest list.

        Args:
            name: The name of the manifest list
            manifest: The individual manifest to annotate
            arch: Architecture override, or None to omit it.
            os: Operating-system override, or None to omit it.
            os_features: Operating-system features, or None to omit the option.
            os_version: Operating-system version override, or None to omit it.
            variant: Architecture variant override, or None to omit it.
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
        """Create a local Docker manifest list.

        Args:
            name: Name for the manifest list.
            manifests: Manifest references to include.
            ammend: Amend an existing list rather than creating a new one.
            insecure: Allow communication with an insecure registry during creation.

        Returns:
            A handle to the created manifest list.
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
        """Inspect a Docker manifest list.

        Args:
            x: Manifest-list reference to inspect.
            insecure: Allow inspection through an insecure registry.

        Returns:
            A handle with the inspected manifest-list data.
        """
        return ManifestList(self.client_config, x, insecure=insecure)

    def push(
        self,
        x: str,
        purge: bool = False,
        quiet: bool = False,
        *,
        timeout: float | None = None,
    ) -> None:
        """Push a manifest list to a repository.

        Args:
            x: Manifest-list reference to inspect and push.
            purge: Remove the local manifest list after pushing.
            quiet: Capture and discard push output instead of displaying it.
            timeout: Positive finite seconds from the push CLI's own launch,
                replacing the client default; `None` inherits it. The local
                manifest-list preflight inspection does not consume this budget.

        Raises:
            ValueError: `timeout` is not positive and finite. Booleans are
                rejected before the preflight inspection.
            DockerTimeout: The push CLI exceeds its deadline and is killed and
                reaped, without undoing the push or guaranteeing that
                daemon-side work stops.
        """
        validate_timeout(timeout)
        # this is just to raise a correct exception if the manifest list doesn't exist
        self.inspect(x)

        full_cmd = Command([*self.docker_cmd, "manifest", "push"])
        full_cmd.add_flag("--purge", purge)
        full_cmd.append(x)
        run(full_cmd, capture_stdout=quiet, capture_stderr=quiet, timeout=timeout)

    def remove(
        self, manifest_lists: ValidManifestList | Iterable[ValidManifestList]
    ) -> None:
        """Remove one or more local Docker manifest lists.

        Args:
            manifest_lists: Manifest-list names or handles. An empty selection is
                a no-op.
        """
        selected = to_list(manifest_lists)
        if not selected:
            return
        full_cmd = Command([*self.docker_cmd, "manifest", "rm"])
        full_cmd += selected
        run(full_cmd)
