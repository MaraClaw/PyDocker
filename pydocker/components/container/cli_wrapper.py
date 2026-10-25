from __future__ import annotations

import builtins
import json
import shlex
import textwrap
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import (
    Any,
    Literal,
    Self,
    Unpack,
    overload,
    override,
)

import pydantic
from typing_extensions import TypedDict

import pydocker.components.image.cli_wrapper
import pydocker.components.network.cli_wrapper
import pydocker.components.pod.cli_wrapper
import pydocker.components.volume.cli_wrapper
from pydocker.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ParsingError,
    ReloadableObjectFromJson,
)
from pydocker.components.container.models import (
    ContainerConfig,
    ContainerGraphDriver,
    ContainerHostConfig,
    ContainerInspectResult,
    ContainerState,
    Mount,
    NetworkSettings,
)
from pydocker.components.container.top import docker_top_fields
from pydocker.components.top import ContainerTopResult, parse_table
from pydocker.exceptions import NoSuchContainer
from pydocker.utils import (
    ProcessStream,
    ValidPath,
    ValidPortMapping,
    custom_parse_object_as,
    format_cpuset,
    format_port_arg,
    format_signal_arg,
    format_time_arg,
    run,
    stream_stdout_and_stderr,
    to_list,
    to_seconds,
)

