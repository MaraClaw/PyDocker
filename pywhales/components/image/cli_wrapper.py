"""Local image handles, registry transfers, and Docker archive operations.

Commands use the configured Docker CLI and selected daemon. Progress streams and
archive-byte iterators have different startup and cleanup contracts.
"""

from __future__ import annotations

import builtins
import json
import os
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from multiprocessing.pool import ThreadPool
from pathlib import Path
from subprocess import PIPE, Popen
from time import monotonic
from types import TracebackType
from typing import (
    Literal,
    Never,
    Self,
    overload,
    override,
)

from pydantic import JsonValue

import pywhales.components.buildx.cli_wrapper
from pywhales._archive_deadline import ArchiveDeadline
from pywhales._environment import finalize_environment, resolve_environment
from pywhales._execution_policy import get_execution_policy
from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ParsingError,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components.container.cli_wrapper import (
    ContainerCLI,
    ContainerConfig,
)
from pywhales.components.image.models import (
    ImageGraphDriver,
    ImageInspectResult,
    ImageRootFS,
)
from pywhales.components.image.streams import (
    EmptyImageStream,
    ImageStream,
    MultiImageStream,
)
from pywhales.exceptions import DockerException, NoSuchImage
from pywhales.utils import (
    MappedProcessStream,
    ValidPath,
    custom_parse_object_as,
    run,
    stream_stdout_and_stderr,
    to_list,
    validate_timeout,
)

