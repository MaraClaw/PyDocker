"""Docker daemon information, rounded disk summaries, and owned event streams."""

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
    """Docker-reported disk usage grouped by resource kind.

    Byte totals are derived from the CLI's rounded values, not exact daemon counts.

    Attributes:
        images: Image usage and reclaimable space.
        containers: Container usage and reclaimable space.
        volumes: Local volume usage and reclaimable space.
        build_cache: Build-cache usage and reclaimable space.
    """

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
    """System commands using the configured CLI, daemon, and execution policy."""

    def disk_free(self) -> DiskFreeResult:
        """Return the selected Docker daemon's disk usage summaries.

        Returns:
            Usage for images, containers, local volumes, and build cache. Byte
            values reflect the CLI's rounding rather than exact daemon totals.
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
        """Start a closeable stream of events from the selected Docker daemon.

        The local CLI starts during this call, before iteration. Without until,
        it continues waiting for events until closed, failed, or timed out.
        Use the stream as a context manager or call `close()` when stopping
        early. A bare loop break does not release the local CLI process.
        Closing only cancels this local CLI, not daemon-managed resources.

        Args:
            since: Lower event time bound, or None to omit it.
            until: Upper event time bound, or None to keep the subscription open.
            filters: Docker event filter names and values; the default adds none.
            timeout: Positive finite seconds for the local CLI, or None for no
                deadline. Timing starts at launch and continues while iteration
                pauses. Expiry kills and reaps the CLI while preserving captured
                output; natural post-exit pipe draining is unchanged.

        Returns:
            An owned iterator mapping stdout JSON records to DockerEvent objects;
            stderr is not yielded as events. It inherits the client's stream
            buffer budget and retains transport ownership through mapping.

        Raises:
            ValueError: If timeout is a Boolean or is neither None nor a positive
                finite number of seconds.
            DockerTimeout: If the CLI exceeds its deadline, delivered during
                iteration or clean context exit.
            BufferError: If a record or retained stderr history exceeds the
                configured stream buffer budget.
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
        """Return Docker-reported client and daemon information.

        Returns:
            Parsed system information. Fields omitted by Docker remain None.
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
        """Remove unused Docker data without a confirmation prompt.

        Args:
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
