import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Literal, overload

from pydocker.client_config import Command, DockerCLICaller
from pydocker.utils import run

from .models import Manifest


class ImagetoolsCLI(DockerCLICaller):
    def inspect(self, name: str) -> Manifest:
        """Returns the manifest of a Docker image in a registry without pulling it"""
        full_cmd = Command(
            [*self.docker_cmd, "buildx", "imagetools", "inspect", "--raw", name]
        )
        result = run(full_cmd)
        return Manifest(**json.loads(result))

    @overload
    def create(
        self,
        sources: Sequence[str] = (),
        tags: Sequence[str] = (),
        append: bool = False,
        annotations: Mapping[str, str] = MappingProxyType({}),
        files: Sequence[str | Path] = (),
        *,
        dry_run: Literal[True],
        builder: str | None = None,
    ) -> Manifest: ...

    @overload
    def create(
        self,
        sources: Sequence[str] = (),
        tags: Sequence[str] = (),
        append: bool = False,
        annotations: Mapping[str, str] = MappingProxyType({}),
        files: Sequence[str | Path] = (),
        dry_run: Literal[False] = False,
        builder: str | None = None,
    ) -> None: ...

    @overload
    def create(
        self,
        sources: Sequence[str] = (),
        tags: Sequence[str] = (),
        append: bool = False,
        annotations: Mapping[str, str] = MappingProxyType({}),
        files: Sequence[str | Path] = (),
        dry_run: bool = False,
        builder: str | None = None,
    ) -> Manifest | None: ...

    def create(
        self,
        sources: Sequence[str] = (),
        tags: Sequence[str] = (),
        append: bool = False,
        annotations: Mapping[str, str] = MappingProxyType({}),
        files: Sequence[str | Path] = (),
        dry_run: bool = False,
        builder: str | None = None,
    ) -> Manifest | None:
        """
        Create a new manifest list based on source manifests.
        The source manifests can be manifest lists or single platform distribution manifests and
        must already exist in the registry where the new manifest is created.
        If only one source is specified, create performs a carbon copy.

        The CLI docs is [here](https://docs.docker.com/engine/reference/commandline/buildx_imagetools_create/)
        and it contains a lot more information.

        Parameters:
            sources: The sources manifest to create, change
            append: Append to existing manifest
            annotations: Add annotations to the image as a dict of key-value pairs
            dry_run: Show final image instead of pushing
            files: Read source descriptor from file
            builder: The builder to use.
        """
        if not isinstance(sources, Sequence) or isinstance(sources, str):
            raise TypeError(
                "The argument 'sources' of the function docker.buildx.imagetools.create() must be a list of strings."
            )
        if not isinstance(tags, Sequence) or isinstance(tags, str):
            raise TypeError(
                "The argument 'tags' of the function docker.buildx.imagetools.create() must be a list of strings."
            )
        if not isinstance(files, Sequence) or isinstance(files, str):
            raise TypeError(
                "The argument 'files' of the function docker.buildx.imagetools.create() must be a list of strings."
            )
        if not isinstance(annotations, Mapping):
            raise TypeError(
                "The argument 'annotations' of the function docker.buildx.imagetools.create() must be a dict."
            )

        full_cmd = Command([*self.docker_cmd, "buildx", "imagetools", "create"])
        for tag in tags:
            full_cmd.add_simple_arg("--tag", tag)
        for key, value in annotations.items():
            full_cmd.add_simple_arg("--annotation", f"{key}={value}")
        for file in files:
            full_cmd.add_simple_arg("--file", file)
        full_cmd.add_simple_arg("--builder", builder)
        full_cmd.add_flag("--append", append)
        full_cmd.add_flag("--dry-run", dry_run)
        full_cmd += sources

        result = run(full_cmd)
        if dry_run:
            return Manifest(**json.loads(result))