type ImageListFilter = (
    tuple[Literal["id"], str]
    | tuple[Literal["reference"], str]
    | tuple[Literal["digest"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["label!"], str]
    | tuple[Literal["before"], str]
    | tuple[Literal["after", "since"], str]
    | tuple[Literal["until"], str]
    | tuple[Literal["dangling"], str]
    | tuple[Literal["intermediate"], str]
    | tuple[Literal["manifest"], str]
    | tuple[Literal["readonly"], str]
)


class Image(ReloadableObjectFromJson[ImageInspectResult]):
    """Reloadable local image handle retaining its originating configuration.

    Inspection-backed properties may refresh through the Docker CLI. Using the
    handle as a context manager opts into forced image removal on exit, even for
    a pre-existing image; an already missing image is tolerated during cleanup.
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
        with resource_cleanup(exc_val, "Image body and cleanup both failed"):
            try:
                self.remove(force=True)
            except NoSuchImage:
                return

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "image", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: Mapping[str, JsonValue]
    ) -> ImageInspectResult:
        return ImageInspectResult.model_validate(json_object)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def repo_tags(self) -> list[str] | None:
        return self._get_inspect_result().repo_tags

    @property
    def repo_digests(self) -> list[str] | None:
        return self._get_inspect_result().repo_digests

    @property
    def parent(self) -> str | None:
        return self._get_inspect_result().parent

    @property
    def comment(self) -> str | None:
        return self._get_inspect_result().comment

    @property
    def created(self) -> datetime | None:
        return self._get_inspect_result().created

    @property
    def container(self) -> str | None:
        return self._get_inspect_result().container

    @property
    def container_config(
        self,
    ) -> ContainerConfig | None:
        return self._get_inspect_result().container_config

    @property
    def docker_version(self) -> str | None:
        return self._get_inspect_result().docker_version

    @property
    def author(self) -> str | None:
        return self._get_inspect_result().author

    @property
    def config(
        self,
    ) -> ContainerConfig | None:
        return self._get_inspect_result().config

    @property
    def architecture(self) -> str | None:
        return self._get_inspect_result().architecture

    @property
    def os(self) -> str | None:
        return self._get_inspect_result().os

    @property
    def os_version(self) -> str | None:
        return self._get_inspect_result().os_version

    @property
    def variant(self) -> str | None:
        return self._get_inspect_result().variant

    @property
    def size(self) -> int | None:
        return self._get_inspect_result().size

    @property
    def virtual_size(self) -> int | None:
        return self._get_inspect_result().virtual_size

    @property
    def graph_driver(self) -> ImageGraphDriver | None:
        return self._get_inspect_result().graph_driver

    @property
    def root_fs(self) -> ImageRootFS | None:
        return self._get_inspect_result().root_fs

    @property
    def metadata(self) -> Mapping[str, str] | None:
        return self._get_inspect_result().metadata

    @override
    def __repr__(self) -> str:
        return f"pywhales.Image(id='{self.id[:20]}', tags={self.repo_tags})"

    def remove(self, force: bool = False, prune: bool = True) -> None:
        """Remove this Docker image.

        See the [`docker.image.remove`](../sub-commands/image.md#pywhales.components.image.cli_wrapper.ImageCLI.remove) command for
        information about the arguments.
        """
        ImageCLI(self.client_config).remove(self, force, prune)

    @overload
    def save(self, output: None = None) -> Iterator[bytes]: ...

    @overload
    def save(self, output: ValidPath) -> None: ...

    @overload
    def save(self, output: ValidPath | None) -> Iterator[bytes] | None: ...

    def save(self, output: ValidPath | None = None) -> Iterator[bytes] | None:
        """Save this image as a Docker tar archive.

        See the [`docker.image.save`](../sub-commands/image.md#pywhales.components.image.cli_wrapper.ImageCLI.save) command for
        information about the arguments.

        Args:
            output: Archive path, or `None` to return archive bytes lazily.

        Returns:
            An archive-byte generator when `output=None`; otherwise `None` after
            the save CLI completes. Inspection occurs during this call; the
            generator starts its save subprocess on first iteration. Close it
            when abandoning consumption to leave its subprocess context; cleanup
            can wait for the CLI and is not ProcessStream cancellation.
        """
        return ImageCLI(self.client_config).save(self, output)

    def tag(self, new_tag: str) -> None:
        """Add a tag to a Docker image.

        See the [`docker.image.tag`](../sub-commands/image.md#pywhales.components.image.cli_wrapper.ImageCLI.tag) command for
        information about the arguments.
        """
        return ImageCLI(self.client_config).tag(self, new_tag)

    def copy_from(
        self, path_in_image: ValidPath, destination: ValidPath, pull: str = "missing"
    ) -> None:
        """Copy files from this image to the local filesystem.

        The copy uses a temporary container that is removed on context exit.

        Args:
            path_in_image: Source path inside the image.
            destination: Local destination path.
            pull: Image acquisition policy, forwarded to container creation;
                defaults to `"missing"`.
        """
        return ImageCLI(self.client_config).copy_from(
            self, path_in_image, destination, pull
        )

    def copy_to(
        self,
        local_path: ValidPath,
        path_in_image: ValidPath,
        new_tag: str | None = None,
        pull: str = "missing",
    ) -> Image:
        """Copy local files into a temporary container and commit a new image.

        The temporary container is removed on context exit. The resulting image
        remains available; the original image is not modified.

        Args:
            local_path: Local source path.
            path_in_image: Destination path inside the new image.
            new_tag: Optional tag applied when committing; `None` omits a tag.
            pull: Image acquisition policy, forwarded to container creation;
                defaults to `"missing"`.

        Returns:
            The committed image using the originating client configuration.
        """
        return ImageCLI(self.client_config).copy_to(
            self, local_path, path_in_image, new_tag, pull
        )

    def exists(self) -> bool:
        """Return whether this image's immutable ID still exists in the daemon.

        Note that you might have done `docker.image.remove("some_tag")` and the image
        might still exist because PyWhales references images by ID, not by tag.

        Only a missing-image failure becomes `False`; other CLI failures propagate.
        """
        return ImageCLI(self.client_config).exists(self.id)


ValidImage = str | Image


class ImageCLI(DockerCLICaller):
    """Image commands sharing client configuration and Buildx build behavior."""

    def __init__(self, client_config: ClientConfig) -> None:
        super().__init__(client_config)
        self.build = pywhales.components.buildx.cli_wrapper.BuildxCLI(
            client_config
        ).build

    def history(self) -> Never:
        """Raise NotImplementedError because image history is not implemented.

        Raises:
            NotImplementedError: This operation has no implementation.
        """
        raise NotImplementedError

    def import_(
        self,
        source: ValidPath,
        tag: str | None = None,
        changes: Iterable[str] = (),
        message: str | None = None,
        platform: str | None = None,
    ) -> Image:
        """Import a tarball's contents as a new filesystem image.

        Alias: `docker.import_(...)`

        Args:
            source: Archive path or source accepted by `docker image import`.
            tag: Optional repository tag for the imported image.
            changes: Apply Dockerfile instruction to the created image
            message: Set commit message for imported image
            platform: Set platform if server is multi-platform capable

        Returns:
            The imported image, inspected using the originating configuration.
        """
        full_cmd = Command([*self.docker_cmd, "image", "import"])
        full_cmd.add_args_iterable("--change", changes)
        full_cmd.add_simple_arg("--message", message)
        full_cmd.add_simple_arg("--platform", platform)
        full_cmd.append(source)
        if tag is not None:
            full_cmd.append(tag)
        return Image(self.client_config, run(full_cmd))

    @overload
    def inspect(self, x: str) -> Image: ...

    @overload
    def inspect(self, x: Iterable[str]) -> builtins.list[Image]: ...

    def inspect(self, x: str | Iterable[str]) -> Image | builtins.list[Image]:
        """Inspect one or more local image references and return image handles.

        Iterable input uses one batch inspection and validates every snapshot's
        identity before returning handles with seeded inspection caches.

        Args:
            x: A reference, or references in the desired result order. Tags,
                digests, and IDs are accepted; an empty iterable skips the CLI.

        Returns:
            One image for a string, or a list preserving iterable order and
            duplicates as separate handles. Empty iterable input returns `[]`.

        Raises:
            NoSuchImage: A requested image does not exist in the selected daemon.
            ParsingError: Batch data cannot be validated or associated with its
                requested image reference.
        """
        if isinstance(x, str):
            return Image(self.client_config, x)
        references = list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "image", "inspect", *references])
        parser = Image(self.client_config, references[0], is_immutable_id=True)
        snapshots = parser._parse_inspect_results(json_str, references)

        def normalized(reference: str) -> str:
            reference = reference.removeprefix("index.docker.io/").removeprefix(
                "docker.io/"
            )
            reference = reference.removeprefix("library/")
            if "@" in reference:
                repository, digest = reference.split("@", 1)
                if ":" in repository.rsplit("/", 1)[-1]:
                    repository = repository.rsplit(":", 1)[0]
                return repository + "@" + digest
            if ":" not in reference.rsplit("/", 1)[-1]:
                return reference + ":latest"
            return reference

        images = []
        for reference, snapshot in zip(references, snapshots, strict=True):
            aliases = [*(snapshot.repo_tags or []), *(snapshot.repo_digests or [])]
            normalized_aliases = {normalized(alias) for alias in aliases}
            if snapshot.id is None or not (
                reference
                and (
                    snapshot.id.startswith(reference)
                    or snapshot.id.removeprefix("sha256:").startswith(reference)
                    or normalized(reference) in normalized_aliases
                )
            ):
                raise ParsingError(
                    f"Image inspect identity does not match {reference!r}"
                    if self.client_config.raw_diagnostics
                    else "Image inspect identity does not match the requested reference"
                )
            image = Image(self.client_config, snapshot.id, is_immutable_id=True)
            image._set_inspect_result(snapshot)
            images.append(image)
        return images

    def exists(self, x: str) -> bool:
        """Return whether a local image reference can be inspected.

        Args:
            x: Image tag, digest, or ID in the selected daemon.

        Returns:
            `False` only when inspection raises `NoSuchImage`; `True` on success.
            Other CLI failures propagate.
        """
        try:
            self.inspect(x)
        except NoSuchImage:
            return False
        else:
            return True

    def load(
        self, input: ValidPath | bytes | Iterator[bytes], quiet: bool = False
    ) -> builtins.list[str]:
        """Load images eagerly from an archive path or archive bytes.

        Alias: `docker.load(...)`

        Path input consumes a process-backed progress stream before returning.
        Byte input is written to a local CLI's stdin and fully consumed during
        the call. These archive bytes are not progress-log records. A configured
        default deadline reaps the local CLI even if the input iterator stalls;
        an iterator's Python code is not interrupted.

        Args:
            input: Archive path, complete archive bytes, or an iterator of archive
                byte chunks. Loading adds images and tags to the selected daemon.
            quiet: Pass the CLI's quiet flag. For path input, also suppress
                printing of decoded output records.

        Returns:
            Image tags or IDs extracted from `Loaded image` output lines, in
            output order, for both paths and byte input.
        """
        full_cmd = Command([*self.docker_cmd, "image", "load"])

        if isinstance(input, (str, Path)):
            full_cmd += ["--input", str(input)]

        if quiet:
            full_cmd.append("--quiet")

        if isinstance(input, (str, Path)):
            all_tags = []
            for _, stream_bytes in stream_stdout_and_stderr(full_cmd):
                decoded = stream_bytes.decode()[:-1]
                if not quiet:
                    print(decoded)
                if "Loaded image" in decoded:
                    all_tags.append(decoded.split(" ")[-1])
            return all_tags

        input_chunks = (input,) if isinstance(input, bytes) else input
        stdout_lines = self._load_from_generator(full_cmd, input_chunks)
        all_tags = []
        for line in stdout_lines:
            if "Loaded image" in line:
                all_tags.append(line.split(" ")[-1])
        return all_tags

    def _load_from_generator(
        self, full_cmd: Sequence[object], input: Iterable[bytes]
    ) -> builtins.list[str]:
        policy = get_execution_policy(full_cmd)
        validate_timeout(policy.default_timeout)
        environment = resolve_environment(
            policy.environment, frozen=policy.frozen_environment
        )
        command = [str(argument) for argument in full_cmd]
        environment = finalize_environment(environment, command)
        deadline = (
            None
            if policy.default_timeout is None
            else monotonic() + policy.default_timeout
        )
        with Popen(
            command,
            stdin=PIPE,
            stdout=PIPE,
            env=environment,
            bufsize=0 if deadline is not None else -1,
        ) as process:
            assert process.stdin is not None
            assert process.stdout is not None
            owner = (
                None
                if deadline is None
                else ArchiveDeadline(
                    process, command, deadline, raw=policy.raw_diagnostics
                )
            )
            for buffer_bytes in input:
                if owner is None:
                    process.stdin.write(buffer_bytes)
                    process.stdin.flush()
                else:
                    owner.write(process.stdin, buffer_bytes)
            if owner is not None:
                owner.check()
            process.stdin.close()
            if owner is None:
                stdout = process.stdout.read()
            else:
                chunks = []
                while chunk := owner.read(process.stdout):
                    chunks.append(chunk)
                stdout = b"".join(chunks)
                owner.check()
            exit_code = process.wait()
            if owner is not None:
                owner.check()
        if exit_code != 0:
            raise DockerException(
                command, exit_code, raw_diagnostics=policy.raw_diagnostics
            )
        return stdout.decode().splitlines()

    def list(
        self,
        repository_or_tag: str | None = None,
        filters: Iterable[ImageListFilter] = (),
        all: bool = False,
    ) -> builtins.list[Image]:
        """Return unique image IDs present in the selected Docker daemon.

        Alias: `docker.images()`

        Note that each image may have multiple tags.

        Args:
            repository_or_tag: Optional CLI repository or tag selector.
            filters: CLI filter pairs.
            all: Include intermediate images.

        Returns:
            One handle per distinct image ID, with unspecified order, retaining
            the originating client configuration.
        """
        if isinstance(filters, Mapping):
            raise TypeError("filters must be an iterable of pairs, not a mapping")
        full_cmd = Command(
            [
                *self.docker_cmd,
                "image",
                "list",
                "--quiet",
                "--no-trunc",
            ]
        )
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable(
            "--filter", (f"{key}={value}" for key, value in filters)
        )

        if repository_or_tag is not None:
            full_cmd.append(repository_or_tag)

        ids = run(full_cmd).splitlines()
        # the list of tags is bigger than the number of images. We uniquify
        ids = set(ids)

        return [Image(self.client_config, x, is_immutable_id=True) for x in ids]

    def prune(
        self,
        all: bool = False,
        filters: Iterable[ImageListFilter] = (),
    ) -> str:
        """Remove unused images without prompting.

        Args:
            all: Remove all unused images, not just dangling ones
            filters: Provide filter values (e.g. `[("until", "<timestamp>")]`)

        Returns:
            The output of the CLI (the layers removed).
        """
        if isinstance(filters, Mapping):
            raise TypeError("filters must be an iterable of pairs, not a mapping")
        full_cmd = Command([*self.docker_cmd, "image", "prune", "--force"])
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable(
            "--filter", (f"{key}={value}" for key, value in filters)
        )
        return run(full_cmd)

    @overload
    def pull(
        self,
        x: str,
        quiet: bool = False,
        stream_logs: Literal[False] = False,
        platform: str | None = None,
    ) -> Image: ...

    @overload
    def pull(
        self,
        x: Iterable[str],
        quiet: bool = False,
        stream_logs: Literal[False] = False,
        platform: str | None = None,
    ) -> builtins.list[Image]: ...

    @overload
    def pull(
        self,
        x: str,
        quiet: bool = False,
        *,
        stream_logs: Literal[True],
        platform: str | None = None,
    ) -> ImageStream: ...

    @overload
    def pull(
        self,
        x: str,
        quiet: bool,
        stream_logs: Literal[True],
        platform: str | None = None,
    ) -> ImageStream: ...

    @overload
    def pull(
        self,
        x: Iterable[str],
        quiet: bool = False,
        *,
        stream_logs: Literal[True],
        platform: str | None = None,
    ) -> ImageStream | EmptyImageStream: ...

    @overload
    def pull(
        self,
        x: Iterable[str],
        quiet: bool,
        stream_logs: Literal[True],
        platform: str | None = None,
    ) -> ImageStream | EmptyImageStream: ...

    @overload
    def pull(
        self,
        x: str,
        quiet: bool = False,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> Image | ImageStream: ...

    @overload
    def pull(
        self,
        x: Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> builtins.list[Image] | ImageStream | EmptyImageStream: ...

    def pull(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> Image | builtins.list[Image] | ImageStream | EmptyImageStream:
        """Pull one or more image references into the selected Docker daemon.

        Alias: `docker.pull(...)`

        Blocking transfers start during the call and use at most four workers
        for multiple distinct references. Streaming one unique reference starts
        its CLI before returning; streaming several starts up to four workers on
        first consumption, not on construction or context entry.

        Nonempty streams own their local CLIs. Exhaust them or use a context
        manager or `close()` on early exit; a bare loop break does not close them.
        Closing cancels local CLIs without undoing pulls or guaranteeing that
        daemon-side transfers stop. The selected image/general stream budget is
        snapshotted before consuming references and retained for queued children.

        Args:
            x: Image reference or iterable of references. Iterable references are
                deduplicated in first-seen order before selecting transfer mode.
            quiet: If you don't want to see the progress bars.
            stream_logs: Return owned `(image_reference, bytes)` progress records
                instead of waiting for transfers and returning image handles.
            platform: If you want to enforce a platform.

        Returns:
            One image for a string, or an ordered list for iterable input when
            `stream_logs=False`, even for one unique reference. With streaming,
            return an owned stream of `(image_reference, bytes)` progress records,
            without global ordering across images or stdout/stderr. Empty
            iterable input returns `[]`, or an empty generator when streaming.

        Raises:
            ValueError: `quiet=True` is combined with `stream_logs=True`.
            DockerException: A transfer or subsequent image inspection fails.
            BufferError: A streamed record or retained stderr exceeds the selected
                byte budget. Multi-image failure also cancels sibling transfers.
        """
        if quiet and stream_logs:
            raise ValueError(
                "It's not possible to have stream_logs=True and quiet=True at the same time. "
                "Only one can be activated at a time."
            )

        buffer_bytes = self.client_config.image_stream_buffer_bytes
        if buffer_bytes is None:
            buffer_bytes = self.client_config.stream_buffer_bytes

        if isinstance(x, str):
            result = self._pull_single_tag(
                image_name=x,
                quiet=quiet,
                stream_logs=stream_logs,
                platform=platform,
                buffer_bytes=buffer_bytes,
            )
            if stream_logs:
                assert result is not None
                return result
            return Image(self.client_config, x)

        images = list(OrderedDict.fromkeys(x))
        if len(images) == 0:
            return (item for item in ()) if stream_logs else []
        if len(images) == 1:
            image, *_ = images
            result = self._pull_single_tag(
                image_name=image,
                quiet=quiet,
                stream_logs=stream_logs,
                platform=platform,
                buffer_bytes=buffer_bytes,
            )
            if stream_logs:
                assert result is not None
                return result
            return [Image(self.client_config, image)]
        else:
            return self._pull_multiple_tags(
                image_names=images,
                quiet=quiet,
                stream_logs=stream_logs,
                platform=platform,
                buffer_bytes=buffer_bytes,
            )

    def _pull_multiple_tags(
        self,
        image_names: builtins.list[str],
        quiet: bool,
        stream_logs: bool = False,
        platform: str | None = None,
        *,
        buffer_bytes: int | None,
    ) -> builtins.list[Image] | ImageStream:
        if stream_logs:
            return self._pull_multiple_tags_stream(
                image_names=image_names,
                quiet=quiet,
                stream_logs=stream_logs,
                platform=platform,
                buffer_bytes=buffer_bytes,
            )
        else:
            pool = ThreadPool(4)
            pool.starmap(
                func=self._pull_single_tag,
                iterable=(
                    (image_name, quiet, stream_logs, platform)
                    for image_name in image_names
                ),
            )
            pool.close()
            pool.join()
            return [Image(self.client_config, image_name) for image_name in image_names]

    def _pull_multiple_tags_stream(
        self,
        image_names: builtins.list[str],
        quiet: bool,
        stream_logs: bool = False,
        platform: str | None = None,
        *,
        buffer_bytes: int | None,
    ) -> MultiImageStream:
        def start(image_name: str) -> MappedProcessStream[tuple[str, bytes]]:
            child = self._pull_single_tag(
                image_name, quiet, True, platform, buffer_bytes=buffer_bytes
            )
            assert child is not None
            return child

        return MultiImageStream(
            image_names,
            start,
            buffer_bytes=buffer_bytes,
        )

    def _pull_single_tag(
        self,
        image_name: str,
        quiet: bool,
        stream_logs: bool = False,
        platform: str | None = None,
        *,
        buffer_bytes: int | None = None,
    ) -> MappedProcessStream[tuple[str, bytes]] | None:
        full_cmd = Command([*self.docker_cmd, "image", "pull"])

        if quiet:
            full_cmd.append("--quiet")

        if platform is not None and platform != "":
            full_cmd.append(f"--platform={platform}")

        full_cmd.append(image_name)

        if stream_logs:
            stream = stream_stdout_and_stderr(full_cmd, buffer_bytes=buffer_bytes)
            return MappedProcessStream(
                stream, ((image_name, line) for _, line in stream)
            )
        else:
            run(full_cmd, capture_stdout=quiet, capture_stderr=quiet)
            return None

    @overload
    def push(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: Literal[False] = False,
    ) -> None: ...

    @overload
    def push(
        self,
        x: str,
        quiet: bool = False,
        *,
        stream_logs: Literal[True],
    ) -> ImageStream: ...

    @overload
    def push(
        self,
        x: str,
        quiet: bool,
        stream_logs: Literal[True],
    ) -> ImageStream: ...

    @overload
    def push(
        self,
        x: Iterable[str],
        quiet: bool = False,
        *,
        stream_logs: Literal[True],
    ) -> ImageStream | None: ...

    @overload
    def push(
        self,
        x: Iterable[str],
        quiet: bool,
        stream_logs: Literal[True],
    ) -> ImageStream | None: ...

    @overload
    def push(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
    ) -> ImageStream | None: ...

    def push(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
    ) -> ImageStream | None:
        """Push image references to a registry after local inspection.

        Alias: `docker.push(...)`

        References are deduplicated and batch-inspected before any transfer,
        including streamed transfers. Blocking transfers start during the call
        with at most four workers. One unique streamed reference starts eagerly;
        several unique references start workers on first consumption, not on
        construction or context entry.

        Nonempty streams own their local CLIs. Exhaust them or use a context
        manager or `close()` on early exit; a bare loop break does not close them.
        Closing cancels local CLIs, not registry changes or daemon-side work. The
        selected image/general stream budget is snapshotted before consuming
        references or inspecting images and retained for queued children.

        Args:
            x: Tag or repository reference, or an iterable deduplicated in
                first-seen order.
            quiet: Don't print anything.
            stream_logs: Return owned `(image_reference, bytes)` progress records
                instead of waiting for all transfers to complete.

        Returns:
            An owned stream of `(image_reference, bytes)` progress records with
            no global ordering across images or stdout/stderr when streaming.
            Otherwise `None` after transfers complete. Empty iterable input
            returns `None` in both modes, without starting a transfer CLI.

        Raises:
            ValueError: `quiet=True` is combined with `stream_logs=True`.
            NoSuchImage: A locally inspected reference does not exist.
            DockerException: Inspection or a push command fails.
            BufferError: A streamed record or retained stderr exceeds the selected
                byte budget. Multi-image failure also cancels sibling transfers.
        """
        if quiet and stream_logs:
            raise ValueError(
                "It's not possible to have stream_logs=True and quiet=True at the same time. "
                "Only one can be activated at a time."
            )

        buffer_bytes = self.client_config.image_stream_buffer_bytes
        if buffer_bytes is None:
            buffer_bytes = self.client_config.stream_buffer_bytes

        images = list(OrderedDict.fromkeys(to_list(x)))

        # this is just to raise a correct exception if the images don't exist
        self.inspect(images)

        if len(images) == 0:
            return None
        elif len(images) == 1:
            image, *_ = images
            return self._push_single_tag(
                tag_or_repo=image,
                quiet=quiet,
                stream_logs=stream_logs,
                buffer_bytes=buffer_bytes,
            )
        else:
            return self._push_multiple_tags(
                tags_or_repos=images,
                quiet=quiet,
                stream_logs=stream_logs,
                buffer_bytes=buffer_bytes,
            )

    def _push_multiple_tags(
        self,
        tags_or_repos: builtins.list[str],
        quiet: bool,
        stream_logs: bool,
        *,
        buffer_bytes: int | None,
    ) -> ImageStream | None:
        if stream_logs:
            return self._push_multiple_tags_stream(
                tags_or_repos=tags_or_repos,
                quiet=quiet,
                stream_logs=stream_logs,
                buffer_bytes=buffer_bytes,
            )
        else:
            pool = ThreadPool(4)
            pool.starmap(
                func=self._push_single_tag,
                iterable=(
                    (tags_or_repo, quiet, stream_logs) for tags_or_repo in tags_or_repos
                ),
            )
            pool.close()
            pool.join()
            return None

    def _push_multiple_tags_stream(
        self,
        tags_or_repos: builtins.list[str],
        quiet: bool,
        stream_logs: bool,
        *,
        buffer_bytes: int | None,
    ) -> MultiImageStream:
        def start(tag_or_repo: str) -> MappedProcessStream[tuple[str, bytes]]:
            child = self._push_single_tag(
                tag_or_repo, quiet, True, buffer_bytes=buffer_bytes
            )
            assert child is not None
            return child

        return MultiImageStream(
            tags_or_repos,
            start,
            buffer_bytes=buffer_bytes,
        )

    def _push_single_tag(
        self,
        tag_or_repo: str,
        quiet: bool,
        stream_logs: bool,
        *,
        buffer_bytes: int | None = None,
    ) -> MappedProcessStream[tuple[str, bytes]] | None:
        full_cmd = Command([*self.docker_cmd, "image", "push"])
        full_cmd.add_flag("--quiet", quiet)
        full_cmd.append(tag_or_repo)
        if stream_logs:
            stream = stream_stdout_and_stderr(full_cmd, buffer_bytes=buffer_bytes)
            return MappedProcessStream(
                stream, ((tag_or_repo, line) for _, line in stream)
            )
        else:
            run(full_cmd, capture_stdout=quiet, capture_stderr=quiet)
            return None

    def remove(
        self,
        x: ValidImage | Iterable[ValidImage],
        force: bool = False,
        prune: bool = True,
    ) -> None:
        """Remove one or more docker images.

        Args:
            x: Single image or iterable of Docker images to remove. You can use tags or
                `pywhales.Image` objects.
            force: Force removal of the image(s).
            prune: Delete untagged parents.

        Raises:
            NoSuchImage: A selected image does not exist.
        """
        images = to_list(x)
        if x == []:
            return
        full_cmd = Command([*self.docker_cmd, "image", "rm"])
        full_cmd.add_flag("--force", force)
        full_cmd.add_flag("--no-prune", not prune)
        full_cmd.extend(images)
        run(full_cmd)

    @overload
    def save(
        self,
        images: ValidImage | Iterable[ValidImage],
        output: None = None,
    ) -> Iterator[bytes]: ...

    @overload
    def save(
        self,
        images: ValidImage | Iterable[ValidImage],
        output: ValidPath,
    ) -> None: ...

    @overload
    def save(
        self,
        images: ValidImage | Iterable[ValidImage],
        output: ValidPath | None,
    ) -> Iterator[bytes] | None: ...

    def save(
        self,
        images: ValidImage | Iterable[ValidImage],
        output: ValidPath | None = None,
    ) -> Iterator[bytes] | None:
        """Save one or more images as a Docker tar archive.

        Alias: `docker.save(...)`

        Inspection occurs eagerly before returning. An output path runs the save
        CLI during the call; omitted output returns a generator whose save CLI
        starts on first iteration. Exhaust or close that generator to leave its
        subprocess context. A configured default deadline reaps the CLI while
        consumption is paused and is reported on resumption. Archive bytes have
        no progress-stream buffer budget; no daemon rollback is guaranteed.

        Args:
            images: Single image or non-empty iterable of images to save.
            output: Path of the tar archive to produce. If `output` is None, a generator
                of bytes is produced. It can be used to stream those bytes elsewhere,
                to another Docker daemon for example.

        Returns:
            An iterator of archive-byte chunks when `output=None`; these are
            archive payload, not `(source, bytes)` progress records. Otherwise
            `None` after the local CLI completes.

        Raises:
            ValueError: No images were supplied.
            NoSuchImage: A requested image does not exist.
            DockerException: Inspection or the save command fails, with lazy save
                failures delivered during iteration.
        """
        image_list = [images] if isinstance(images, (str, Image)) else list(images)
        if len(image_list) == 0:
            raise ValueError("One or more images must be provided")

        # Trigger an exception early if an image doesn't exist.
        self.inspect([str(image) for image in image_list])

        full_cmd = Command([*self.docker_cmd, "image", "save"])
        full_cmd.add_simple_arg("--output", output)
        full_cmd.extend(image_list)
        if output is None:
            # we stream the bytes
            return self._save_generator(full_cmd)
        else:
            run(full_cmd)

    def _save_generator(self, full_cmd: Sequence[object]) -> Iterator[bytes]:
        """Launch an archive export on first iteration and yield stdout bytes.

        Args:
            full_cmd: Complete Docker save arguments to stringify at launch.

        Yields:
            Archive-byte chunks from stdout, with no progress-record framing or
            configured payload budget. Exhaustion or generator closure leaves
            the subprocess context, which closes pipes and waits for the child.

        Raises:
            NoSuchImage: The save CLI reports a missing image.
            DockerException: The save CLI exits unsuccessfully.
        """
        policy = get_execution_policy(full_cmd)
        validate_timeout(policy.default_timeout)
        environment = resolve_environment(
            policy.environment, frozen=policy.frozen_environment
        )
        command = [str(argument) for argument in full_cmd]
        environment = finalize_environment(environment, command)
        deadline = (
            None
            if policy.default_timeout is None
            else monotonic() + policy.default_timeout
        )
        with Popen(
            command,
            stdout=PIPE,
            stderr=PIPE,
            env=environment,
            bufsize=0 if deadline is not None else -1,
        ) as process:
            assert process.stdout is not None
            assert process.stderr is not None
            owner = (
                None
                if deadline is None
                else ArchiveDeadline(
                    process, command, deadline, raw=policy.raw_diagnostics
                )
            )
            if owner is None:
                yield from process.stdout
                exit_code = process.wait(0.1)
            else:
                while chunk := owner.read(process.stdout):
                    yield chunk
                owner.check()
                exit_code = process.wait()
                owner.check()
            if exit_code != 0:
                if owner is not None:
                    os.set_blocking(process.stderr.fileno(), True)
                stderr = process.stderr.read()
                if "No such image" in stderr.decode():
                    raise NoSuchImage(
                        command,
                        exit_code,
                        stderr=stderr,
                        raw_diagnostics=policy.raw_diagnostics,
                    )
                raise DockerException(
                    command,
                    exit_code,
                    stderr=stderr,
                    raw_diagnostics=policy.raw_diagnostics,
                )

    def tag(self, source_image: Image | str, new_tag: str) -> None:
        """Add a tag to a Docker image.

        Alias: `docker.tag(...)`

        Args:
            source_image: The Docker image to tag. You can use a tag to reference it.
            new_tag: The tag to add to the Docker image.

        Raises:
            NoSuchImage: The source image does not exist.
        """
        full_cmd = Command(
            [
                *self.docker_cmd,
                "image",
                "tag",
                str(source_image),
                new_tag,
            ]
        )
        run(full_cmd)

    def _pull_if_necessary(self, image: ValidImage) -> Image:
        if isinstance(image, Image):
            # A docker image object must exist in the daemon to be defined.
            return image
        try:
            return self.inspect(image)
        except DockerException:
            if self.client_config.raw_diagnostics:
                print(f"Unable to find image '{image}' locally")
            return self.pull(image)

    def copy_from(
        self,
        image: ValidImage,
        path_in_image: ValidPath,
        destination: ValidPath,
        pull: str = "missing",
    ) -> None:
        """Copy files out of an image through a temporary container.

        The temporary container is removed on context exit, including copy
        failure. The source image is retained.

        Args:
            image: Source image handle or reference.
            path_in_image: Source path inside the image.
            destination: Local destination path.
            pull: Image acquisition policy forwarded to container creation;
                defaults to `"missing"`.
        """
        with ContainerCLI(self.client_config).create(image, pull=pull) as tmp_container:
            tmp_container.copy_from(path_in_image, destination)

    def copy_to(
        self,
        base_image: ValidImage,
        local_path: ValidPath,
        path_in_image: ValidPath,
        new_tag: str | None = None,
        pull: str = "missing",
    ) -> Image:
        """Copy local files into a temporary container and commit a new image.

        The temporary container is removed on context exit; the original and
        committed images are retained.

        Args:
            base_image: Source image handle or reference.
            local_path: Local source path.
            path_in_image: Destination path inside the new image.
            new_tag: Optional tag for the committed image; `None` omits a tag.
            pull: Image acquisition policy forwarded to container creation;
                defaults to `"missing"`.

        Returns:
            The committed image using the originating client configuration.
        """
        with ContainerCLI(self.client_config).create(
            base_image, pull=pull
        ) as tmp_container:
            tmp_container.copy_to(local_path, path_in_image)
            return tmp_container.commit(tag=new_tag)
