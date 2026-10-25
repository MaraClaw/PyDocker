from __future__ import annotations

import builtins
import json
import warnings
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures.thread import ThreadPoolExecutor
from datetime import datetime
from multiprocessing.pool import ThreadPool
from pathlib import Path
from queue import Empty as EmptyQueue
from queue import Queue
from subprocess import PIPE, Popen
from types import TracebackType
from typing import (
    Any,
    Literal,
    Never,
    Self,
    overload,
    override,
)

import pydocker.components.buildx.cli_wrapper
from pydocker.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ParsingError,
    ReloadableObjectFromJson,
)
from pydocker.components.container.cli_wrapper import (
    ContainerCLI,
    ContainerConfig,
)
from pydocker.components.image.models import (
    ImageGraphDriver,
    ImageInspectResult,
    ImageRootFS,
)
from pydocker.exceptions import DockerException, NoSuchImage
from pydocker.utils import ValidPath, run, stream_stdout_and_stderr, to_list

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
        self.remove(force=True)

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, Any]:
        json_str = run([*self.docker_cmd, "image", "inspect", reference])
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: Mapping[str, Any]) -> ImageInspectResult:
        return ImageInspectResult(**json_object)

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
        return f"pydocker.Image(id='{self.id[:20]}', tags={self.repo_tags})"

    def remove(self, force: bool = False, prune: bool = True) -> None:
        """Remove this Docker image.

        See the [`docker.image.remove`](../sub-commands/image.md#pydocker.components.image.cli_wrapper.ImageCLI.remove) command for
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
        """Saves this Docker image in a tar.

        See the [`docker.image.save`](../sub-commands/image.md#pydocker.components.image.cli_wrapper.ImageCLI.save) command for
        information about the arguments.
        """
        return ImageCLI(self.client_config).save(self, output)

    def tag(self, new_tag: str) -> None:
        """Add a tag to a Docker image.

        See the [`docker.image.tag`](../sub-commands/image.md#pydocker.components.image.cli_wrapper.ImageCLI.tag) command for
        information about the arguments.
        """
        return ImageCLI(self.client_config).tag(self, new_tag)

    def copy_from(
        self, path_in_image: ValidPath, destination: ValidPath, pull: str = "missing"
    ) -> None:
        """Copy a file from a docker image in the local filesystem.

        See the `docker.image.copy_from` command for information about the arguments.
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
        """Copy a file from the local filesystem in a docker image to create a new
        docker image.

        As if you did a dockerfile with a COPY instruction.

        See the `docker.image.copy_to` command for information about the arguments.
        """
        return ImageCLI(self.client_config).copy_to(
            self, local_path, path_in_image, new_tag, pull
        )

    def exists(self) -> bool:
        """Returns `True` if the docker image exists and `False` if it doesn't exists.

        Note that you might have done `docker.image.remove("some_tag")` and the image
        might still exists because PyDocker references images by id, not by tag.

         See the `docker.image.exists` command for information about the arguments.
        """
        return ImageCLI(self.client_config).exists(self.id)


ValidImage = str | Image


class ImageCLI(DockerCLICaller):
    def __init__(self, client_config: ClientConfig) -> None:
        super().__init__(client_config)
        self.build = pydocker.components.buildx.cli_wrapper.BuildxCLI(
            client_config
        ).build

    def legacy_build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str] = {},
        build_args: Mapping[str, str] = {},
        cache: bool = True,
        file: ValidPath | None = None,
        labels: Mapping[str, str] = {},
        network: str | None = None,
        pull: bool = False,
        tags: str | Iterable[str] = (),
        target: str | None = None,
        isolation: str | None = None,
        quiet: bool = True,
    ) -> Image:
        """Build a Docker image with the old Docker builder (meaning not using buildx/buildkit)

        As the name implies this is a legacy building method. Users are strongly encouraged to use
        `docker.build()` instead. The legacy builder will not be available in docker v22.06 and above.

        This function also won't run the legacy builder if the environment variable
        `DOCKER_BUILDKIT` is set to `1` or if you had run previously `docker buildx install` from bash
        or `docker.buildx.install()` from Python.

        Some resources on why moving to buildx/buildkit is necessary:

        * [Proposal: make BuildKit the default builder on Linux](https://github.com/moby/moby/issues/40379)
        * [Deprecated Engine Features: Legacy builder for Linux images](https://github.com/docker/cli/blob/master/docs/deprecated.md#legacy-builder-for-linux-images)

        A `pydocker.Image` is returned, even when using multiple tags.
        That is because it will produce a single image with multiple tags.

        Parameters:
            context_path: The path of the build context. Defaults to the current working directory
            add_hosts: Hosts to add. `add_hosts={"my_host1": "192.168.32.35"}`
            build_args: The build arguments.
                ex `build_args={"PY_VERSION": "3.7.8", "UBUNTU_VERSION": "20.04"}`.
            cache: Whether or not to use the cache, defaults to True
            file: The path of the Dockerfile, defaults to `context_path/Dockerfile`
            labels: Mapping of labels to add to the image.
                `labels={"very-secure": "1", "needs-gpu": "0"}` for example.
            network: which network to use when building the Docker image
            pull: Always attempt to pull a newer version of the image
            tags: Tag or tags to put on the resulting image.
            target: Set the target build stage to build.
            isolation: Specify isolation technology for container (useful for Windows).
            quiet: If you don't want to display the progress bars.

        # Returns
            A `pydocker.Image`
        """
        # to make it easier to write and read tests, the tests of this function
        # are also grouped with the tests of "docker.build()".
        full_cmd = Command([*self.docker_cmd, "build"])
        full_cmd.add_flag("--quiet", quiet)
        full_cmd.add_args_mapping("--add-host", add_hosts, separator=":")
        full_cmd.add_args_mapping("--build-arg", build_args)
        full_cmd.add_args_mapping("--label", labels)
        full_cmd.add_flag("--pull", pull)
        full_cmd.add_simple_arg("--file", file)
        full_cmd.add_simple_arg("--target", target)
        full_cmd.add_simple_arg("--network", network)
        full_cmd.add_flag("--no-cache", not cache)
        full_cmd.add_args_iterable_or_single("--tag", tags)
        full_cmd.add_simple_arg("--isolation", isolation)

        docker_image = ImageCLI(self.client_config)
        full_cmd.append(context_path)
        if quiet:
            image_id = run(full_cmd).splitlines()[-1].strip()
            return docker_image.inspect(image_id)
        else:
            image_id: str | None = None
            for _, line in stream_stdout_and_stderr(full_cmd):
                try:
                    line = line.decode().rstrip()
                except UnicodeDecodeError:
                    line = str(line)
                print(line)
                if "Successfully built" in line:
                    image_id = line.split(" ")[-1]
            assert image_id is not None
            return docker_image.inspect(image_id)

    def history(self) -> Never:
        """Not yet implemented"""
        raise NotImplementedError

    def import_(
        self,
        source: ValidPath,
        tag: str | None = None,
        changes: Iterable[str] = (),
        message: str | None = None,
        platform: str | None = None,
    ) -> Image:
        """Import the contents from a tarball to create a filesystem image

        Alias: `docker.import_(...)`

        Parameters:
            changes: Apply Dockerfile instruction to the created image
            message: Set commit message for imported image
            platform: Set platform if server is multi-platform capable
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
        """Creates a `pydocker.Image` object.

        # Returns
            `pydocker.Image`, or `List[pydocker.Image]` if the input
            was a list of strings.

        # Raises
            `pydocker.exceptions.NoSuchImage` if one of the images does not exists.

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
                    or "localhost/" + normalized(reference) in normalized_aliases
                )
            ):
                raise ParsingError(
                    f"Image inspect identity does not match {reference!r}"
                )
            image = Image(self.client_config, snapshot.id, is_immutable_id=True)
            image._set_inspect_result(snapshot)
            images.append(image)
        return images

    def exists(self, x: str) -> bool:
        """Returns `True` if the image exists. `False` otherwise.

         It's just calling `docker.image.inspect(...)` and verifies that it doesn't throw
         a `pydocker.exceptions.NoSuchImage`.

        # Returns
            A `bool`
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
        """Loads one or multiple Docker image(s) from a tar or an iterator of `bytes`.

        Alias: `docker.load(...)`

        Parameters:
            input: Path or input stream to load the images from.
            quiet: If you don't want to display the progress bars.

        # Returns
            A `List[str]` of loaded image tags for both paths and byte streams.
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
        command = [str(argument) for argument in full_cmd]
        with Popen(command, stdin=PIPE, stdout=PIPE) as process:
            assert process.stdin is not None
            assert process.stdout is not None
            for buffer_bytes in input:
                process.stdin.write(buffer_bytes)
                process.stdin.flush()
            process.stdin.close()
            stdout = process.stdout.read()
            exit_code = process.wait()
        if exit_code != 0:
            raise DockerException(command, exit_code)
        return stdout.decode().splitlines()

    def list(
        self,
        repository_or_tag: str | None = None,
        filters: Iterable[ImageListFilter] | Mapping[str, Any] = (),
        all: bool = False,
    ) -> builtins.list[Image]:
        """Returns the list of Docker images present on the machine.

        Alias: `docker.images()`

        Note that each image may have multiple tags.

        # Returns
            A `List[pydocker.Image]` object.
        """
        if isinstance(filters, Mapping):
            filter_items = filters.items()
            warnings.warn(
                "Passing filters as a mapping is deprecated, replace with an "
                "iterable of tuples instead, as so:\n"
                f"filters={list(filter_items)}",
                DeprecationWarning,
                stacklevel=2,
            )
        else:
            filter_items = filters
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
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))

        if repository_or_tag is not None:
            full_cmd.append(repository_or_tag)

        ids = run(full_cmd).splitlines()
        # the list of tags is bigger than the number of images. We uniquify
        ids = set(ids)

        return [Image(self.client_config, x, is_immutable_id=True) for x in ids]

    def prune(
        self,
        all: bool = False,
        filters: Iterable[ImageListFilter] | Mapping[str, Any] = (),
    ) -> str:
        """Remove unused images

        Parameters:
            all: Remove all unused images, not just dangling ones
            filters: Provide filter values (e.g. `[("until", "<timestamp>")]`)

        Returns:
            The output of the CLI (the layers removed).
        """
        if isinstance(filters, Mapping):
            filter_items = filters.items()
            warnings.warn(
                "Passing filters as a mapping is deprecated, replace with an "
                "iterable of tuples instead, as so:\n"
                f"filters={list(filter_items)}",
                DeprecationWarning,
                stacklevel=2,
            )
        else:
            filter_items = filters
        full_cmd = Command([*self.docker_cmd, "image", "prune", "--force"])
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))
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
        x: str | Iterable[str],
        quiet: bool = False,
        *,
        stream_logs: Literal[True],
        platform: str | None = None,
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def pull(
        self,
        x: str | Iterable[str],
        quiet: bool,
        stream_logs: Literal[True],
        platform: str | None = None,
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def pull(
        self,
        x: str,
        quiet: bool = False,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> Image | Iterable[tuple[str, bytes]]: ...

    @overload
    def pull(
        self,
        x: Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> builtins.list[Image] | Iterable[tuple[str, bytes]]: ...

    def pull(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> Image | builtins.list[Image] | Iterable[tuple[str, bytes]]:
        """Pull one or more docker image(s)

        Alias: `docker.pull(...)`

        Parameters:
            x: Image name(s), can be a string or a list of strings.
                In case a list is passed, multithreading is used to pull
                the images.
            quiet: If you don't want to see the progress bars.
            stream_logs: If `False` this function returns the pulled image(s).
                If `True`, this function returns an `Iterable` of `Tuple[str, bytes]`
                where the first element corresponds to the image name that a pull is being done for.
                The second element is the log statement related to pull progress, as `bytes`.
                You'll need to call `.decode()` if you want the logs as `str`.
            platform: If you want to enforce a platform.
        """
        if quiet and stream_logs:
            raise ValueError(
                "It's not possible to have stream_logs=True and quiet=True at the same time. "
                "Only one can be activated at a time."
            )

        if isinstance(x, str):
            result = self._pull_single_tag(
                image_name=x,
                quiet=quiet,
                stream_logs=stream_logs,
                platform=platform,
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
            )

    def _pull_multiple_tags(
        self,
        image_names: builtins.list[str],
        quiet: bool,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> builtins.list[Image] | Iterable[tuple[str, bytes]]:
        if stream_logs:
            return self._pull_multiple_tags_stream(
                image_names=image_names,
                quiet=quiet,
                stream_logs=stream_logs,
                platform=platform,
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
    ) -> Iterator[tuple[str, bytes]]:
        queue: Queue[tuple[str, bytes] | None] = Queue()

        def _pull_and_queue_logs(image_name: str) -> None:
            try:
                generator = self._pull_single_tag(
                    image_name, quiet, stream_logs, platform
                )
                if generator is not None:
                    for value in generator:
                        queue.put(value)
            finally:
                queue.put(None)  # Signal completion

        with ThreadPoolExecutor(4) as executor:
            futures = [
                executor.submit(_pull_and_queue_logs, image_name)
                for image_name in image_names
            ]

            completed = 0
            while completed < len(image_names):
                try:
                    item = queue.get(timeout=0.1)
                    if item is not None:
                        yield item
                    else:
                        completed += 1
                except EmptyQueue:
                    continue

            [future.result() for future in futures]

    def _pull_single_tag(
        self,
        image_name: str,
        quiet: bool,
        stream_logs: bool = False,
        platform: str | None = None,
    ) -> Iterable[tuple[str, bytes]] | None:
        full_cmd = Command([*self.docker_cmd, "image", "pull"])

        if quiet:
            full_cmd.append("--quiet")

        if platform is not None and platform != "":
            full_cmd.append(f"--platform={platform}")

        full_cmd.append(image_name)

        if stream_logs:
            return (
                (image_name, line) for _, line in stream_stdout_and_stderr(full_cmd)
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
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def push(
        self,
        x: str,
        quiet: bool,
        stream_logs: Literal[True],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def push(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
    ) -> Iterable[tuple[str, bytes]] | None: ...

    def push(
        self,
        x: str | Iterable[str],
        quiet: bool = False,
        stream_logs: bool = False,
    ) -> Iterable[tuple[str, bytes]] | None:
        """Push a tag or a repository to a registry

        Alias: `docker.push(...)`

        Parameters:
            x: Tag(s) or repo(s) to push. Can be a string or an iterable of strings.
                If it's an iterable, PyDocker will push all the images with
                multiple threads.
            quiet: Don't print anything.
            stream_logs: If `False` this function returns None. If `True`, this
                function returns an `Iterable` of `Tuple[str, bytes]` where the first element
                corresponds to the image or repository name that a push is being done for.
                The second element is the log statement related to push progress, as `bytes`.
                You'll need to call `.decode()` if you want the logs as `str`.

        # Raises
            `pydocker.exceptions.NoSuchImage` if one of the images does not exist.
        """
        if quiet and stream_logs:
            raise ValueError(
                "It's not possible to have stream_logs=True and quiet=True at the same time. "
                "Only one can be activated at a time."
            )

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
            )
        else:
            return self._push_multiple_tags(
                tags_or_repos=images,
                quiet=quiet,
                stream_logs=stream_logs,
            )

    def _push_multiple_tags(
        self,
        tags_or_repos: builtins.list[str],
        quiet: bool,
        stream_logs: bool,
    ) -> Iterable[tuple[str, bytes]] | None:
        if stream_logs:
            return self._push_multiple_tags_stream(
                tags_or_repos=tags_or_repos,
                quiet=quiet,
                stream_logs=stream_logs,
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
    ) -> Iterator[tuple[str, bytes]]:
        queue: Queue[tuple[str, bytes] | None] = Queue()

        def _push_and_queue_logs(tag_or_repo: str) -> None:
            try:
                generator = self._push_single_tag(tag_or_repo, quiet, stream_logs)
                if generator is not None:
                    for value in generator:
                        queue.put(value)
            finally:
                queue.put(None)  # Signal completion

        with ThreadPoolExecutor(4) as executor:
            futures = [
                executor.submit(_push_and_queue_logs, tag_or_repo)
                for tag_or_repo in tags_or_repos
            ]

            completed = 0
            while completed < len(tags_or_repos):
                try:
                    item = queue.get(timeout=0.1)
                    if item is not None:
                        yield item
                    else:
                        completed += 1
                except EmptyQueue:
                    continue

            [future.result() for future in futures]

    def _push_single_tag(
        self,
        tag_or_repo: str,
        quiet: bool,
        stream_logs: bool,
    ) -> Iterable[tuple[str, bytes]] | None:
        full_cmd = Command([*self.docker_cmd, "image", "push"])
        full_cmd.add_flag("--quiet", quiet)
        full_cmd.append(tag_or_repo)
        if stream_logs:
            return (
                (tag_or_repo, line) for _, line in stream_stdout_and_stderr(full_cmd)
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

        Parameters:
            x: Single image or iterable of Docker images to remove. You can use tags or
                `pydocker.Image` objects.
            force: Force removal of the image(s).
            prune: Delete untagged parents.

        # Raises
            `pydocker.exceptions.NoSuchImage` if one of the images does not exist.

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
        """Save one or more images to a tar archive. Returns a stream if output is `None`

        Alias: `docker.save(...)`

        Parameters:
            images: Single image or non-empty iterable of images to save.
            output: Path of the tar archive to produce. If `output` is None, a generator
                of bytes is produced. It can be used to stream those bytes elsewhere,
                to another Docker daemon for example.

        # Returns
            `Optional[Iterator[bytes]]`. If output is a path, nothing is returned.

        # Raises
            `pydocker.exceptions.NoSuchImage` if one of the images does not exist.

        # Example

        An example of transfer of an image from a local Docker daemon to a remote Docker
        daemon. We assume that the remote machine has an ssh access:

        ```python
        from pydocker import DockerClient

        local_docker = DockerClient()
        remote_docker = DockerClient(host="ssh://my_user@186.167.32.84")

        image_name = "busybox:1"
        local_docker.pull(image_name)
        bytes_iterator = local_docker.image.save(image_name)

        remote_docker.image.load(bytes_iterator)
        ```

        Of course the best solution is to use a registry to transfer images, but
        it's a cool example nonetheless.
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
        command = [str(argument) for argument in full_cmd]
        with Popen(command, stdout=PIPE, stderr=PIPE) as process:
            assert process.stdout is not None
            assert process.stderr is not None
            yield from process.stdout
            exit_code = process.wait(0.1)
            if exit_code != 0:
                stderr = process.stderr.read()
                if "No such image" in stderr.decode():
                    raise NoSuchImage(command, exit_code, stderr=stderr)
                raise DockerException(command, exit_code, stderr=stderr)

    def tag(self, source_image: Image | str, new_tag: str) -> None:
        """Adds a tag to a Docker image.

        Alias: `docker.tag(...)`

        Parameters:
            source_image: The Docker image to tag. You can use a tag to reference it.
            new_tag: The tag to add to the Docker image.

        # Raises
            `pydocker.exceptions.NoSuchImage` if the image does not exist.
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
            print(f"Unable to find image '{image}' locally")
            return self.pull(image)

    def copy_from(
        self,
        image: ValidImage,
        path_in_image: ValidPath,
        destination: ValidPath,
        pull: str = "missing",
    ) -> None:
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
        with ContainerCLI(self.client_config).create(
            base_image, pull=pull
        ) as tmp_container:
            tmp_container.copy_to(local_path, path_in_image)
            return tmp_container.commit(tag=new_tag)
