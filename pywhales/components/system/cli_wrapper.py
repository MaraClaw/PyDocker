import datetime
import json
from collections.abc import Mapping
from types import MappingProxyType

from pywhales.client_config import Command, DockerCLICaller
from pywhales.components.system.models import (
    DockerEvent,
    DockerItemsSummary,
    SystemInfo,
)
from pywhales.utils import (
    MappedProcessStream,
    format_mapping_for_cli,
    format_time_arg,
    run,
    stream_stdout_and_stderr,
)


class DiskFreeResult:
    def __init__(self, cli_stdout: str) -> None:
        docker_items = {}
        for line in cli_stdout.splitlines():
            docker_items_dict = json.loads(line)
            reclamable = docker_items_dict["Reclaimable"]
            docker_items_dict["Reclaimable"] = reclamable.split(" ")[0]
            if "%" in reclamable:
                docker_items_dict["ReclaimablePercent"] = reclamable.split(" ")[1][1:-2]
            else:
                docker_items_dict["ReclaimablePercent"] = "100"

            docker_items[docker_items_dict["Type"]] = docker_items_dict

        self.images: DockerItemsSummary
        self.images = DockerItemsSummary(**docker_items["Images"])
        self.containers: DockerItemsSummary
        self.containers = DockerItemsSummary(**docker_items["Containers"])
        self.volumes: DockerItemsSummary
        self.volumes = DockerItemsSummary(**docker_items["Local Volumes"])
        self.build_cache: DockerItemsSummary
        self.build_cache = DockerItemsSummary(**docker_items["Build Cache"])


class SystemCLI(DockerCLICaller):
    def disk_free(self) -> DiskFreeResult:
        """Give information about the disk usage of the Docker daemon.

        Returns a `pywhales.DiskFreeResult` object.

        ```python
        from pywhales import docker
        disk_free_result = docker.system.disk_free()
        print(disk_free_result.images.active)  #int
        print(disk_free_result.containers.reclaimable)  # int, number of bytes
        print(disk_free_result.volumes.reclaimable_percent)  # float
        print(disk_free_result.build_cache.total_count)  # int
        print(disk_free_result.build_cache.size)  # int, number of bytes
        ...
        ```
        Note that the number are not 100% accurate because the docker CLI
        doesn't provide the exact numbers.

        Maybe in a future implementation, we can provide exact numbers.

        Verbose mode is not yet implemented.
        """

        full_cmd = Command([*self.docker_cmd, "system", "df", "--format", "{{json .}}"])
        return DiskFreeResult(run(full_cmd))

    def events(
        self,
        since: datetime.datetime | datetime.timedelta | None = None,
        until: datetime.datetime | datetime.timedelta | None = None,
        filters: Mapping[str, str] = MappingProxyType({}),
        *,
        timeout: float | None = None,
    ) -> MappedProcessStream[DockerEvent]:
        """Return docker events information up to the current point in time.

        If `until` is not specified, then the iterator returned is infinite.
        Use the stream as a context manager or call `close()` when stopping
        early. A bare loop break does not release the local CLI process.
        For example

        ```python
        from pywhales import docker
        from datetime import datetime, timedelta


        with docker.system.events() as events:
            for event in events:
                print("new event!")
                print(event)

        for event in docker.system.events(until=datetime.now() - timedelta(seconds=30)):
            print("some past event")
            print(event)
            # this loop will end, unlike the previous one

        for event in docker.system.events(until=datetime.now() + timedelta(seconds=30)):
            print("some past event")
            print(event)
            # this loop will end in 30 seconds, even if there are no events at all

        events = list(docker.system.events(filters={"container": "mycontainer"}, until=datetime.now()))
        # the list of all events concerning the container "mycontainer"
        ```

        Parameters:
            since:  Show all events created since timestamp
            until: 	Stream events until this timestamp
            filters: See the [Docker documentation page about filtering
                ](https://docs.docker.com/engine/reference/commandline/events/#filtering).
            timeout: Positive finite seconds for the local events CLI, or `None`.
                Expiry raises `DockerTimeout` without stopping daemon resources or
                changing post-exit pipe draining.
        # Returns
            A closeable iterator yielding DockerEvent objects from stdout.

        [reference page for
        system events](https://docs.docker.com/engine/api/v1.40/#operation/SystemEvents)
        """
        full_cmd = Command(
            [
                *self.docker_cmd,
                "system",
                "events",
                "--format",
                "{{json .}}",
            ]
        )
        full_cmd.add_simple_arg("--since", format_time_arg(since))
        full_cmd.add_simple_arg("--until", format_time_arg(until))
        full_cmd.add_args_iterable_or_single(
            "--filter", format_mapping_for_cli(filters)
        )
        stream = stream_stdout_and_stderr(full_cmd, timeout=timeout)
        return MappedProcessStream(
            stream,
            (
                DockerEvent(**json.loads(content))
                for origin, content in stream
                if origin == "stdout"
            ),
        )

    def info(self) -> SystemInfo:
        """Returns diverse information about the Docker client and daemon.

        # Returns
            A `pywhales.SystemInfo` object

        As an example

        ```python
        from pywhales import docker

        info = docker.system.info()
        print(info.images)
        # 40
        print(info.plugins.volume)
        # ["local"}
        ...
        ```

        You can find all attributes available by looking up the [reference page for
        system info](https://docs.docker.com/engine/api/v1.40/#operation/SystemInfo).
        """
        full_cmd = Command(
            [*self.docker_cmd, "system", "info", "--format", "{{json .}}"]
        )
        return SystemInfo(**json.loads(run(full_cmd)))

    def prune(
        self,
        all: bool = False,
        volumes: bool = False,
        filters: Mapping[str, str] = MappingProxyType({}),
    ) -> None:
        """Remove unused docker data

        Parameters:
            all: Remove all unused images not just dangling ones
            volumes: Prune volumes
            filters: See the [Docker documentation page about filtering
                ](https://docs.docker.com/engine/reference/commandline/system_prune/#filtering).
                For example, `filters=dict(until="24h")`.
        """
        full_cmd = Command([*self.docker_cmd, "system", "prune", "--force"])
        full_cmd.add_flag("--all", all)
        full_cmd.add_flag("--volumes", volumes)
        full_cmd.add_args_iterable_or_single(
            "--filter", format_mapping_for_cli(filters)
        )
        run(full_cmd)