type ContainerListFilter = (
    tuple[Literal["id"], str]
    | tuple[Literal["name"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["label!"], str]
    | tuple[Literal["exited"], int]
    | tuple[
        Literal["status"],
        Literal[
            "created", "restarting", "running", "removing", "paused", "exited", "dead"
        ],
    ]
    | tuple[Literal["ancestor"], str]
    | tuple[Literal["before"], str]
    | tuple[Literal["since"], str]
    | tuple[Literal["volume"], str]
    | tuple[Literal["network"], str]
    | tuple[Literal["pod"], str]
    | tuple[Literal["publish"], str]
    | tuple[Literal["expose"], str]
    | tuple[Literal["health"], Literal["starting", "healthy", "unhealthy", "none"]]
    | tuple[Literal["isolation"], Literal["default", "process", "hyperv"]]
    | tuple[Literal["is-task"], str]
    | tuple[Literal["until"], str]
)


class ExecuteArgs(TypedDict, total=False, closed=True):
    envs: Mapping[str, str]
    env_files: ValidPath | Iterable[ValidPath]
    interactive: bool
    privileged: bool
    tty: bool
    user: str | None
    workdir: ValidPath | None
    detach_keys: str | None


class Container(ReloadableObjectFromJson[ContainerInspectResult]):
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
        try:
            host_config = self.host_config
            state = self.state
            assert host_config is not None
            assert state is not None
            autoremove = host_config.auto_remove
            if state.running is True:
                self.stop()
            if autoremove is not True:
                self.remove(volumes=True)
        except NoSuchContainer:
            return
        except BaseException as cleanup_error:
            if exc_val is None:
                raise
            raise BaseExceptionGroup(
                "Container body and cleanup both failed", [exc_val, cleanup_error]
            ) from None

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, Any]:
        json_str = run([*self.docker_cmd, "container", "inspect", reference])
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: dict[str, Any]) -> ContainerInspectResult:
        return ContainerInspectResult(**json_object)

    # ----------------------------------------------------------------
    # attributes taken from the json inspect result
    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def created(self) -> datetime | None:
        return self._get_inspect_result().created

    @property
    def path(self) -> str | None:
        return self._get_inspect_result().path

    @property
    def args(self) -> list[str] | None:
        return self._get_inspect_result().args

    @property
    def state(self) -> ContainerState | None:
        return self._get_inspect_result().state

    @property
    def image(self) -> str | None:
        return self._get_inspect_result().image

    @property
    def pod(self) -> str | None:
        return self._get_inspect_result().pod

    @property
    def resolv_conf_path(self) -> str | None:
        return self._get_inspect_result().resolv_conf_path

    @property
    def hostname_path(self) -> Path | None:
        return self._get_inspect_result().hostname_path

    @property
    def hosts_path(self) -> Path | None:
        return self._get_inspect_result().hosts_path

    @property
    def log_path(self) -> Path | None:
        return self._get_inspect_result().log_path

    @property
    def node(self) -> object:
        return self._get_inspect_result().node

    @property
    def name(self) -> str | None:
        name = self._get_inspect_result().name
        return name.removeprefix("/") if name is not None else None

    @property
    def restart_count(self) -> int | None:
        return self._get_inspect_result().restart_count

    @property
    def driver(self) -> str | None:
        return self._get_inspect_result().driver

    @property
    def platform(self) -> str | None:
        return self._get_inspect_result().platform

    @property
    def mount_label(self) -> str | None:
        return self._get_inspect_result().mount_label

    @property
    def process_label(self) -> str | None:
        return self._get_inspect_result().process_label

    @property
    def app_armor_profile(self) -> str | None:
        return self._get_inspect_result().app_armor_profile

    @property
    def exec_ids(self) -> list[str] | None:
        return self._get_inspect_result().exec_ids

    @property
    def host_config(self) -> ContainerHostConfig | None:
        return self._get_inspect_result().host_config

    @property
    def graph_driver(self) -> ContainerGraphDriver | None:
        return self._get_inspect_result().graph_driver

    @property
    def size_rw(self) -> int | None:
        return self._get_inspect_result().size_rw

    @property
    def size_root_fs(self) -> int | None:
        return self._get_inspect_result().size_root_fs

    @property
    def mounts(self) -> list[Mount] | None:
        return self._get_inspect_result().mounts

    @property
    def config(self) -> ContainerConfig | None:
        return self._get_inspect_result().config

    @property
    def network_settings(self) -> NetworkSettings | None:
        return self._get_inspect_result().network_settings

    @property
    def namespace(self) -> str | None:
        return self._get_inspect_result().namespace

    @property
    def is_infra(self) -> bool | None:
        return self._get_inspect_result().is_infra

    @override
    def __repr__(self) -> str:
        return f"pydocker.Container(id='{self.id[:12]}', name='{self.name}')"

    # --------------------------------------------------------------------
    # public methods

    def attach(
        self,
        detach_keys: str | None = None,
        stdin: bool = True,
        sig_proxy: bool = True,
    ) -> None:
        """Attach local standard input, output, and error streams to a running container.

        Alias: `docker.attach(...)`

        See the [`docker.container.attach`](../sub-commands/container.md#attach) command for
        information about the arguments.
        """
        ContainerCLI(self.client_config).attach(self, detach_keys, not stdin, sig_proxy)

    def commit(
        self,
        tag: str | None = None,
        author: str | None = None,
        message: str | None = None,
        pause: bool = True,
    ) -> pydocker.components.image.cli_wrapper.Image:
        """Create a new image from the container's changes.

        Alias: `docker.commit(...)`

        See the [`docker.container.commit`](../sub-commands/container.md) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).commit(
            self, tag, author, message, pause
        )

    def copy_from(self, container_path: ValidPath, local_path: ValidPath) -> None:
        return ContainerCLI(self.client_config).copy((self, container_path), local_path)

    def copy_to(self, local_path: ValidPath, container_path: ValidPath) -> None:
        return ContainerCLI(self.client_config).copy(local_path, (self, container_path))

    def diff(self) -> dict[str, str]:
        """Returns the diff of this container filesystem.

        See the [`docker.container.diff`](../sub-commands/container.md) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).diff(self)

    @overload
    def execute(
        self,
        command: Sequence[str],
        *,
        detach: Literal[True],
        stream: bool = False,
        **kwargs: Unpack[ExecuteArgs],
    ) -> None: ...

    @overload
    def execute(
        self,
        command: Sequence[str],
        *,
        detach: Literal[False] = False,
        stream: Literal[True],
        **kwargs: Unpack[ExecuteArgs],
    ) -> ProcessStream: ...

    @overload
    def execute(
        self,
        command: Sequence[str],
        *,
        detach: Literal[False] = False,
        stream: Literal[False] = False,
        **kwargs: Unpack[ExecuteArgs],
    ) -> str: ...

    @overload
    def execute(
        self,
        command: Sequence[str],
        detach: bool = False,
        envs: Mapping[str, str] = MappingProxyType({}),
        env_files: ValidPath | Iterable[ValidPath] = (),
        interactive: bool = False,
        privileged: bool = False,
        tty: bool = False,
        user: str | None = None,
        workdir: ValidPath | None = None,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> str | ProcessStream | None: ...

    def execute(
        self,
        command: Sequence[str],
        detach: bool = False,
        envs: Mapping[str, str] = MappingProxyType({}),
        env_files: ValidPath | Iterable[ValidPath] = (),
        interactive: bool = False,
        privileged: bool = False,
        tty: bool = False,
        user: str | None = None,
        workdir: ValidPath | None = None,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> str | ProcessStream | None:
        """Execute a command in this container

        See the [`docker.container.execute`](../sub-commands/container.md#execute)
        command for information about the arguments.
        """
        return ContainerCLI(self.client_config).execute(
            self,
            command,
            detach,
            envs,
            env_files,
            interactive,
            privileged,
            tty,
            user,
            workdir,
            stream,
            detach_keys,
        )

    def exists(self) -> bool:
        """Returns `True` if the docker container exists and `False` if it doesn't exists.

        If it doesn't exists, it most likely mean that it was removed.

         See the `docker.container.exists` command for information about the arguments.
        """
        return ContainerCLI(self.client_config).exists(self.id)

    def export(self, output: ValidPath) -> None:
        """Export this container filesystem.

        See the [`docker.container.export`](../sub-commands/container.md) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).export(self, output)

    def init(self) -> None:
        """Initialize this container.

        See the [`docker.container.init`](../sub-commands/container.md#init) command.
        """
        return ContainerCLI(self.client_config).init(self)

    def kill(self, signal: int | str | None = None) -> None:
        """Kill this container

        See the [`docker.container.kill`](../sub-commands/container.md#kill) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).kill(self, signal)

    def logs(
        self,
        *,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        until: datetime | timedelta | None = None,
    ) -> str:
        """Returns the logs of the container

        See the [`docker.container.logs`](../sub-commands/container.md#logs) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).logs(
            self,
            details=details,
            since=since,
            tail=tail,
            timestamps=timestamps,
            until=until,
        )

    def pause(self) -> None:
        """Pause this container.

        See the [`docker.container.pause`](../sub-commands/container.md#pause) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).pause(self)

    def unpause(self) -> None:
        """Unpause the container

        See the [`docker.container.unpause`](../sub-commands/container.md#unpause) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).unpause(self)

    def rename(self, new_name: str) -> None:
        """Rename this container

        See the [`docker.container.rename`](../sub-commands/container.md#rename) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).rename(self, new_name)

    def restart(self, time: int | timedelta | None = None) -> None:
        """Restarts this container.

        See the [`docker.container.restart`](../sub-commands/container.md#restart) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).restart(self, time)

    def remove(self, force: bool = False, volumes: bool = False) -> None:
        """Remove this container.

        See the [`docker.container.remove`](../sub-commands/container.md#remove) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).remove(
            self, force=force, volumes=volumes
        )

    @overload
    def start(
        self,
        attach: Literal[False] = False,
        interactive: bool = False,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> None: ...

    @overload
    def start(
        self,
        attach: Literal[True],
        interactive: bool = False,
        stream: Literal[False] = False,
        detach_keys: str | None = None,
    ) -> str: ...

    @overload
    def start(
        self,
        attach: Literal[True],
        interactive: bool = False,
        *,
        stream: Literal[True],
        detach_keys: str | None = None,
    ) -> ProcessStream: ...

    @overload
    def start(
        self,
        attach: bool = False,
        interactive: bool = False,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> str | ProcessStream | None: ...

    def start(
        self,
        attach: bool = False,
        interactive: bool = False,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> str | ProcessStream | None:
        """Starts this container.

        See the [`docker.container.start`](../sub-commands/container.md#start) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).start(
            self, attach, interactive, stream, detach_keys
        )

    def stop(self, time: int | timedelta | None = None) -> None:
        """Stops this container.

        See the [`docker.container.stop`](../sub-commands/container.md#stop) command for
        information about the arguments.
        """
        return ContainerCLI(self.client_config).stop(self, time)


ContainerPath = tuple[Container | str, ValidPath]
ValidContainer = Container | str


class RunArgs(TypedDict, total=False, closed=True):
    add_hosts: Iterable[tuple[str, str]]
    blkio_weight: int | None
    blkio_weight_device: Iterable[str]
    cap_add: Iterable[str]
    cap_drop: Iterable[str]
    cgroup_parent: str | None
    cgroupns: str | None
    cidfile: ValidPath | None
    cpu_period: int | None
    cpu_quota: int | None
    cpu_rt_period: int | None
    cpu_rt_runtime: int | None
    cpu_shares: int | None
    cpus: float | None
    cpuset_cpus: list[int] | str | None
    cpuset_mems: list[int] | str | None
    devices: Iterable[str]
    device_cgroup_rules: Iterable[str]
    device_read_bps: Iterable[str]
    device_read_iops: Iterable[str]
    device_write_bps: Iterable[str]
    device_write_iops: Iterable[str]
    content_trust: bool
    dns: Iterable[str]
    dns_options: Iterable[str]
    dns_search: Iterable[str]
    domainname: str | None
    entrypoint: str | None
    envs: Mapping[str, str]
    env_files: ValidPath | Iterable[ValidPath]
    env_host: bool
    expose: int | Iterable[int]
    gpus: int | str | None
    groups_add: Iterable[str]
    healthcheck: bool
    health_cmd: str | None
    health_interval: int | timedelta | None
    health_retries: int | None
    health_start_period: int | timedelta | None
    health_timeout: int | timedelta | None
    hostname: str | None
    init: bool
    interactive: bool
    ip: str | None
    ip6: str | None
    ipc: str | None
    isolation: str | None
    kernel_memory: int | str | None
    labels: Mapping[str, str]
    label_files: Iterable[ValidPath]
    link: Iterable[ValidContainer]
    link_local_ip: Iterable[str]
    log_driver: str | None
    log_options: Iterable[str]
    mac_address: str | None
    memory: int | str | None
    memory_reservation: int | str | None
    memory_swap: int | str | None
    memory_swappiness: int | None
    mounts: Iterable[list[str]]
    name: str | None
    networks: Iterable[pydocker.components.network.cli_wrapper.ValidNetwork]
    network_aliases: Iterable[str]
    oom_kill: bool
    oom_score_adj: int | None
    pid: str | None
    pids_limit: int | None
    platform: str | None
    pod: pydocker.components.pod.cli_wrapper.ValidPod | None
    preserve_fds: int | None
    privileged: bool
    publish: Iterable[ValidPortMapping]
    publish_all: bool
    pull: str
    read_only: bool
    restart: str | None
    remove: bool
    runtime: str | None
    security_options: Iterable[str]
    service_ports: bool
    shm_size: int | str | None
    sig_proxy: bool
    stop_signal: int | str | None
    stop_timeout: int | None
    storage_options: Iterable[str]
    sysctl: Mapping[str, str]
    systemd: bool | Literal["always"] | None
    tmpfs: Iterable[ValidPath]
    tty: bool
    tz: str | None
    ulimit: Iterable[str]
    user: str | None
    userns: str | None
    uts: str | None
    volumes: Iterable[pydocker.components.volume.cli_wrapper.VolumeDefinition]
    volume_driver: str | None
    volumes_from: Iterable[ValidContainer]
    workdir: ValidPath | None


class ContainerCLI(DockerCLICaller):
    def attach(
        self,
        container: ValidContainer,
        detach_keys: str | None = None,
        stdin: bool = True,
        sig_proxy: bool = True,
    ) -> None:
        """Attach local standard input, output, and error streams to a running container

        Alias: `docker.attach(...)`

        Parameters:
            container: The running container to attach to
            detach_keys: Override the key sequence for detaching a container
            stdin: Attach STDIN
            sig_proxy: Proxy all received signals to the process (default true)

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        self.inspect(container)

        full_cmd = Command([*self.docker_cmd, "attach"])
        full_cmd.add_simple_arg("--detach-keys", detach_keys)
        full_cmd.add_flag("--no-stdin", not stdin)
        full_cmd.add_flag("--sig-proxy", sig_proxy)
        full_cmd.append(container)

        run(full_cmd, tty=True)

    def commit(
        self,
        container: ValidContainer,
        tag: str | None = None,
        author: str | None = None,
        message: str | None = None,
        pause: bool = True,
    ) -> pydocker.components.image.cli_wrapper.Image:
        """Create a new image from a container's changes

        Parameters:
            container: The container to create the image from
            tag: tag to apply on the image produced
            author: Author (e.g., "John Hannibal Smith <hannibal@a-team.com>")
            message: Commit message
            pause: Pause container during commit
        """
        full_cmd = Command([*self.docker_cmd, "container", "commit"])

        full_cmd.add_simple_arg("--author", author)
        full_cmd.add_simple_arg("--message", message)

        # TODO: fixme
        # full_cmd += ["--pause", str(pause).lower()]

        full_cmd.append(str(container))
        if tag is not None:
            full_cmd.append(tag)

        return pydocker.components.image.cli_wrapper.Image(
            self.client_config, run(full_cmd), is_immutable_id=True
        )

    def copy(
        self,
        source: ValidPath | ContainerPath,
        destination: ValidPath | ContainerPath,
    ) -> None:
        """Copy files/folders between a container and the local filesystem

        Alias: `docker.copy(...)`

        ```python
        from pydocker import docker

        docker.run("ubuntu", ["sleep", "infinity"], name="dodo", remove=True, detach=True)

        docker.copy("/tmp/my_local_file.txt", ("dodo", "/path/in/container.txt"))
        docker.copy(("dodo", "/path/in/container.txt"), "/tmp/my_local_file2.txt")
        ```

        Doesn't yet support sending or receiving iterators of Python bytes.

        Parameters:
            source: Local path or tuple. When using a tuple, the first element
                of the tuple is the container, the second element is the path in
                the container. ex: `source=("my-container", "/usr/bin/something")`.
            destination: Local path or tuple. When using a tuple, the first element
                of the tuple is the container, the second element is the path in
                the container. ex: `source=("my-container", "/usr/bin/something")`.
        """
        full_cmd = Command([*self.docker_cmd, "container", "cp"])

        if isinstance(source, tuple):
            source = f"{source[0]!s}:{source[1]}"
        else:
            source = str(source)

        if isinstance(destination, tuple):
            destination = f"{destination[0]!s}:{destination[1]}"
        else:
            destination = str(destination)

        run([*full_cmd, source, destination])

    def create(
        self,
        image: pydocker.components.image.cli_wrapper.ValidImage,
        command: Sequence[str] = (),
        *,
        add_hosts: Iterable[tuple[str, str]] = (),
        blkio_weight: int | None = None,
        blkio_weight_device: Iterable[str] = (),
        cap_add: Iterable[str] = (),
        cap_drop: Iterable[str] = (),
        cgroup_parent: str | None = None,
        cgroupns: str | None = None,
        cidfile: ValidPath | None = None,
        cpu_period: int | None = None,
        cpu_quota: int | None = None,
        cpu_rt_period: int | None = None,
        cpu_rt_runtime: int | None = None,
        cpu_shares: int | None = None,
        cpus: float | None = None,
        cpuset_cpus: builtins.list[int] | str | None = None,
        cpuset_mems: builtins.list[int] | str | None = None,
        detach: bool = False,
        devices: Iterable[str] = (),
        device_cgroup_rules: Iterable[str] = (),
        device_read_bps: Iterable[str] = (),
        device_read_iops: Iterable[str] = (),
        device_write_bps: Iterable[str] = (),
        device_write_iops: Iterable[str] = (),
        content_trust: bool = False,
        dns: Iterable[str] = (),
        dns_options: Iterable[str] = (),
        dns_search: Iterable[str] = (),
        domainname: str | None = None,
        entrypoint: str | None = None,
        envs: Mapping[str, str] = {},
        env_files: ValidPath | Iterable[ValidPath] = (),
        env_host: bool = False,
        expose: int | Iterable[int] = (),
        gpus: int | str | None = None,
        groups_add: Iterable[str] = (),
        healthcheck: bool = True,
        health_cmd: str | None = None,
        health_interval: int | timedelta | None = None,
        health_retries: int | None = None,
        health_start_period: int | timedelta | None = None,
        health_timeout: int | timedelta | None = None,
        hostname: str | None = None,
        init: bool = False,
        interactive: bool = False,
        ip: str | None = None,
        ip6: str | None = None,
        ipc: str | None = None,
        isolation: str | None = None,
        kernel_memory: int | str | None = None,
        labels: Mapping[str, str] = {},
        label_files: Iterable[ValidPath] = (),
        link: Iterable[ValidContainer] = (),
        link_local_ip: Iterable[str] = (),
        log_driver: str | None = None,
        log_options: Iterable[str] = (),
        mac_address: str | None = None,
        memory: int | str | None = None,
        memory_reservation: int | str | None = None,
        memory_swap: int | str | None = None,
        memory_swappiness: int | None = None,
        mounts: Iterable[Iterable[str]] = (),
        name: str | None = None,
        networks: Iterable[pydocker.components.network.cli_wrapper.ValidNetwork] = (),
        network_aliases: Iterable[str] = (),
        oom_kill: bool = True,
        oom_score_adj: int | None = None,
        pid: str | None = None,
        pids_limit: int | None = None,
        platform: str | None = None,
        pod: pydocker.components.pod.cli_wrapper.ValidPod | None = None,
        privileged: bool = False,
        publish: Iterable[ValidPortMapping] = (),
        publish_all: bool = False,
        pull: str = "missing",
        read_only: bool = False,
        restart: str | None = None,
        remove: bool = False,
        runtime: str | None = None,
        security_options: Iterable[str] = (),
        shm_size: int | str | None = None,
        sig_proxy: bool = True,
        stop_signal: int | str | None = None,
        stop_timeout: int | None = None,
        storage_options: Iterable[str] = (),
        sysctl: Mapping[str, str] = {},
        systemd: bool | Literal["always"] | None = None,
        tmpfs: Iterable[ValidPath] = (),
        tty: bool = False,
        tz: str | None = None,
        ulimit: Iterable[str] = (),
        user: str | None = None,
        userns: str | None = None,
        uts: str | None = None,
        volumes: Iterable[pydocker.components.volume.cli_wrapper.VolumeDefinition] = (),
        volume_driver: str | None = None,
        volumes_from: Iterable[ValidContainer] = (),
        workdir: ValidPath | None = None,
    ) -> Container:
        """Creates a container, but does not start it.

        Alias: `docker.create(...)`

        Start it then with the `.start()` method.

        It might be useful if you want to delay the start of a container,
        to do some preparations beforehand. For example, it's common to do this
        workflow: `docker create` -> `docker cp` -> `docker start` to put files
        in the container before starting.

        There is no `detach` argument since it's a runtime option.

        The arguments are the same as [`docker.run`](#run).
        """
        if isinstance(command, str):
            error_message = textwrap.dedent(
                f"""\
                When calling docker.create(), the second argument ('command') should be a sequence of strings.
                Here are some examples:
                 docker.create('ubuntu', ['ls'])
                 docker.create('ubuntu', ['cat', '/some/file.txt'])
                You can try docker.create('{image}', {shlex.split(command)}, ...).
                """
            )
            raise TypeError(error_message)

        image_cli = pydocker.components.image.cli_wrapper.ImageCLI(self.client_config)
        if pull == "missing":
            image_cli._pull_if_necessary(image)
        elif pull == "always":
            image_cli.pull(str(image))

        full_cmd = Command([*self.docker_cmd, "create"])

        full_cmd.add_args_iterable(
            "--add-host", (f"{host}:{ip}" for host, ip in add_hosts)
        )

        full_cmd.add_simple_arg("--blkio-weight", blkio_weight)
        full_cmd.add_args_iterable("--blkio-weight-device", blkio_weight_device)

        full_cmd.add_args_iterable("--cap-add", cap_add)
        full_cmd.add_args_iterable("--cap-drop", cap_drop)

        full_cmd.add_simple_arg("--cgroup-parent", cgroup_parent)
        full_cmd.add_simple_arg("--cgroupns", cgroupns)
        full_cmd.add_simple_arg("--cidfile", cidfile)

        full_cmd.add_simple_arg("--cpu-period", cpu_period)
        full_cmd.add_simple_arg("--cpu-quota", cpu_quota)
        full_cmd.add_simple_arg("--cpu-rt-period", cpu_rt_period)
        full_cmd.add_simple_arg("--cpu-rt-runtime", cpu_rt_runtime)
        full_cmd.add_simple_arg("--cpu-shares", cpu_shares)
        full_cmd.add_simple_arg("--cpus", cpus)
        full_cmd.add_simple_arg("--cpuset-cpus", format_cpuset(cpuset_cpus))
        full_cmd.add_simple_arg("--cpuset-mems", format_cpuset(cpuset_mems))

        full_cmd.add_flag("--detach", detach)

        full_cmd.add_args_iterable("--device", devices)
        full_cmd.add_args_iterable("--device-cgroup-rule", device_cgroup_rules)
        full_cmd.add_args_iterable("--device-read-bps", device_read_bps)
        full_cmd.add_args_iterable("--device-read-iops", device_read_iops)
        full_cmd.add_args_iterable("--device-write-bps", device_write_bps)
        full_cmd.add_args_iterable("--device-write-iops", device_write_iops)

        if content_trust:
            full_cmd += ["--disable-content-trust", "false"]

        full_cmd.add_args_iterable("--dns", dns)
        full_cmd.add_args_iterable("--dns-option", dns_options)
        full_cmd.add_args_iterable("--dns-search", dns_search)
        full_cmd.add_simple_arg("--domainname", domainname)

        full_cmd.add_simple_arg("--entrypoint", entrypoint)

        full_cmd.add_args_mapping("--env", envs)
        full_cmd.add_args_iterable_or_single("--env-file", env_files)
        full_cmd.add_flag("--env-host", env_host)

        full_cmd.add_args_iterable_or_single("--expose", expose)

        full_cmd.add_simple_arg("--gpus", gpus)

        full_cmd.add_args_iterable("--group-add", groups_add)

        full_cmd.add_flag("--no-healthcheck", not healthcheck)
        full_cmd.add_simple_arg("--health-cmd", health_cmd)
        full_cmd.add_simple_arg("--health-interval", to_seconds(health_interval))
        full_cmd.add_simple_arg("--health-retries", health_retries)
        full_cmd.add_simple_arg(
            "--health-start-period", to_seconds(health_start_period)
        )
        full_cmd.add_simple_arg("--health-timeout", to_seconds(health_timeout))

        full_cmd.add_simple_arg("--hostname", hostname)

        full_cmd.add_flag("--init", init)
        full_cmd.add_flag("--interactive", interactive)

        full_cmd.add_simple_arg("--ip", ip)
        full_cmd.add_simple_arg("--ip6", ip6)
        full_cmd.add_simple_arg("--ipc", ipc)

        full_cmd.add_simple_arg("--isolation", isolation)
        full_cmd.add_simple_arg("--kernel-memory", kernel_memory)

        full_cmd.add_args_mapping("--label", labels)
        full_cmd.add_args_iterable("--label-file", label_files)

        full_cmd.add_args_iterable("--link", link)
        full_cmd.add_args_iterable("--link-local-ip", link_local_ip)

        full_cmd.add_simple_arg("--log-driver", log_driver)
        full_cmd.add_args_iterable("--log-opt", log_options)

        full_cmd.add_simple_arg("--mac-address", mac_address)

        full_cmd.add_simple_arg("--memory", memory)
        full_cmd.add_simple_arg("--memory-reservation", memory_reservation)
        full_cmd.add_simple_arg("--memory-swap", memory_swap)
        full_cmd.add_simple_arg("--memory-swappiness", memory_swappiness)

        full_cmd.add_args_iterable("--mount", (",".join(x) for x in mounts))
        full_cmd.add_simple_arg("--name", name)

        full_cmd.add_args_iterable("--network", networks)
        full_cmd.add_args_iterable("--network-alias", network_aliases)

        full_cmd.add_flag("--oom-kill-disable", not oom_kill)
        full_cmd.add_simple_arg("--oom-score-adj", oom_score_adj)

        full_cmd.add_simple_arg("--pid", pid)
        full_cmd.add_simple_arg("--pids-limit", pids_limit)

        full_cmd.add_simple_arg("--platform", platform)
        full_cmd.add_simple_arg("--pod", pod)
        full_cmd.add_flag("--privileged", privileged)

        full_cmd.add_args_iterable("-p", (format_port_arg(p) for p in publish))
        full_cmd.add_flag("--publish-all", publish_all)

        if pull == "never":
            full_cmd.add_simple_arg("--pull", "never")

        full_cmd.add_flag("--read-only", read_only)
        full_cmd.add_simple_arg("--restart", restart)
        full_cmd.add_flag("--rm", remove)

        full_cmd.add_simple_arg("--runtime", runtime)
        full_cmd.add_args_iterable("--security-opt", security_options)

        full_cmd.add_simple_arg("--shm-size", shm_size)
        if sig_proxy is False:
            full_cmd += ["--sig-proxy", "false"]

        full_cmd.add_simple_arg("--stop-signal", format_signal_arg(stop_signal))
        full_cmd.add_simple_arg("--stop-timeout", stop_timeout)

        full_cmd.add_args_iterable("--storage-opt", storage_options)
        full_cmd.add_args_mapping("--sysctl", sysctl)
        full_cmd.add_simple_arg("--systemd", systemd)
        full_cmd.add_args_iterable("--tmpfs", tmpfs)
        full_cmd.add_flag("--tty", tty)
        full_cmd.add_simple_arg("--tz", tz)
        full_cmd.add_args_iterable("--ulimit", ulimit)

        full_cmd.add_simple_arg("--user", user)
        full_cmd.add_simple_arg("--userns", userns)
        full_cmd.add_simple_arg("--uts", uts)

        for volume_definition in volumes:
            volume_definition = tuple(str(x) for x in volume_definition)
            full_cmd += ["--volume", ":".join(volume_definition)]
        full_cmd.add_simple_arg("--volume-driver", volume_driver)
        full_cmd.add_args_iterable("--volumes-from", volumes_from)

        full_cmd.add_simple_arg("--workdir", workdir)

        full_cmd.append(str(image))
        full_cmd.extend(command)
        return Container(self.client_config, run(full_cmd), is_immutable_id=True)

    def diff(self, container: ValidContainer) -> dict[str, str]:
        """List all the files modified, added or deleted since the container started.

        Alias: `docker.diff(...)`

        Parameters:
            container: The container to inspect

        # Returns
            `Dict[str, str]` Something like
            `{"/some_path": "A", "/some_file": "M", "/tmp": "D"}` for example.

        """
        full_cmd = Command([*self.docker_cmd, "diff", container])

        result_dict = {}
        result = run(full_cmd)
        for line in result.splitlines():
            result_dict[line[2:]] = line[0]
        return result_dict

    @overload
    def execute(
        self,
        container: ValidContainer,
        command: Sequence[str],
        *,
        detach: Literal[True],
        stream: bool = False,
        preserve_fds: int | None = None,
        **kwargs: Unpack[ExecuteArgs],
    ) -> None: ...

    @overload
    def execute(
        self,
        container: ValidContainer,
        command: Sequence[str],
        *,
        detach: Literal[False] = False,
        stream: Literal[True],
        preserve_fds: int | None = None,
        **kwargs: Unpack[ExecuteArgs],
    ) -> ProcessStream: ...

    @overload
    def execute(
        self,
        container: ValidContainer,
        command: Sequence[str],
        *,
        detach: Literal[False] = False,
        stream: Literal[False] = False,
        preserve_fds: int | None = None,
        **kwargs: Unpack[ExecuteArgs],
    ) -> str: ...

    @overload
    def execute(
        self,
        container: ValidContainer,
        command: Sequence[str],
        detach: bool = False,
        envs: Mapping[str, str] = MappingProxyType({}),
        env_files: ValidPath | Iterable[ValidPath] = (),
        interactive: bool = False,
        privileged: bool = False,
        tty: bool = False,
        user: str | None = None,
        workdir: ValidPath | None = None,
        stream: bool = False,
        detach_keys: str | None = None,
        preserve_fds: int | None = None,
    ) -> str | ProcessStream | None: ...

    def execute(
        self,
        container: ValidContainer,
        command: Sequence[str],
        detach: bool = False,
        envs: Mapping[str, str] = {},
        env_files: ValidPath | Iterable[ValidPath] = (),
        interactive: bool = False,
        privileged: bool = False,
        tty: bool = False,
        user: str | None = None,
        workdir: ValidPath | None = None,
        stream: bool = False,
        detach_keys: str | None = None,
        preserve_fds: int | None = None,
    ) -> str | ProcessStream | None:
        """Execute a command inside a container

        Alias: `docker.execute(...)`

        Parameters:
            container: The container to execute the command in.
            command: The command to execute.
            detach: if `True`, returns immediately with `None`. If `False`,
                returns the command stdout as string.
            envs: Set environment variables
            env_files: Read one or more files of environment variables
            interactive: Leave stdin open during the duration of the process
                to allow communication with the parent process.
                Can be combined with `stream=True` to get an
                `ProcessStream` that allows writing to stdin while
                iterating over stdout/stderr.
            preserve_fds: The number of additional file descriptors to pass
                through to the container. Only supported by podman.
            privileged: Give extended privileges to the container.
            tty: Allocate a pseudo-TTY. Allow the process to access your terminal
                to write on it.
            user: Username or UID, format: `"<name|uid>[:<group|gid>]"`
            workdir: Working directory inside the container
            stream: Similar to `docker.run(..., stream=True)`.
            detach_keys: Override the key sequence for detaching a container.

        Returns:
            Optional[str]

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        if isinstance(command, str):
            container_name = container if isinstance(container, str) else container.name
            error_message = textwrap.dedent(
                f"""\
                When calling docker.execute(), the second argument ('command') should be a sequence of strings.
                Here are some examples:
                  docker.execute('somecontainer', ['ls'])
                  docker.execute('somecontainer', ['cat', '/some/file.txt'])
                You can try docker.execute('{container_name}', {shlex.split(command)}, ...).
                """
            )
            raise TypeError(error_message)

        full_cmd = Command([*self.docker_cmd, "exec"])

        full_cmd.add_flag("--detach", detach)
        full_cmd.add_simple_arg("--detach-keys", detach_keys)

        full_cmd.add_args_mapping("--env", envs)
        full_cmd.add_args_iterable_or_single("--env-file", env_files)

        if tty and stream:
            raise ValueError(
                "You can't set tty=True and stream=True at the same"
                "time. Their purpose are not compatible."
            )

        if detach and stream:
            raise ValueError(
                "You can't detach and stream at the same time. It's not compatible."
            )

        full_cmd.add_flag("--interactive", interactive)
        full_cmd.add_simple_arg("--preserve-fds", preserve_fds)
        full_cmd.add_flag("--privileged", privileged)
        full_cmd.add_flag("--tty", tty)

        full_cmd.add_simple_arg("--user", user)
        full_cmd.add_simple_arg("--workdir", workdir)

        full_cmd.append(container)
        full_cmd.extend(command)

        pass_fds = range(3, 3 + preserve_fds) if preserve_fds is not None else ()
        if stream:
            return stream_stdout_and_stderr(
                full_cmd, pass_fds=pass_fds, pipe_stdin=interactive
            )
        else:
            result = run(full_cmd, tty=tty, pass_fds=pass_fds)
            if detach:
                return None
            else:
                return result

    def exists(self, x: ValidContainer) -> bool:
        """Returns `True` if the container exists. `False` otherwise.

         It's just calling `docker.container.inspect(...)` and verifies that it doesn't throw
         a `pydocker.exceptions.NoSuchContainer`.

        # Returns
            A `bool`
        """
        try:
            self.inspect(x)
        except NoSuchContainer:
            return False
        else:
            return True

    def export(self, container: ValidContainer, output: ValidPath) -> None:
        """Export a container's filesystem as a tar archive

        Alias: `docker.export(...)`

        Parameters:
            container: The container to export.
            output: The path of the output tar archive. Returning a generator of bytes
                is not yet implemented.

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        full_cmd = Command([*self.docker_cmd, "container", "export"])
        full_cmd.add_simple_arg("--output", output)

        full_cmd.append(container)

        if output is None:
            # we must return a generator of bytes
            raise NotImplementedError
        else:
            run(full_cmd)

    def init(self, containers: ValidContainer | Iterable[ValidContainer]) -> None:
        """Initialize one or more containers.

        Note that this is only supported by podman.

        Alias: `docker.init(...)`

        Parameters:
            containers: One or more containers to kill
            output: The path of the output tar archive. Returning a generator of bytes
                is not yet implemented.

        # Raises
            `pydocker.exceptions.NoSuchContainer` if any of the
            containers do not exist.
        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if len(container_list) == 0:
            # nothing to do
            return

        full_cmd = Command([*self.docker_cmd, "container", "init"])
        full_cmd += container_list

        run(full_cmd)

    @overload
    def inspect(self, x: ValidContainer, /) -> Container: ...

    @overload
    def inspect(self, x: Iterable[ValidContainer], /) -> builtins.list[Container]: ...

    def inspect(
        self, x: ValidContainer | Iterable[ValidContainer], /
    ) -> Container | builtins.list[Container]:
        """Returns a container object from a name or ID.

        Parameters:
            x: A container name or ID, or a list of container names
                and/or IDs

        Returns:
            A `pydocker.Container` object or a list of those
            if a list of IDs was passed as input.

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """

        if isinstance(x, str | Container):
            return Container(self.client_config, str(x))
        references = [str(reference) for reference in x]
        if not references:
            return []
        json_str = run([*self.docker_cmd, "container", "inspect", *references])
        parser = Container(self.client_config, references[0], is_immutable_id=True)
        snapshots = parser._parse_inspect_results(json_str, references)
        containers = []
        for reference, snapshot in zip(references, snapshots, strict=True):
            if snapshot.id is None or not (
                reference
                and (
                    snapshot.id.startswith(reference)
                    or reference.removeprefix("/")
                    == (snapshot.name or "").removeprefix("/")
                )
            ):
                raise ParsingError(
                    f"Container inspect identity does not match {reference!r}"
                )
            container = Container(self.client_config, snapshot.id, is_immutable_id=True)
            container._set_inspect_result(snapshot)
            containers.append(container)
        return containers

    def kill(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        signal: int | str | None = None,
    ) -> None:
        """Kill one or more containers.

        Alias: `docker.kill(...)`

        Parameters:
            containers: One or more containers to kill
            signal: The signal to send the container

        # Raises
            `pydocker.exceptions.NoSuchContainer` if any of the
            containers do not exist.

        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if container_list == []:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "container", "kill"])

        full_cmd.add_simple_arg("--signal", format_signal_arg(signal))
        full_cmd += container_list

        run(full_cmd)

    @overload
    def logs(
        self,
        container: ValidContainer,
        *,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        until: datetime | timedelta | None = None,
        follow: bool = False,
        stream: Literal[False] = False,
    ) -> str: ...

    @overload
    def logs(
        self,
        container: ValidContainer,
        *,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        until: datetime | timedelta | None = None,
        follow: bool = False,
        stream: Literal[True],
    ) -> ProcessStream: ...

    @overload
    def logs(
        self,
        container: ValidContainer,
        *,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        until: datetime | timedelta | None = None,
        follow: bool = False,
        stream: bool = False,
    ) -> str | ProcessStream: ...

    def logs(
        self,
        container: ValidContainer,
        *,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        until: datetime | timedelta | None = None,
        follow: bool = False,
        stream: bool = False,
    ) -> str | ProcessStream:
        """Returns the logs of a container as a string or an iterator.

        Alias: `docker.logs(...)`

        Parameters:
            container: The container to get the logs of
            details: Show extra details provided to logs
            since: Use a datetime or timedelta to specify the lower
                date limit for the logs.
            tail: Number of lines to show from the end of the logs (default all)
            timestamps: Put timestamps next to lines.
            until: Use a datetime or a timedelta to specify the upper date
                limit for the logs.
            follow: If `False` (the default), the logs returned are the logs up to the time
                of the function call. If `True`, the logs of the container up to the time the
                container stopped are displayed. Which means that if the container isn't stopped
                yet, the function will continue until the container is stopped.
                Which is why it is advised to use the `stream` option if you use the `follow` option.
                Without `stream`, only a `str` will be returned, possibly much later in the
                future. With `stream`, you'll be able to read the logs in real time.
            stream: Similar to the `stream` argument of `docker.run`.
                This function will then return an iterator that will yield a
                tuple `(source, content)` with `source` being `"stderr"` or
                `"stdout"`. `content` is the content of the line as bytes.
                Take a look at [the user guide](https://github.com/MaraClaw/PyDocker/blob/HEAD/docs/template/user_guide/docker_run.md#stream-the-output)
                to have an example of the output.

        # Returns
            `str` if `stream=False` (the default), `Iterable[Tuple[str, bytes]]`
            if `stream=True`.

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exist.

        If you are a bit confused about `follow` and `stream`, here are some use cases.

        * If you want to have the logs up to this point as a `str`, don't use those args.
        * If you want to stream the output in real time, use `follow=True, stream=True`
        * If you want the logs up to this point, but you don't want to fit all the logs
        in memory because they are too big, use `stream=True`.
        """

        # first we verify that the container exists and raise an exception if not.
        self.inspect(container)

        full_cmd = Command([*self.docker_cmd, "container", "logs"])
        full_cmd.add_flag("--details", details)
        full_cmd.add_simple_arg("--since", format_time_arg(since))
        full_cmd.add_simple_arg("--tail", tail)
        full_cmd.add_flag("--timestamps", timestamps)
        full_cmd.add_simple_arg("--until", format_time_arg(until))
        full_cmd.add_flag("--follow", follow)
        full_cmd.append(container)

        iterator = stream_stdout_and_stderr(full_cmd)

        if stream:
            return iterator
        else:
            return "".join(x[1].decode() for x in iterator)

    def list(
        self,
        all: bool = False,
        filters: Iterable[ContainerListFilter] | Mapping[str, Any] = (),
    ) -> builtins.list[Container]:
        """List the containers on the host.

        Alias: `docker.ps(...)`

        Parameters:
            all: If `True`, also returns containers that are not running.
            filters: Filters to apply when listing containers.

        # Returns
            A `List[pydocker.Container]`
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
        full_cmd = self.docker_cmd
        full_cmd += ["container", "list", "-q", "--no-trunc"]
        full_cmd.add_flag("--all", all)
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))

        # TODO: add a test for the fix of is_immutable_id, without it, we get
        # race conditions (we read the attributes of a container but it might not exist.
        return [
            Container(self.client_config, x, is_immutable_id=True)
            for x in run(full_cmd).splitlines()
        ]

    def pause(self, containers: ValidContainer | Iterable[ValidContainer]) -> None:
        """Pauses one or more containers

        Alias: `docker.pause(...)`

        Parameters:
            containers: One or more containers to pause

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if container_list == []:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "pause"])
        full_cmd.extend(container_list)

        run(full_cmd)

    @overload
    def prune(
        self,
        filters: Iterable[ContainerListFilter] | Mapping[str, object] = (),
        *,
        stream_logs: Literal[True],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def prune(
        self,
        filters: Iterable[ContainerListFilter] | Mapping[str, object] = (),
        stream_logs: Literal[False] = ...,
    ) -> None: ...

    @overload
    def prune(
        self,
        filters: Iterable[ContainerListFilter] | Mapping[str, object] = (),
        stream_logs: bool = False,
    ) -> Iterable[tuple[str, bytes]] | None: ...

    def prune(
        self,
        filters: Iterable[ContainerListFilter] | Mapping[str, Any] = (),
        stream_logs: bool = False,
    ) -> Iterable[tuple[str, bytes]] | None:
        """Remove containers that are not running.

        Parameters:
            filters: Filters to apply when pruning.
            stream_logs: If `True` this function will return an iterator of strings.
                You can then read the logs as they arrive. If `False` (the default value), then
                the function returns `None`, but when it returns, then the prune operation has already been
                done.
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
        full_cmd = Command([*self.docker_cmd, "container", "prune", "--force"])
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))
        if stream_logs:
            return stream_stdout_and_stderr(full_cmd)
        run(full_cmd)

    def rename(self, container: ValidContainer, new_name: str) -> None:
        """Changes the name of a container.

        Alias: `docker.rename(...)`

        Parameters:
            container: The container to rename
            new_name: The new name of the container.

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exist.
        """
        full_cmd = Command(
            [*self.docker_cmd, "container", "rename", str(container), new_name]
        )
        run(full_cmd)

    def restart(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        time: int | timedelta | None = None,
    ) -> None:
        """Restarts one or more container.

        Alias: `docker.restart(...)`

        Parameters:
            containers: One or more containers to restart
            time: Amount of to wait for stop before killing the container (default 10s).
                If `int`, the unit is seconds.

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if not container_list:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "restart"])

        if time is not None:
            seconds = time.total_seconds() if isinstance(time, timedelta) else time
            full_cmd += ["--time", str(seconds)]

        full_cmd.extend(container_list)

        run(full_cmd)

    def remove(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        *,
        force: bool = False,
        volumes: bool = False,
    ) -> None:
        """Removes a container

        Alias: `docker.remove(...)`

        Parameters:
            containers: One or more containers.
            force: Force the removal of a running container (uses SIGKILL)
            volumes: Remove anonymous volumes associated with the container

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if not container_list:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "container", "rm"])
        full_cmd.add_flag("--force", force)
        full_cmd.add_flag("--volumes", volumes)

        full_cmd.extend(container_list)

        run(full_cmd)

    @overload
    def run(
        self,
        image: pydocker.components.image.cli_wrapper.ValidImage,
        command: Sequence[str] = ...,
        *,
        detach: Literal[True],
        stream: bool = ...,
        **kwargs: Unpack[RunArgs],
    ) -> Container: ...

    @overload
    def run(
        self,
        image: pydocker.components.image.cli_wrapper.ValidImage,
        command: Sequence[str] = ...,
        *,
        detach: Literal[False] = ...,
        stream: Literal[True],
        **kwargs: Unpack[RunArgs],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def run(
        self,
        image: pydocker.components.image.cli_wrapper.ValidImage,
        command: Sequence[str] = ...,
        *,
        detach: Literal[False] = ...,
        stream: Literal[False] = ...,
        **kwargs: Unpack[RunArgs],
    ) -> str: ...

    @overload
    def run(
        self,
        image: pydocker.components.image.cli_wrapper.ValidImage,
        command: Sequence[str] = (),
        *,
        detach: bool = False,
        stream: bool = False,
        **kwargs: Unpack[RunArgs],
    ) -> Container | str | Iterable[tuple[str, bytes]]: ...

    def run(
        self,
        image: pydocker.components.image.cli_wrapper.ValidImage,
        command: Sequence[str] = (),
        *,
        add_hosts: Iterable[tuple[str, str]] = (),
        blkio_weight: int | None = None,
        blkio_weight_device: Iterable[str] = (),
        cap_add: Iterable[str] = (),
        cap_drop: Iterable[str] = (),
        cgroup_parent: str | None = None,
        cgroupns: str | None = None,
        cidfile: ValidPath | None = None,
        cpu_period: int | None = None,
        cpu_quota: int | None = None,
        cpu_rt_period: int | None = None,
        cpu_rt_runtime: int | None = None,
        cpu_shares: int | None = None,
        cpus: float | None = None,
        cpuset_cpus: builtins.list[int] | str | None = None,
        cpuset_mems: builtins.list[int] | str | None = None,
        detach: bool = False,
        devices: Iterable[str] = (),
        device_cgroup_rules: Iterable[str] = (),
        device_read_bps: Iterable[str] = (),
        device_read_iops: Iterable[str] = (),
        device_write_bps: Iterable[str] = (),
        device_write_iops: Iterable[str] = (),
        content_trust: bool = False,
        dns: Iterable[str] = (),
        dns_options: Iterable[str] = (),
        dns_search: Iterable[str] = (),
        domainname: str | None = None,
        entrypoint: str | None = None,
        envs: Mapping[str, str] = {},
        env_files: ValidPath | Iterable[ValidPath] = (),
        env_host: bool = False,
        expose: int | Iterable[int] = (),
        gpus: int | str | None = None,
        groups_add: Iterable[str] = (),
        healthcheck: bool = True,
        health_cmd: str | None = None,
        health_interval: int | timedelta | None = None,
        health_retries: int | None = None,
        health_start_period: int | timedelta | None = None,
        health_timeout: int | timedelta | None = None,
        hostname: str | None = None,
        init: bool = False,
        interactive: bool = False,
        ip: str | None = None,
        ip6: str | None = None,
        ipc: str | None = None,
        isolation: str | None = None,
        kernel_memory: int | str | None = None,
        labels: Mapping[str, str] = {},
        label_files: Iterable[ValidPath] = (),
        link: Iterable[ValidContainer] = (),
        link_local_ip: Iterable[str] = (),
        log_driver: str | None = None,
        log_options: Iterable[str] = (),
        mac_address: str | None = None,
        memory: int | str | None = None,
        memory_reservation: int | str | None = None,
        memory_swap: int | str | None = None,
        memory_swappiness: int | None = None,
        mounts: Iterable[builtins.list[str]] = (),
        name: str | None = None,
        networks: Iterable[pydocker.components.network.cli_wrapper.ValidNetwork] = (),
        network_aliases: Iterable[str] = (),
        oom_kill: bool = True,
        oom_score_adj: int | None = None,
        pid: str | None = None,
        pids_limit: int | None = None,
        platform: str | None = None,
        pod: pydocker.components.pod.cli_wrapper.ValidPod | None = None,
        preserve_fds: int | None = None,
        privileged: bool = False,
        publish: Iterable[ValidPortMapping] = (),
        publish_all: bool = False,
        pull: str = "missing",
        read_only: bool = False,
        restart: str | None = None,
        remove: bool = False,
        runtime: str | None = None,
        security_options: Iterable[str] = (),
        service_ports: bool = False,
        shm_size: int | str | None = None,
        sig_proxy: bool = True,
        stop_signal: int | str | None = None,
        stop_timeout: int | None = None,
        storage_options: Iterable[str] = (),
        stream: bool = False,
        sysctl: Mapping[str, str] = {},
        systemd: bool | Literal["always"] | None = None,
        tmpfs: Iterable[ValidPath] = (),
        tty: bool = False,
        tz: str | None = None,
        ulimit: Iterable[str] = (),
        user: str | None = None,
        userns: str | None = None,
        uts: str | None = None,
        volumes: Iterable[pydocker.components.volume.cli_wrapper.VolumeDefinition] = (),
        volume_driver: str | None = None,
        volumes_from: Iterable[ValidContainer] = (),
        workdir: ValidPath | None = None,
    ) -> Container | str | Iterable[tuple[str, bytes]]:
        """Runs a container

        You can use `docker.run` or `docker.container.run` to call this function.

        For a deeper dive into the arguments and what they do, visit
        <https://docs.docker.com/engine/reference/run/>

        If you want to know exactly how to call `docker.run()` depending on your
        use case (detach, stream...), take a look at
        the [`docker.run()` guide](../user_guide/docker_run.md).

        ```python
        >>> from pydocker import docker
        >>> returned_string = docker.run("hello-world")
        >>> print(returned_string)

        Hello from Docker!
        This message shows that your installation appears to be working correctly.

        To generate this message, Docker took the following steps:
         1. The Docker client contacted the Docker daemon.
         2. The Docker daemon pulled the "hello-world" image from the Docker Hub.
            (amd64)
         3. The Docker daemon created a new container from that image which runs the
            executable that produces the output you are currently reading.
         4. The Docker daemon streamed that output to the Docker client, which sent it
            to your terminal.

        To try something more ambitious, you can run an Ubuntu container with:
         $ docker run -it ubuntu bash

        Share images, automate workflows, and more with a free Docker ID:
         https://hub.docker.com/

        For more examples and ideas, visit:
         https://docs.docker.com/get-started/
        ```

        ```python
        >>> from pydocker import docker
        >>> result_string = docker.run("ubuntu", ["ls", "/host"], volumes=[("/", "/host", "ro")])
        >>> print(result_string)
        bin
        boot
        dev
        etc
        home
        init
        lib
        lib64
        lost+found
        media
        mnt
        opt
        proc
        projects
        root
        run
        sbin
        snap
        srv
        sys
        tmp
        usr
        var
        ```

        Parameters:
            image: The image to use for the container.
            command: Sequence of arguments to provide to the container.
            add_hosts: hosts to add in the format of a tuple. For example,
                `add_hosts=[("my_host_1", "192.168.30.31"), ("host2", "192.168.80.81")]`
            blkio_weight: Block IO (relative weight), between 10 and 1000,
                or 0 to disable (default 0)
            cgroupns: Cgroup namespace mode to use, one of 'host' or 'private'.
            cpu_period: Limit CPU CFS (Completely Fair Scheduler) period
            cpu_quota: Limit CPU CFS (Completely Fair Scheduler) quota
            cpu_rt_period: Limit CPU real-time period in microseconds
            cpu_rt_runtime: Limit CPU real-time runtime in microseconds
            cpu_shares: CPU shares (relative weight)
            cpus: The maximal amount of cpu the container can use.
                `1` means one cpu core.
            cpuset_cpus: CPUs in which to allow execution. Accepts a list of
                CPU indices (e.g. `[0, 2]`) or a cpuset string as understood by
                the docker CLI (e.g. `"0-3"`, `"0,2"`, `"0-3,7"`).
            cpuset_mems: MEMs in which to allow execution. Accepts a list of
                memory-node indices or a cpuset string (same format as
                `cpuset_cpus`).
            detach: If `False`, returns the ouput of the container as a string.
                If `True`, returns a `pydocker.Container` object.
            dns_search: Set custom DNS search domains
            domainname: Container NIS domain name
            entrypoint: Overwrite the default ENTRYPOINT of the image
            envs: Environment variables as a `dict`.
                For example: `{"OMP_NUM_THREADS": 3}`
            env_files: One or a list of env files.
            env_host: Use host environment inside the container. Only supported
                with podman.
            gpus: For this to work, you need the
                [Nvidia container runtime](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html#install-guide)
                The value needed is a `str` or `int`. Some examples of valid argument
                are `"all"` or `"device=GPU-3a23c669-1f69-c64e-cf85-44e9b07e7a2a"` or
                `"device=0,2"`. If you want 3 gpus, just write `gpus=3`.
            hostname: Container host name
            interactive: Leave stdin open during the duration of the process
                to allow communication with the parent process.
                Currently only works with `tty=True` for interactive use
                on the terminal.
            ip: IPv4 address (e.g., 172.30.100.104)
            ip6: IPv6 address (e.g., 2001:db8::33)
            ipc: IPC mode to use
            isolation: Container isolation technology
            kernel_memory: Kernel memory limit. `int` represents the number of bytes,
                but you can use `"4k"` or `2g` for example.
            labels: Set meta data on a container. The labels can be used later when filtering
                containers with `docker.ps(filters='...')`. The labels can also be found on
                each container with the attribute `my_container.config.labels`.
            log_driver: Logging driver for the container
            mac_address: Container MAC address (e.g., `"92:d0:c6:0a:29:33"`)
            memory:  Memory limit, valid values are `1024` (ints are bytes) or
                `"43m"` or `"6g"`.
            memory_reservation: Memory soft limit
            memory_swap: Swap limit equal to memory plus swap: '-1'
                to enable unlimited swap.
            memory_swappiness: Tune container memory swappiness (0 to 100) (default -1)
            name: The container name. If not provided, one is automatically genrated for
                you.
            healthcheck: Set to `False` to disable container periodic healthcheck.
            oom_kill: Set to `False` to disable the OOM killer for this container.
            pid: PID namespace to use
            pids_limit: Tune container pids limit (set `-1` for unlimited)
            platform: Set platform if server is multi-platform capable.
            pod: Create the container in an existing pod (only supported with podman).
            preserve_fds: The number of additional file descriptors to pass
                through to the container. Only supported by podman.
            privileged: Give extended privileges to this container.
            publish: Ports to publish, same as the `-p` argument in the Docker CLI.
                example are `[(8000, 7000) , ("127.0.0.1:3000", 2000)]` or
                `[("127.0.0.1:3000", 2000, "udp")]`. You can also use a single entry in
                the tuple to signify that you want a random free port on the host. For example:
                `publish=[(80,)]`.
            publish_all: Publish all exposed ports to random ports.
            pull: Pull image before running ("always"|"missing"|"never") (default "missing").
            read_only: Mount the container's root filesystem as read only.
            restart: Restart policy to apply when a container exits (default "no")
            remove: Automatically remove the container when it exits.
            runtime: Runtime to use for this container.
            security_options: Security options
            service_ports: Enable service's ports to be created and mapped to the host (disabled by default)
            shm_size: Size of /dev/shm. `int` is for bytes. But you can use `"512m"` or
                `"4g"` for example.
            stop_timeout: Signal to stop a container (default "SIGTERM")
            storage_options: Storage driver options for the container
            systemd: Whether to run in systemd mode. Only known to apply to Podman, see
                https://docs.podman.io/en/latest/markdown/podman-run.1.html#systemd-true-false-always
            tty: Allocate a pseudo-TTY. Allow the process to access your terminal
                to write on it.
            tz: Set timezone in container, or `local` to match the host's timezone.
                See `/usr/share/zoneinfo/` for valid timezones.
                Note: This option is only known to apply to Podman containers.
            user: Username or UID (format: `<name|uid>[:<group|gid>]`)
            userns:  User namespace to use
            uts:  UTS namespace to use
            volumes:  Bind mount a volume. Some examples:
                `[("/", "/host"), ("/etc/hosts", "/etc/hosts", "rw")]`.
            volume_driver: Optional volume driver for the container
            workdir: The directory in the container where the process will be executed.

        # Returns
            The container output as a string if detach is `False` (the default),
            and a `pydocker.Container` if detach is `True`.
        """
        if isinstance(command, str):
            error_message = textwrap.dedent(
                f"""\
                When calling docker.run(), the second argument ('command') should be a sequence of strings.
                Here are some examples:
                 docker.run('ubuntu', ['ls'])
                 docker.run('ubuntu', ['cat', '/some/file.txt'])
                You can try docker.run('{image}', {shlex.split(command)}, ...).
                """
            )
            raise TypeError(error_message)

        image_cli = pydocker.components.image.cli_wrapper.ImageCLI(self.client_config)
        if pull == "missing":
            image_cli._pull_if_necessary(image)
        elif pull == "always":
            image_cli.pull(str(image))

        full_cmd = Command([*self.docker_cmd, "container", "run"])

        full_cmd.add_args_iterable(
            "--add-host", (f"{host}:{ip}" for host, ip in add_hosts)
        )

        full_cmd.add_simple_arg("--blkio-weight", blkio_weight)
        full_cmd.add_args_iterable("--blkio-weight-device", blkio_weight_device)

        full_cmd.add_args_iterable("--cap-add", cap_add)
        full_cmd.add_args_iterable("--cap-drop", cap_drop)

        full_cmd.add_simple_arg("--cgroup-parent", cgroup_parent)
        full_cmd.add_simple_arg("--cgroupns", cgroupns)
        full_cmd.add_simple_arg("--cidfile", cidfile)

        full_cmd.add_simple_arg("--cpu-period", cpu_period)
        full_cmd.add_simple_arg("--cpu-quota", cpu_quota)
        full_cmd.add_simple_arg("--cpu-rt-period", cpu_rt_period)
        full_cmd.add_simple_arg("--cpu-rt-runtime", cpu_rt_runtime)
        full_cmd.add_simple_arg("--cpu-shares", cpu_shares)
        full_cmd.add_simple_arg("--cpus", cpus)
        full_cmd.add_simple_arg("--cpuset-cpus", format_cpuset(cpuset_cpus))
        full_cmd.add_simple_arg("--cpuset-mems", format_cpuset(cpuset_mems))

        full_cmd.add_flag("--detach", detach)

        full_cmd.add_args_iterable("--device", devices)
        full_cmd.add_args_iterable("--device-cgroup-rule", device_cgroup_rules)
        full_cmd.add_args_iterable("--device-read-bps", device_read_bps)
        full_cmd.add_args_iterable("--device-read-iops", device_read_iops)
        full_cmd.add_args_iterable("--device-write-bps", device_write_bps)
        full_cmd.add_args_iterable("--device-write-iops", device_write_iops)

        if content_trust:
            full_cmd += ["--disable-content-trust", "false"]

        full_cmd.add_args_iterable("--dns", dns)
        full_cmd.add_args_iterable("--dns-option", dns_options)
        full_cmd.add_args_iterable("--dns-search", dns_search)
        full_cmd.add_simple_arg("--domainname", domainname)

        full_cmd.add_simple_arg("--entrypoint", entrypoint)

        full_cmd.add_args_mapping("--env", envs)
        full_cmd.add_args_iterable_or_single("--env-file", env_files)
        full_cmd.add_flag("--env-host", env_host)

        full_cmd.add_args_iterable_or_single("--expose", expose)

        full_cmd.add_simple_arg("--gpus", gpus)

        full_cmd.add_args_iterable_or_single("--group-add", groups_add)

        full_cmd.add_flag("--no-healthcheck", not healthcheck)
        full_cmd.add_simple_arg("--health-cmd", health_cmd)
        full_cmd.add_simple_arg("--health-interval", to_seconds(health_interval))
        full_cmd.add_simple_arg("--health-retries", health_retries)
        full_cmd.add_simple_arg(
            "--health-start-period", to_seconds(health_start_period)
        )
        full_cmd.add_simple_arg("--health-timeout", to_seconds(health_timeout))

        full_cmd.add_simple_arg("--hostname", hostname)

        full_cmd.add_flag("--init", init)

        full_cmd.add_flag("--interactive", interactive)

        full_cmd.add_simple_arg("--ip", ip)
        full_cmd.add_simple_arg("--ip6", ip6)
        full_cmd.add_simple_arg("--ipc", ipc)

        full_cmd.add_simple_arg("--isolation", isolation)
        full_cmd.add_simple_arg("--kernel-memory", kernel_memory)

        full_cmd.add_args_mapping("--label", labels)
        full_cmd.add_args_iterable("--label-file", label_files)

        full_cmd.add_args_iterable("--link", link)
        full_cmd.add_args_iterable("--link-local-ip", link_local_ip)

        full_cmd.add_simple_arg("--log-driver", log_driver)
        full_cmd.add_args_iterable("--log-opt", log_options)

        full_cmd.add_simple_arg("--mac-address", mac_address)

        full_cmd.add_simple_arg("--memory", memory)
        full_cmd.add_simple_arg("--memory-reservation", memory_reservation)
        full_cmd.add_simple_arg("--memory-swap", memory_swap)
        full_cmd.add_simple_arg("--memory-swappiness", memory_swappiness)

        full_cmd.add_args_iterable("--mount", (",".join(x) for x in mounts))
        full_cmd.add_simple_arg("--name", name)

        full_cmd.add_args_iterable("--network", networks)
        full_cmd.add_args_iterable("--network-alias", network_aliases)

        full_cmd.add_flag("--oom-kill-disable", not oom_kill)
        full_cmd.add_simple_arg("--oom-score-adj", oom_score_adj)

        full_cmd.add_simple_arg("--pid", pid)
        full_cmd.add_simple_arg("--pids-limit", pids_limit)

        full_cmd.add_simple_arg("--platform", platform)
        full_cmd.add_simple_arg("--pod", pod)
        full_cmd.add_simple_arg("--preserve-fds", preserve_fds)
        full_cmd.add_flag("--privileged", privileged)

        full_cmd.add_args_iterable("-p", (format_port_arg(p) for p in publish))
        full_cmd.add_flag("--publish-all", publish_all)

        if pull == "never":
            full_cmd.add_simple_arg("--pull", "never")

        full_cmd.add_flag("--read-only", read_only)
        full_cmd.add_simple_arg("--restart", restart)
        full_cmd.add_flag("--rm", remove)

        full_cmd.add_simple_arg("--runtime", runtime)
        full_cmd.add_args_iterable("--security-opt", security_options)

        full_cmd.add_flag("--service-ports", service_ports)

        full_cmd.add_simple_arg("--shm-size", shm_size)
        if sig_proxy is False:
            full_cmd += ["--sig-proxy", "false"]

        full_cmd.add_simple_arg("--stop-signal", format_signal_arg(stop_signal))
        full_cmd.add_simple_arg("--stop-timeout", stop_timeout)

        full_cmd.add_args_iterable("--storage-opt", storage_options)
        full_cmd.add_args_mapping("--sysctl", sysctl)
        full_cmd.add_simple_arg("--systemd", systemd)
        full_cmd.add_args_iterable("--tmpfs", tmpfs)
        full_cmd.add_flag("--tty", tty)
        full_cmd.add_simple_arg("--tz", tz)
        full_cmd.add_args_iterable("--ulimit", ulimit)

        full_cmd.add_simple_arg("--user", user)
        full_cmd.add_simple_arg("--userns", userns)
        full_cmd.add_simple_arg("--uts", uts)

        for volume_definition in volumes:
            volume_definition = tuple(str(x) for x in volume_definition)
            full_cmd += ["--volume", ":".join(volume_definition)]
        full_cmd.add_simple_arg("--volume-driver", volume_driver)
        full_cmd.add_args_iterable("--volumes-from", volumes_from)

        full_cmd.add_simple_arg("--workdir", workdir)

        full_cmd.append(image)
        full_cmd.extend(command)

        if detach and stream:
            raise ValueError(
                "It's not possible to stream and detach a container at the same time."
            )

        pass_fds = range(3, 3 + preserve_fds) if preserve_fds is not None else ()
        if detach:
            return Container(self.client_config, run(full_cmd, pass_fds=pass_fds))
        elif stream:
            return stream_stdout_and_stderr(full_cmd, pass_fds=pass_fds)
        else:
            return run(full_cmd, tty=tty, capture_stderr=False, pass_fds=pass_fds)

    @overload
    def start(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        attach: Literal[False] = False,
        interactive: bool = False,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> None: ...

    @overload
    def start(
        self,
        containers: ValidContainer,
        attach: Literal[True],
        interactive: bool = False,
        stream: Literal[False] = False,
        detach_keys: str | None = None,
    ) -> str: ...

    @overload
    def start(
        self,
        containers: ValidContainer,
        attach: Literal[True],
        interactive: bool = False,
        *,
        stream: Literal[True],
        detach_keys: str | None = None,
    ) -> ProcessStream: ...

    @overload
    def start(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        attach: bool = False,
        interactive: bool = False,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> str | ProcessStream | None: ...

    def start(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        attach: bool = False,
        interactive: bool = False,
        stream: bool = False,
        detach_keys: str | None = None,
    ) -> str | ProcessStream | None:
        """Starts one or more created/stopped containers.

        Aliases: `docker.start`, `docker.container.start`,
        `pydocker.Container.start`.

        Parameters:
            containers: One or a list of containers.
            attach: Attach stdout/stderr and forward signals.
            interactive: Attach stdin (ensure it is open).
            stream: Stream output as a generator.
            detach_keys: Override the key sequence for detaching a container.
        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if container_list == []:
            # nothing to do
            return
        if attach and len(container_list) > 1:
            raise ValueError("Attaching multiple containers on start is not supported.")
        if not attach and stream:
            raise ValueError(
                "It's not possible to stream stderr and stdout if the client isn't "
                "attached to the container. Please set `attach=True`."
            )
        full_cmd = Command([*self.docker_cmd, "container", "start"])
        full_cmd.add_flag("--attach", attach)
        full_cmd.add_simple_arg("--detach-keys", detach_keys)
        full_cmd.add_flag("--interactive", interactive)
        full_cmd += container_list

        if stream:
            return stream_stdout_and_stderr(full_cmd)
        elif attach:
            return run(full_cmd)
        else:
            run(full_cmd)

    def stats(
        self,
        containers: ValidContainer | Iterable[ValidContainer] | None = None,
        all: bool = False,
    ) -> builtins.list[ContainerStats]:
        """Get containers resource usage statistics

        Alias: `docker.stats(...)`

        Usage:
        ```python
        from pydocker import docker

        docker.run("redis", detach=True)
        print(docker.stats())
        # [<<class 'pydocker.components.container.ContainerStats'> object,
        # attributes are block_read=0, block_write=0, cpu_percentage=0.08,
        # container=e90ae41a5b17,
        # container_id=e90ae41a5b17df998584141692f1e361c485e8d00c37ee21fdc360d3523dd1c1,
        # memory_percentage=0.18, memory_used=11198791, memory_limit=6233071288,
        # container_name=crazy_northcutt, net_upload=696, net_download=0>]
        ```

        The data unit is the byte.

        Parameters:
            all: Get the stats of all containers, not just running ones.
            containers: One or a list of containers.

        # Returns
            A `List[pydocker.ContainerStats]`.
        """
        containers_list = to_list(containers) if containers else []
        if len(containers_list) == 0 and containers is not None and not all:
            return []

        full_cmd = Command(
            [
                *self.docker_cmd,
                "container",
                "stats",
                "--format",
                "{{json .}}",
                "--no-stream",
                "--no-trunc",
            ]
        )
        full_cmd.add_flag("--all", all)
        if containers:
            full_cmd.extend(containers_list)

        stats_output = run(full_cmd)
        return [ContainerStats(json.loads(x)) for x in stats_output.splitlines()]

    def stop(
        self,
        containers: ValidContainer | Iterable[ValidContainer],
        time: int | timedelta | None = None,
    ) -> None:
        """Stops one or more running containers

        Alias: `docker.stop(...)`

        Aliases: `docker.stop`, `docker.container.stop`,
        `pydocker.Container.stop`.

        Parameters:
            containers: One or a list of containers.
            time: Seconds to wait for stop before killing a container (default 10)

        # Raises
            `pydocker.exceptions.NoSuchContainer` if any of the
            containers do not exist.
        """
        container_list = (
            [containers]
            if isinstance(containers, str | Container)
            else list(containers)
        )
        if container_list == []:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "container", "stop"])
        full_cmd.add_simple_arg(
            "--time", format_time_arg(time) if isinstance(time, timedelta) else time
        )
        full_cmd.extend(container_list)

        run(full_cmd)

    @overload
    def top(
        self,
        container: ValidContainer,
        ps_args: Sequence[str] = (),
        *,
        raw: Literal[False] = False,
    ) -> ContainerTopResult: ...

    @overload
    def top(
        self,
        container: ValidContainer,
        ps_args: Sequence[str] = (),
        *,
        raw: Literal[True],
    ) -> str: ...

    @overload
    def top(
        self, container: ValidContainer, ps_args: Sequence[str] = (), *, raw: bool
    ) -> ContainerTopResult | str: ...

    def top(
        self,
        container: ValidContainer,
        ps_args: Sequence[str] = (),
        *,
        raw: bool = False,
    ) -> ContainerTopResult | str:
        """Get a container's running processes without changing its state.

        Alias: `docker.top(...)`.

        Parameters:
            container: Container name, id, or Container object.
            ps_args: Docker ps option tokens, forwarded after the container.
                Defaults to the Engine's ps selection (normally -ef).
            raw: Return the output from the existing CLI execution path as text
                instead of parsing it. Required for ambiguous field selections.

        Returns:
            ContainerTopResult with original headings, ordered string rows, and
            raw_output. Supported selections have scalar fields and optionally
            one final command/free-text field. No fixed process schema is imposed.

        Raises:
            TopParseError: Unsupported or malformed parsed layout; use raw=True.
            NoSuchContainer: The container does not exist.
        """
        output = run([*self.docker_cmd, "container", "top", container, *ps_args])
        if raw:
            return output
        table = parse_table(output, docker_top_fields(ps_args))
        return ContainerTopResult(table.headings, table.rows, output)

    def unpause(self, x: ValidContainer | Iterable[ValidContainer], /) -> None:
        """Unpause all processes within one or more containers

        Alias: `docker.unpause(...)`

        Parameters:
            x: One or more containers (name, id or `pydocker.Container` object).

        # Raises
            `pydocker.exceptions.NoSuchContainer` if any of the
            containers do not exist.
        """
        containers = [x] if isinstance(x, str | Container) else list(x)
        if len(containers) == 0:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "container", "unpause"])
        full_cmd.extend(containers)
        run(full_cmd)

    def update(
        self,
        x: ValidContainer | Iterable[ValidContainer],
        /,
        *,
        blkio_weight: int | None = None,
        cpu_period: int | None = None,
        cpu_quota: int | None = None,
        cpu_rt_period: int | None = None,
        cpu_rt_runtime: int | None = None,
        cpu_shares: int | None = None,
        cpus: float | None = None,
        cpuset_cpus: str | None = None,
        cpuset_mems: str | None = None,
        kernel_memory: int | str | None = None,
        memory: int | str | None = None,
        memory_reservation: int | str | None = None,
        memory_swap: int | str | None = None,
        pids_limit: int | None = None,
        restart: str | None = None,
    ) -> None:
        """Update configuration of one or more containers

        Alias: `docker.update(...)`

        Parameters:
            x: One or a list of containers to update.
            blkio_weight: Block IO (relative weight), between 10 and 1000,
                or 0 to disable (default 0)
            cpu_period: Limit CPU CFS (Completely Fair Scheduler) period
            cpu_quota: Limit CPU CFS (Completely Fair Scheduler) quota
            cpu_rt_period: Limit CPU real-time period in microseconds
            cpu_rt_runtime: Limit CPU real-time runtime in microseconds
            cpu_shares: CPU shares (relative weight)
            cpus: The maximal amount of cpu the container can use.
                `1` means one cpu core.
            cpuset_cpus: CPUs in which to allow execution, as a cpuset string
                understood by the docker CLI (e.g. `"0-3"`, `"0,2"`,
                `"0-3,7"`).
            cpuset_mems: MEMs in which to allow execution, as a cpuset string
                (same format as `cpuset_cpus`).
            memory:  Memory limit, valid values are `1024` (ints are bytes) or
                `"43m"` or `"6g"`.
            memory_reservation: Memory soft limit
            memory_swap: Swap limit equal to memory plus swap: '-1'
                to enable unlimited swap.
            pids_limit: Tune container pids limit (set `-1` for unlimited)
            restart: Restart policy to apply when a container exits (default "no")

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        containers = [x] if isinstance(x, str | Container) else list(x)
        if containers == []:
            # nothing to do
            return
        full_cmd = Command([*self.docker_cmd, "container", "update"])
        full_cmd.add_simple_arg("--blkio-weight", blkio_weight)
        full_cmd.add_simple_arg("--cpu-period", cpu_period)
        full_cmd.add_simple_arg("--cpu-quota", cpu_quota)
        full_cmd.add_simple_arg("--cpu-rt-period", cpu_rt_period)
        full_cmd.add_simple_arg("--cpu-rt-runtime", cpu_rt_runtime)
        full_cmd.add_simple_arg("--cpu-shares", cpu_shares)
        full_cmd.add_simple_arg("--cpus", cpus)
        full_cmd.add_simple_arg("--cpuset-cpus", cpuset_cpus)
        full_cmd.add_simple_arg("--cpuset-mems", cpuset_mems)
        full_cmd.add_simple_arg("--kernel-memory", kernel_memory)
        full_cmd.add_simple_arg("--memory", memory)
        full_cmd.add_simple_arg("--memory-reservation", memory_reservation)
        full_cmd.add_simple_arg("--memory-swap", memory_swap)
        full_cmd.add_simple_arg("--pids-limit", pids_limit)
        full_cmd.add_simple_arg("--restart", restart)
        full_cmd.extend(containers)
        run(full_cmd)

    @overload
    def wait(self, x: ValidContainer) -> int: ...

    @overload
    def wait(self, x: Iterable[ValidContainer]) -> builtins.list[int]: ...

    def wait(
        self, x: ValidContainer | Iterable[ValidContainer]
    ) -> int | builtins.list[int]:
        """Block until one or more containers stop, then returns their exit codes

        Alias: `docker.wait(...)`


        Parameters:
            x: One or a list of containers to wait for.

        # Returns
            An `int` if a single container was passed as argument or a list of ints
            if multiple containers were passed as arguments.

        Some Examples:

        ```python
        cont = docker.run("ubuntu", ["bash", "-c", "sleep 2 && exit 8"], detach=True)

        exit_code = docker.wait(cont)

        print(exit_code)
        # 8
        docker.container.remove(cont)
        ```

        ```python
        cont_1 = docker.run("ubuntu", ["bash", "-c", "sleep 4 && exit 8"], detach=True)
        cont_2 = docker.run("ubuntu", ["bash", "-c", "sleep 2 && exit 10"], detach=True)

        exit_codes = docker.wait([cont_1, cont_2])

        print(exit_codes)
        # [8, 10]
        docker.container.remove([cont_1, cont_2])
        ```

        # Raises
            `pydocker.exceptions.NoSuchContainer` if the container does not exists.
        """
        containers = to_list(x)
        if len(containers) == 0:
            # nothing to do
            return []

        full_cmd = Command([*self.docker_cmd, "container", "wait", *containers])
        if isinstance(x, Iterable) and not isinstance(x, str):
            exit_codes = run(full_cmd)
            return [int(exit_code) for exit_code in exit_codes.splitlines()]
        else:
            return int(run(full_cmd))


class ContainerStats:
    def __init__(self, json_dict: dict[str, Any]) -> None:
        """Takes a json_dict with container stats from the CLI and
        parses it.
        """
        self.block_read: int = custom_parse_object_as(
            pydantic.ByteSize, json_dict["BlockIO"].split("/")[0]
        )
        self.block_write: int = custom_parse_object_as(
            pydantic.ByteSize, json_dict["BlockIO"].split("/")[1]
        )
        self.cpu_percentage: float = float(json_dict["CPUPerc"][:-1])
        self.container: str = json_dict["Container"]
        self.container_id: str = json_dict["ID"]
        self.memory_percentage: float = float(json_dict["MemPerc"][:-1])
        self.memory_used: int = custom_parse_object_as(
            pydantic.ByteSize, json_dict["MemUsage"].split("/")[0]
        )
        self.memory_limit: int = custom_parse_object_as(
            pydantic.ByteSize, json_dict["MemUsage"].split("/")[1]
        )
        self.container_name: str = json_dict["Name"]
        self.net_upload: int = custom_parse_object_as(
            pydantic.ByteSize, json_dict["NetIO"].split("/")[0]
        )
        self.net_download: int = custom_parse_object_as(
            pydantic.ByteSize, json_dict["NetIO"].split("/")[1]
        )

    @override
    def __repr__(self) -> str:
        attr = ", ".join(f"{key}={value}" for key, value in self.__dict__.items())
        return f"<{self.__class__} object, attributes are {attr}>"
