from __future__ import annotations

import builtins
import datetime as dt
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import (
    Final,
    Literal,
    Never,
    Self,
    overload,
    override,
)

from pydantic import JsonValue, TypeAdapter

import pywhales.components.image.cli_wrapper
from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObject,
)
from pywhales.components.buildx.imagetools.cli_wrapper import ImagetoolsCLI
from pywhales.components.buildx.models import BuilderInspectResult, BuilderNode
from pywhales.utils import (
    MappedProcessStream,
    ValidPath,
    format_mapping_for_cli,
    run,
    stream_stdout_and_stderr,
    to_list,
)

type BakeResult = dict[str, dict[str, dict[str, JsonValue]]]
type BuildxOptions = Mapping[str, str | Path]
type BuildxProgress = Literal["auto", "none", "plain", "quiet", "rawjson", "tty", False]
type BuildxCall = Literal["build", "check", "outline", "targets"]
type BuildxReportCall = Literal["check", "outline", "targets"]
_BAKE_RESULT_ADAPTER: Final = TypeAdapter(BakeResult)


class Builder(ReloadableObject[BuilderInspectResult]):
    def __init__(
        self,
        client_config: ClientConfig,
        reference: str,
        is_immutable_id: bool = False,
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
        except BaseException as cleanup_error:
            if exc_val is None:
                raise
            raise BaseExceptionGroup(
                "Builder body and cleanup both failed", [exc_val, cleanup_error]
            ) from None

    @override
    def _fetch_and_parse_inspect_result(self, reference: str) -> BuilderInspectResult:
        full_cmd = Command(
            [*self.docker_cmd, "buildx", "ls", "--format", "{{json . }}"]
        )
        inspect_str = run(full_cmd)
        lines = inspect_str.splitlines()
        for line in lines:
            builder_inspect_result = BuilderInspectResult(**json.loads(line))
            if builder_inspect_result.name == reference:
                return builder_inspect_result
        raise ValueError(
            f"Could not find a builder with the name {reference}, this should "
            f"never happen unless you are quickly creating and deleting builders. "
            f"Please open an issue at https://github.com/MaraClaw/PyWhales/issues"
        )

    @property
    def name(self) -> str:
        return self._get_immutable_id()

    @property
    def driver(self) -> str | None:
        return self._get_inspect_result().driver

    @property
    def last_activity(self) -> dt.datetime | None:
        return self._get_inspect_result().last_activity

    @property
    def dynamic(self) -> bool | None:
        return self._get_inspect_result().dynamic

    @property
    def nodes(self) -> list[BuilderNode] | None:
        return self._get_inspect_result().nodes

    @override
    def __repr__(self) -> str:
        return f"pywhales.Builder(name='{self.name}', driver='{self.driver}')"

    def remove(self) -> None:
        """Removes this builder. After this operation the builder cannot be used anymore.

        If you use the builder as a context manager, it will call this function when
        you exit the context manager.

        ```python
        from pywhales import docker

        buildx_builder = docker.buildx.create(use=True)
        with buildx_builder:
            docker.build(".")

        # now the variable buildx_builder is not usable since we're out of the context manager.
        # the .remove() method was called behind the scenes
        # since it was the current builder, 'default' is now the current builder.
        ```

        """
        BuildxCLI(self.client_config).remove(self)


ValidBuilder = str | Builder


class BuildxCLI(DockerCLICaller):
    def __init__(self, client_config: ClientConfig) -> None:
        super().__init__(client_config)
        self.imagetools = ImagetoolsCLI(self.client_config)

    @overload
    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: BuildxProgress = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        stream_logs: Literal[False] = False,
        remote_definition: str | None = None,
        *,
        call: Literal["build"] | None = None,
        jobs: int | str | None = None,
        return_config: Literal[True] = True,
    ) -> BakeResult: ...

    @overload
    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: BuildxProgress = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        *,
        stream_logs: Literal[True],
        remote_definition: str | None = None,
        call: BuildxCall | None = None,
        jobs: int | str | None = None,
        return_config: bool = True,
    ) -> MappedProcessStream[str]: ...

    @overload
    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: BuildxProgress = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        stream_logs: Literal[False] = False,
        remote_definition: str | None = None,
        *,
        call: Literal["build"] | None = None,
        jobs: int | str | None = None,
        return_config: Literal[False],
    ) -> None: ...

    @overload
    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: BuildxProgress = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        stream_logs: Literal[False] = False,
        remote_definition: str | None = None,
        *,
        call: BuildxReportCall,
        jobs: int | str | None = None,
        return_config: bool = True,
    ) -> str: ...

    @overload
    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: BuildxProgress = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        stream_logs: Literal[False] = False,
        remote_definition: str | None = None,
        *,
        call: BuildxCall | None = None,
        jobs: int | str | None = None,
        return_config: Literal[True] = True,
    ) -> BakeResult | str: ...

    @overload
    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: BuildxProgress = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        stream_logs: Literal[False] = False,
        remote_definition: str | None = None,
        *,
        call: BuildxCall | None = None,
        jobs: int | str | None = None,
        return_config: Literal[False],
    ) -> str | None: ...

    @overload
    def bake(
        self,
        targets: str | Sequence[str],
        builder: ValidBuilder | None,
        files: ValidPath | Sequence[ValidPath],
        load: bool,
        cache: bool,
        print: bool,
        progress: BuildxProgress,
        pull: bool,
        push: bool,
        set: Mapping[str, str],
        variables: Mapping[str, str],
        metadata_file: ValidPath | None,
        stream_logs: Literal[True],
        remote_definition: str | None = None,
        *,
        call: BuildxCall | None = None,
        jobs: int | str | None = None,
        return_config: bool = True,
    ) -> MappedProcessStream[str]: ...

    def bake(
        self,
        targets: str | Sequence[str] = (),
        builder: ValidBuilder | None = None,
        files: ValidPath | Sequence[ValidPath] = (),
        load: bool = False,
        cache: bool = True,
        print: bool = False,
        progress: Literal[
            "auto", "none", "plain", "quiet", "rawjson", "tty", False
        ] = "auto",
        pull: bool = False,
        push: bool = False,
        set: Mapping[str, str] = MappingProxyType({}),
        variables: Mapping[str, str] = MappingProxyType({}),
        metadata_file: ValidPath | None = None,
        stream_logs: bool = False,
        remote_definition: str | None = None,
        *,
        call: BuildxCall | None = None,
        jobs: int | str | None = None,
        return_config: bool = True,
    ) -> BakeResult | str | MappedProcessStream[str] | None:
        """Bake is similar to make, it allows you to build things declared in a file.

        For example it allows you to build multiple docker image in parallel.

        The CLI docs is [here](https://github.com/docker/buildx#buildx-bake-options-target)
        and it contains a lot more information.

        Parameters:
            targets: Targets or groups of targets to build.
            builder: The builder to use.
            files: Build definition file(s)
            load: Shorthand for `set=["*.output=type=docker"]`
            cache: Whether to use the cache or not.
            print: Do nothing, just returns the config.
            progress: Set type of progress output (`"auto"`, `"none"`, `"plain"`,
                `"quiet"`, `"rawjson"`, `"tty"`, or `False`).
                Use plain to keep the container output on screen
            pull: Always try to pull the newer version of the image
            push: Shorthand for `set=["*.output=type=registry"]`
            set: A list of overrides in the form `"targetpattern.key=value"`.
            variables: A dict containing the values of the variables defined in the
                hcl file. See <https://github.com/docker/buildx#hcl-variables-and-functions>
            metadata_file: Write build results metadata to the given file
            remote_definition: Remote context in which to find bake files
            call: Frontend method to invoke. `None` and `"build"` build normally;
                `"check"`, `"outline"` and `"targets"` return captured report text.
                Requires a Buildx version supporting `--call`.
            jobs: Target concurrency or execution mode, forwarded as `--jobs`.
                Requires a Buildx version supporting this flag. Zero is forwarded.
            return_config: When `False`, skip the extra configuration query after
                a blocking build and return `None`. Reports and streams do not
                query configuration. Cannot be combined with `print=True`.

        # Returns
            By default, the configuration used for the bake (files merged + override with
            the arguments used in the function). It's the loaded json you would
            obtain by running `docker buildx bake --print --load my_target` if
            your command was `docker buildx bake --load my_target`. Some example here.
            Blocking report calls return captured text. Blocking builds with
            `return_config=False` return `None`. Streaming returns an owned log stream.


        ```python
        from pywhales import docker

        # returns the config used and runs the builds
        config = docker.buildx.bake(["my_target1", "my_target2"], load=True)
        assert config == {
            "target": {
                "my_target1": {
                    "context": "./",
                    "dockerfile": "Dockerfile",
                    "tags": ["pretty_image1:1.0.0"],
                    "target": "out1",
                    "output": ["type=docker"]
                },
                "my_target2": {
                    "context": "./",
                    "dockerfile": "Dockerfile",
                    "tags": ["pretty_image2:1.0.0"],
                    "target": "out2",
                    "output": ["type=docker"]
                }
            }
        }

        # returns the config only, doesn't run the builds
        config = docker.buildx.bake(["my_target1", "my_target2"], load=True, print=True)
        ```
        """
        report = call in ("check", "outline", "targets")
        if print and stream_logs:
            raise ValueError(
                "Getting the config of the bake and streaming "
                "logs at the same time is not possible."
            )
        if print and not return_config:
            raise ValueError("print=True requires return_config=True.")
        if print and report:
            raise ValueError("print=True cannot be combined with a report call.")

        full_cmd = Command([*self.docker_cmd, "buildx", "bake"])

        full_cmd.add_flag("--no-cache", not cache)
        full_cmd.add_simple_arg("--builder", builder)
        full_cmd.add_flag("--load", load)
        full_cmd.add_flag("--pull", pull)
        full_cmd.add_flag("--push", push)
        full_cmd.add_flag("--print", print)
        full_cmd.add_simple_arg("--call", call)
        full_cmd.add_simple_arg("--jobs", jobs)
        if progress != "auto" and isinstance(progress, str):
            full_cmd += ["--progress", progress]
        for file in to_list(files):
            full_cmd.add_simple_arg("--file", file)
        full_cmd.add_args_iterable_or_single("--set", format_mapping_for_cli(set))
        if remote_definition is not None:
            full_cmd.append(remote_definition)
        if metadata_file is not None:
            full_cmd.add_simple_arg("--metadata-file", metadata_file)
        targets = to_list(targets)
        env = dict(variables)
        if print:
            return _BAKE_RESULT_ADAPTER.validate_json(
                run([*full_cmd, *targets], env=env)
            )
        elif stream_logs:
            return stream_buildx_logs([*full_cmd, *targets], env=env)
        else:
            output = run(
                [*full_cmd, *targets], capture_stderr=progress is False, env=env
            )
            if report:
                return output
            if return_config:
                return _BAKE_RESULT_ADAPTER.validate_json(
                    run([*full_cmd, "--print", *targets], env=env)
                )

    @overload
    def build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str] = MappingProxyType({}),
        allow: Sequence[str] = (),
        attest: dict[str, str] | None = None,
        build_args: Mapping[str, str] = MappingProxyType({}),
        build_contexts: Mapping[str, str | ValidPath] = MappingProxyType({}),
        builder: ValidBuilder | None = None,
        cache: bool = True,
        cache_from: str | BuildxOptions | builtins.list[BuildxOptions] | None = None,
        cache_to: str | BuildxOptions | None = None,
        file: ValidPath | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        load: bool = False,
        metadata_file: ValidPath | None = None,
        network: str | None = None,
        output: BuildxOptions = MappingProxyType({}),
        platforms: builtins.list[str] | None = None,
        progress: BuildxProgress = "auto",
        provenance: bool | dict[str, str] | None = None,
        pull: bool = False,
        push: bool = False,
        sbom: bool | dict[str, str] | None = None,
        secrets: str | Sequence[str] = (),
        ssh: str | None = None,
        tags: str | Sequence[str] = (),
        target: str | None = None,
        stream_logs: Literal[False] = False,
        *,
        call: Literal["build"] | None = None,
        no_cache_filter: str | Sequence[str] | None = None,
        annotations: str | Sequence[str] | None = None,
    ) -> pywhales.components.image.cli_wrapper.Image | None: ...

    @overload
    def build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str] = MappingProxyType({}),
        allow: Sequence[str] = (),
        attest: dict[str, str] | None = None,
        build_args: Mapping[str, str] = MappingProxyType({}),
        build_contexts: Mapping[str, str | ValidPath] = MappingProxyType({}),
        builder: ValidBuilder | None = None,
        cache: bool = True,
        cache_from: str | BuildxOptions | builtins.list[BuildxOptions] | None = None,
        cache_to: str | BuildxOptions | None = None,
        file: ValidPath | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        load: bool = False,
        metadata_file: ValidPath | None = None,
        network: str | None = None,
        output: BuildxOptions = MappingProxyType({}),
        platforms: builtins.list[str] | None = None,
        progress: BuildxProgress = "auto",
        provenance: bool | dict[str, str] | None = None,
        pull: bool = False,
        push: bool = False,
        sbom: bool | dict[str, str] | None = None,
        secrets: str | Sequence[str] = (),
        ssh: str | None = None,
        tags: str | Sequence[str] = (),
        target: str | None = None,
        *,
        stream_logs: Literal[True],
        call: BuildxCall | None = None,
        no_cache_filter: str | Sequence[str] | None = None,
        annotations: str | Sequence[str] | None = None,
    ) -> MappedProcessStream[str]: ...

    @overload
    def build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str] = MappingProxyType({}),
        allow: Sequence[str] = (),
        attest: dict[str, str] | None = None,
        build_args: Mapping[str, str] = MappingProxyType({}),
        build_contexts: Mapping[str, str | ValidPath] = MappingProxyType({}),
        builder: ValidBuilder | None = None,
        cache: bool = True,
        cache_from: str | BuildxOptions | builtins.list[BuildxOptions] | None = None,
        cache_to: str | BuildxOptions | None = None,
        file: ValidPath | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        load: bool = False,
        metadata_file: ValidPath | None = None,
        network: str | None = None,
        output: BuildxOptions = MappingProxyType({}),
        platforms: builtins.list[str] | None = None,
        progress: BuildxProgress = "auto",
        provenance: bool | dict[str, str] | None = None,
        pull: bool = False,
        push: bool = False,
        sbom: bool | dict[str, str] | None = None,
        secrets: str | Sequence[str] = (),
        ssh: str | None = None,
        tags: str | Sequence[str] = (),
        target: str | None = None,
        stream_logs: Literal[False] = False,
        *,
        call: BuildxReportCall,
        no_cache_filter: str | Sequence[str] | None = None,
        annotations: str | Sequence[str] | None = None,
    ) -> str: ...

    @overload
    def build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str] = MappingProxyType({}),
        allow: Sequence[str] = (),
        attest: dict[str, str] | None = None,
        build_args: Mapping[str, str] = MappingProxyType({}),
        build_contexts: Mapping[str, str | ValidPath] = MappingProxyType({}),
        builder: ValidBuilder | None = None,
        cache: bool = True,
        cache_from: str | BuildxOptions | builtins.list[BuildxOptions] | None = None,
        cache_to: str | BuildxOptions | None = None,
        file: ValidPath | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        load: bool = False,
        metadata_file: ValidPath | None = None,
        network: str | None = None,
        output: BuildxOptions = MappingProxyType({}),
        platforms: builtins.list[str] | None = None,
        progress: BuildxProgress = "auto",
        provenance: bool | dict[str, str] | None = None,
        pull: bool = False,
        push: bool = False,
        sbom: bool | dict[str, str] | None = None,
        secrets: str | Sequence[str] = (),
        ssh: str | None = None,
        tags: str | Sequence[str] = (),
        target: str | None = None,
        stream_logs: Literal[False] = False,
        *,
        call: BuildxCall | None = None,
        no_cache_filter: str | Sequence[str] | None = None,
        annotations: str | Sequence[str] | None = None,
    ) -> pywhales.components.image.cli_wrapper.Image | str | None: ...

    @overload
    def build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str],
        allow: Sequence[str],
        attest: dict[str, str] | None,
        build_args: Mapping[str, str],
        build_contexts: Mapping[str, str | ValidPath],
        builder: ValidBuilder | None,
        cache: bool,
        cache_from: str | BuildxOptions | builtins.list[BuildxOptions] | None,
        cache_to: str | BuildxOptions | None,
        file: ValidPath | None,
        labels: Mapping[str, str],
        load: bool,
        metadata_file: ValidPath | None,
        network: str | None,
        output: BuildxOptions,
        platforms: builtins.list[str] | None,
        progress: BuildxProgress,
        provenance: bool | dict[str, str] | None,
        pull: bool,
        push: bool,
        sbom: bool | dict[str, str] | None,
        secrets: str | Sequence[str],
        ssh: str | None,
        tags: str | Sequence[str],
        target: str | None,
        stream_logs: Literal[True],
        *,
        call: BuildxCall | None = None,
        no_cache_filter: str | Sequence[str] | None = None,
        annotations: str | Sequence[str] | None = None,
    ) -> MappedProcessStream[str]: ...

    def build(
        self,
        context_path: ValidPath,
        add_hosts: Mapping[str, str] = MappingProxyType({}),
        allow: Sequence[str] = (),
        attest: dict[str, str] | None = None,
        build_args: Mapping[str, str] = MappingProxyType({}),
        build_contexts: Mapping[str, str | ValidPath] = MappingProxyType({}),
        builder: ValidBuilder | None = None,
        cache: bool = True,
        cache_from: str | BuildxOptions | builtins.list[BuildxOptions] | None = None,
        cache_to: str | BuildxOptions | None = None,
        # TODO: cgroup_parent
        file: ValidPath | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        load: bool = False,
        metadata_file: ValidPath | None = None,
        network: str | None = None,
        output: BuildxOptions = MappingProxyType({}),
        platforms: builtins.list[str] | None = None,
        progress: Literal[
            "auto", "none", "plain", "quiet", "rawjson", "tty", False
        ] = "auto",
        provenance: bool | dict[str, str] | None = None,
        pull: bool = False,
        push: bool = False,
        sbom: bool | dict[str, str] | None = None,
        secrets: str | Sequence[str] = (),
        # TODO shm_size
        ssh: str | None = None,
        tags: str | Sequence[str] = (),
        target: str | None = None,
        # TODO: ulimit
        stream_logs: bool = False,
        *,
        call: BuildxCall | None = None,
        no_cache_filter: str | Sequence[str] | None = None,
        annotations: str | Sequence[str] | None = None,
    ) -> (
        pywhales.components.image.cli_wrapper.Image
        | str
        | MappedProcessStream[str]
        | None
    ):
        """Build a Docker image with builkit as backend.

        Alias: `docker.build(...)`

        A `pywhales.Image` is returned, even when using multiple tags.
        That is because it will produce a single image with multiple tags.
        If no image is loaded into the Docker daemon (if `push=True` for ex),
        then `None` is returned.

        Parameters:
            context_path: The path of the build context.
            add_hosts: Hosts to add. `add_hosts={"my_host1": "192.168.32.35"}`
            allow: List of extra privileges.
                Eg `allow=["network.host", "security.insecure"]`
            attest: Attestation parameters. Eg `attest={"type": "sbom", "generator": "my_image"}`
            build_args: The build arguments.
                ex `build_args={"PY_VERSION": "3.7.8", "UBUNTU_VERSION": "20.04"}`.
            build_contexts: Additional build contexts.
                `build_contexts={[name]: [value], ...}`
                Supports local directories, git repositories, HTTP URL to a tarball, a docker
                image defined with a `docker-image://` prefix, and the `oci-layout://` protocol.
                ex `build_contexts={"project2": "../path/to/project2/src", "qumu-src": "https://github.com/qemu/qemu.git"}`.
            builder: Specify which builder to use.
            cache: Whether or not to use the cache
            cache_from: Loads the cache
                (if needed) from a registry `cache_from="user/app:cache"`  or
                a directory on the client `cache_from="type=local,src=path/to/dir"`.
                It's also possible to use a dict or list of dict form for this
                argument. e.g.
                `cache_from=dict(type="local", src="path/to/dir")`
            cache_to: Sends the resulting
                docker cache either to a registry `cache_to="user/app:cache"`,
                or to a local directory `cache_to="type=local,dest=path/to/dir"`.
                It's also possible to use a dict form for this argument. e.g.
                `cache_to=dict(type="local", dest="path/to/dir", mode="max")`
                Supported external cache backends with the Docker driver depend
                on the containerd image store being enabled. For GitHub Actions
                cache, `docker-container` is the conservative recommendation.
            file: The path of the Dockerfile
            labels: Dict of labels to add to the image.
                `labels={"very-secure": "1", "needs-gpu": "0"}` for example.
            load: Shortcut for `output=dict(type="docker")` If `True`,
                `docker.buildx.build` will return a `pywhales.Image`.
            network: which network to use when building the Docker image
            output: Output destination
                (format: `output={"type": "local", "dest": "path"}`
                Possible output types are
                `["local", "tar", "oci", "docker", "image", "registry"]`.
                See [this link](https://github.com/docker/buildx#-o---outputpath-typetypekeyvalue)
                for more details about each exporter.
            platforms: List of target platforms when building the image. Ex:
                `platforms=["linux/amd64", "linux/arm64"]`
            progress: Set type of progress output (`"auto"`, `"none"`, `"plain"`,
                `"quiet"`, `"rawjson"`, `"tty"`, or `False`).
                Use plain to keep the container output on screen
            provenance: Shortand for `attest={"type": "provenance"}`.
                Eg `provenance=True` or `provenance=dict(mode="max")`. `provenance=False` might be needed
                if you are having the
                issue [Default image output from buildx v0.10 cannot run on Google Cloud Run or AWS Lambda](https://github.com/docker/buildx/issues/1533)
            pull: Always attempt to pull a newer version of the image
            push: Shorthand for `output=dict(type="registry")`.
            sbom: Shorthand for `attest={"type": "sbom"}`. Eg `sbom=True`.
            secrets: One or more secrets passed as string(s). For example
                `secrets="id=aws,src=/home/my_user/.aws/credentials"`
            ssh: SSH agent socket or keys to expose to the build
                (format is `default|<id>[=<socket>|<key>[,<key>]]` as a string)
            tags: Tag or tags to put on the resulting image.
            target: Set the target build stage to build.
            stream_logs: If `True` this function will return an iterator of strings.
                You can then read the logs as they arrive.
            metadata_file: Path where build metadata should be written. Equivalent
                to the CLI flag `--metadata-file` and only used when provided.
            call: Frontend method to invoke. `None` and `"build"` build normally;
                `"check"`, `"outline"` and `"targets"` return captured report text
                without image recovery. Requires Buildx support for `--call`.
            no_cache_filter: Stage name(s) to rebuild without cache, passed as
                repeated `--no-cache-filter` flags.
            annotations: Image annotation(s), passed as repeated `--annotation`
                flags. Requires a Buildx version supporting these flags.

        # Returns
            A `pywhales.Image` if a Docker image is loaded
            in the daemon after the build (the default behavior when
            calling `docker.build(...)`). Otherwise, `None`. Blocking report calls
            return captured text instead. Streaming returns an owned log stream.
        """
        tags = to_list(tags)
        full_cmd = Command([*self.docker_cmd, "buildx", "build"])

        if progress != "auto" and isinstance(progress, str):
            full_cmd += ["--progress", progress]

        full_cmd.add_args_iterable_or_single(
            "--add-host", format_mapping_for_cli(add_hosts, separator=":")
        )
        full_cmd.add_args_iterable_or_single("--allow", allow)
        if isinstance(attest, dict):
            full_cmd.add_simple_arg("--attest", format_dict_for_buildx(attest))
        full_cmd.add_args_iterable_or_single(
            "--build-arg", format_mapping_for_cli(build_args)
        )
        full_cmd.add_args_iterable_or_single(
            "--build-context", format_mapping_for_cli(build_contexts)
        )
        full_cmd.add_simple_arg("--builder", builder)
        full_cmd.add_args_iterable_or_single("--label", format_mapping_for_cli(labels))

        full_cmd.add_simple_arg("--ssh", ssh)

        if isinstance(provenance, bool):
            full_cmd.append(f"--provenance={str(provenance).lower()}")
        elif isinstance(provenance, dict):
            full_cmd.add_simple_arg("--provenance", format_dict_for_buildx(provenance))
        full_cmd.add_flag("--pull", pull)
        full_cmd.add_flag("--push", push)
        if isinstance(sbom, bool):
            full_cmd.append(f"--sbom={str(sbom).lower()}")
        elif isinstance(sbom, dict):
            full_cmd.add_simple_arg("--sbom", format_dict_for_buildx(sbom))
        full_cmd.add_flag("--load", load)
        full_cmd.add_simple_arg("--metadata-file", metadata_file)
        full_cmd.add_simple_arg("--file", file)
        full_cmd.add_simple_arg("--target", target)
        if isinstance(cache_from, list):
            for item in cache_from:
                full_cmd.add_simple_arg("--cache-from", format_dict_for_buildx(item))
        elif isinstance(cache_from, Mapping):
            full_cmd.add_simple_arg("--cache-from", format_dict_for_buildx(cache_from))
        else:
            full_cmd.add_simple_arg("--cache-from", cache_from)
        if isinstance(cache_to, Mapping):
            full_cmd.add_simple_arg("--cache-to", format_dict_for_buildx(cache_to))
        else:
            full_cmd.add_simple_arg("--cache-to", cache_to)
        full_cmd.add_args_iterable_or_single("--secret", to_list(secrets))
        if output != {}:
            full_cmd += ["--output", format_dict_for_buildx(output)]
        if platforms is not None:
            full_cmd += ["--platform", ",".join(platforms)]
        full_cmd.add_simple_arg("--network", network)
        full_cmd.add_flag("--no-cache", not cache)
        full_cmd.add_args_iterable_or_single("--tag", tags)
        full_cmd.add_simple_arg("--call", call)
        if no_cache_filter is not None:
            full_cmd.add_args_iterable_or_single("--no-cache-filter", no_cache_filter)
        if annotations is not None:
            full_cmd.add_args_iterable_or_single("--annotation", annotations)

        if stream_logs:
            if progress in (False, "tty", "none", "quiet"):
                raise ValueError(
                    "You want to stream logs, but it's not possible if a tty is used "
                    "as 'progress'. It's also not possible if 'progress' is False, "
                    "'none' or 'quiet', since those modes emit no logs. "
                    "Make sure the function arguments of 'docker.build' are "
                    "coherent."
                )

            full_cmd.append(context_path)
            return stream_buildx_logs(full_cmd)

        if call in ("check", "outline", "targets"):
            full_cmd.append(context_path)
            return run(full_cmd, capture_stderr=progress is False)

        will_load_image = self._build_will_load_image(builder, push, load, output)
        driver = None
        if will_load_image is not False:
            driver = self.inspect(builder).driver
            if will_load_image is None:
                will_load_image = driver == "docker"
        # very special_case, must be fixed https://github.com/docker/buildx/issues/420
        if will_load_image and not tags and driver == "docker-container":
            # we have no way of fetching the image because iidfile is wrong in this case.
            will_load_image = False

        if not will_load_image:
            full_cmd.append(context_path)
            run(full_cmd, capture_stderr=progress is False)
            return

        docker_image = pywhales.components.image.cli_wrapper.ImageCLI(
            self.client_config
        )
        if driver != "docker":
            full_cmd.append(context_path)
            run(full_cmd, capture_stderr=progress is False)
            return docker_image.inspect(tags[0])
        else:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_dir = Path(tmp_dir)
                iidfile = tmp_dir / "id_file.txt"
                full_cmd.add_simple_arg("--iidfile", iidfile)
                full_cmd.append(context_path)
                run(full_cmd, capture_stderr=progress is False)
                image_id = iidfile.read_text()
                return docker_image.inspect(image_id)

    def _build_will_load_image(
        self,
        builder: ValidBuilder | None,
        push: bool,
        load: bool,
        output: BuildxOptions,
    ) -> bool | None:
        if load:
            return True
        if push:
            return False
        if output != {}:
            return output.get("type") == "docker" and "dest" not in output

        return None

    def create(
        self,
        context_or_endpoint: str | None = None,
        bootstrap: bool = False,
        buildkitd_flags: str | None = None,
        config: ValidPath | None = None,
        platforms: builtins.list[str] | None = None,
        driver: str | None = None,
        driver_options: Mapping[str, str] = MappingProxyType({}),
        name: str | None = None,
        use: bool = False,
        append: bool = False,
    ) -> Builder:
        """Create a new builder instance

        Parameters:
            context_or_endpoint:
            bootstrap: Boot builder after creation
            buildkitd_flags: Flags for buildkitd daemon
            config: BuildKit config file
            platforms: Comma-separated list of platforms of the form OS/architecture/variant. Ex:
                `platforms=["linux/amd64", "linux/arm64"]`
            driver: Driver to use (available: [kubernetes docker docker-container])
            driver_options: Options for the driver.
                e.g `driver_options=dict(network="host")`
            name: Builder instance name
            use: Set the current builder instance to this builder
            append: Append a node to the current builder instance, in this case
                `name` must be provided.

        # Returns
            A `pywhales.Builder` object.
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "create"])

        full_cmd.add_flag("--bootstrap", bootstrap)
        full_cmd.add_simple_arg("--buildkitd-flags", buildkitd_flags)
        full_cmd.add_simple_arg("--config", config)
        full_cmd.add_flag("--append", append)
        if platforms is not None:
            full_cmd += ["--platform", ",".join(platforms)]
        full_cmd.add_simple_arg("--driver", driver)
        if driver_options != {}:
            full_cmd.add_simple_arg(
                "--driver-opt", format_dict_for_buildx(driver_options)
            )
        full_cmd.add_simple_arg("--name", name)
        full_cmd.add_flag("--use", use)

        if context_or_endpoint is not None:
            full_cmd.append(context_or_endpoint)
        return Builder(self.client_config, run(full_cmd))

    def disk_usage(self) -> Never:
        """Not yet implemented"""
        raise NotImplementedError

    def inspect(
        self,
        x: ValidBuilder | None = None,
        bootstrap: bool = False,
    ) -> Builder:
        """Returns a builder instance from the name.

        Parameters:
            x: If `None` (the default), returns the current builder. If a string is provided,
                the builder that has this name is returned.
            bootstrap: If set to True, ensure builder has booted before inspecting.

        # Returns
            A `pywhales.Builder` object.
        """
        # Sadly, docker buildx inspect has no json support,
        # so, it's ugly, but we grab the name from the first line and
        # then filter "docker buildx ls".
        full_cmd = Command([*self.docker_cmd, "buildx", "inspect"])
        if x is not None:
            full_cmd.append(x)
        full_cmd.add_flag("--bootstrap", bootstrap)
        output = run(full_cmd)
        name = output.splitlines()[0].split(":")[1].strip()
        for builder in self.list():
            if builder.name == name:
                return builder
        raise ValueError(
            f"Could not find a builder with the name {name}, this should "
            f"never happen unless you are quickly creating and deleting builders. "
            f"Please open an issue at https://github.com/MaraClaw/PyWhales/issues."
        )

    def list(self) -> builtins.list[Builder]:
        """Returns the list of `pywhales.Builder` available."""
        full_cmd = Command(
            [*self.docker_cmd, "buildx", "ls", "--format", "{{json . }}"]
        )
        output = run(full_cmd)
        lines = output.splitlines()
        lines = set(lines)

        inpect_results = [BuilderInspectResult(**json.loads(line)) for line in lines]
        result = []
        for inspect_result in inpect_results:
            assert inspect_result.name is not None
            builder = Builder(
                self.client_config, inspect_result.name, is_immutable_id=True
            )
            builder._set_inspect_result(inspect_result)
            result.append(builder)
        return result

    @overload
    def prune(
        self,
        all: bool = False,
        filters: Mapping[str, str] = MappingProxyType({}),
        *,
        stream_logs: Literal[True],
    ) -> MappedProcessStream[str]: ...

    @overload
    def prune(
        self,
        all: bool = False,
        filters: Mapping[str, str] = MappingProxyType({}),
        stream_logs: Literal[False] = False,
    ) -> None: ...

    @overload
    def prune(
        self,
        all: bool = False,
        filters: Mapping[str, str] = MappingProxyType({}),
        stream_logs: bool = False,
    ) -> MappedProcessStream[str] | None: ...

    def prune(
        self,
        all: bool = False,
        filters: Mapping[str, str] = MappingProxyType({}),
        stream_logs: bool = False,
    ) -> MappedProcessStream[str] | None:
        """Remove build cache on the current builder.

        Parameters:
            all: Remove all cache, not just dangling layers
            filters: Filters to use, for example `filters=dict(until="24h")`
            stream_logs: If `True` this function will return an iterator of strings.
                You can then read the logs as they arrive. If `False` (the default value), then
                the function returns `None`, but when it returns, then the prune operation has already been
                done.
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "prune", "--force"])
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable_or_single(
            "--filter", format_mapping_for_cli(filters)
        )
        if stream_logs:
            return stream_buildx_logs(full_cmd)
        run(full_cmd)

    def remove(self, builder: Builder | str) -> None:
        """Remove a builder

        Parameters:
            builder: The builder to remove
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "rm"])

        full_cmd.append(builder)
        run(full_cmd)

    def stop(self, builder: ValidBuilder | None) -> None:
        """Stop the builder instance

        Parameters:
            builder: The builder to stop. If `None` (the default value),
                the current builder is stopped.
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "stop"])
        if builder is not None:
            full_cmd.append(builder)
        run(full_cmd)

    def use(
        self, builder: Builder | str, default: bool = False, global_: bool = False
    ) -> None:
        """Set the current builder instance

        Parameters:
            builder: The builder to use
            default: Set builder as default for the current context
            global_: Builder will be used even when changing contexts
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "use"])

        full_cmd.add_flag("--default", default)
        full_cmd.add_flag("--global", global_)
        full_cmd.append(builder)
        run(full_cmd)

    def version(self) -> str:
        """Returns the docker buildx version as a string.

        ```python
        from pywhales import docker

        version = docker.buildx.version()
        print(version)
        # "github.com/docker/buildx v0.4.2 fb7b670b764764dc4716df3eba07ffdae4cc47b2"
        ```
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "version"])
        return run(full_cmd)

    def is_installed(self) -> bool:
        """Returns `True` if docker buildx is installed and working.

        If it's not installed, head
        to [the installation page](https://github.com/docker/buildx#installing)
        and follow the instructions.
        """
        full_cmd = Command([*self.docker_cmd, "buildx", "--help"])
        help_output = run(full_cmd)
        return "buildx" in help_output


def format_dict_for_buildx(options: BuildxOptions) -> str:
    return ",".join(format_mapping_for_cli(options, separator="="))


def stream_buildx_logs(
    full_cmd: Sequence[object], env: Mapping[str, str] | None = None
) -> MappedProcessStream[str]:
    """Decode Buildx output while retaining close and context ownership."""
    stream = stream_stdout_and_stderr(full_cmd, env=env)
    return MappedProcessStream(
        stream, (value.decode(errors="replace") for _, value in stream)
    )
