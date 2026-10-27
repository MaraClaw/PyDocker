"""Swarm service administration, task discovery, and owned service-log streams."""

from __future__ import annotations

import builtins
import json
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType, TracebackType
from typing import (
    Any,
    Literal,
    Self,
    assert_never,
    overload,
    override,
)

from pydantic import JsonValue

import pywhales.components.task.cli_wrapper
from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components._swarm_inspect import parse_swarm_inspect_results
from pywhales.components.container.cli_wrapper import to_seconds
from pywhales.components.service.models import (
    ServiceEndpoint,
    ServiceInspectResult,
    ServiceSpec,
    ServiceUpdateStatus,
    ServiceVersion,
)
from pywhales.enums import RestartConditionInput
from pywhales.exceptions import NoSuchService
from pywhales.utils import (
    ValidPath,
    custom_parse_object_as,
    format_mapping_for_cli,
    format_time_arg,
    run,
    stream_stdout_and_stderr,
    to_list,
)

type ServiceListFilter = (
    tuple[Literal["id"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["mode"], str]
    | tuple[Literal["name"], str]
)


class Service(ReloadableObjectFromJson[ServiceInspectResult]):
    """Reloadable Swarm service handle with context-exit removal.

    Cleanup is attempted even if the body raises. If cleanup also fails, both
    exceptions are preserved in an exception group.
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
        with resource_cleanup(exc_val, "Service body and cleanup both failed"):
            self.remove()

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "service", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> ServiceInspectResult:
        return ServiceInspectResult.model_validate(json_object)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def version(self) -> ServiceVersion | None:
        return self._get_inspect_result().version

    @property
    def created_at(self) -> datetime | None:
        return self._get_inspect_result().created_at

    @property
    def updated_at(self) -> datetime | None:
        return self._get_inspect_result().updated_at

    @property
    def spec(self) -> ServiceSpec | None:
        return self._get_inspect_result().spec

    @property
    def previous_spec(self) -> ServiceSpec | None:
        return self._get_inspect_result().previous_spec

    @property
    def endpoint(self) -> ServiceEndpoint | None:
        return self._get_inspect_result().endpoint

    @property
    def update_status(self) -> ServiceUpdateStatus | None:
        return self._get_inspect_result().update_status

    def ps(self) -> list[pywhales.components.task.cli_wrapper.Task]:
        """Return task handles associated with this service."""
        return ServiceCLI(self.client_config).ps(self)

    def remove(self) -> None:
        """Remove this service from the Swarm.

        A context manager attempts this removal even if its body raises.
        """
        ServiceCLI(self.client_config).remove(self)

    def scale(self, new_scale: int, detach: bool = False) -> None:
        """Change the scale of a service.

        See the [`docker.service.scale`](../sub-commands/service.md#scale) command for
        information about the arguments.
        """
        ServiceCLI(self.client_config).scale({self: new_scale}, detach=detach)

    def update(
        self,
        detach: bool = False,
        force: bool = False,
        image: str | None = None,
        with_registry_authentication: bool = False,
        quiet: bool = False,
        replicas: int | None = None,
    ) -> None:
        """Update this service.

        See the [`docker.service.update`](../sub-commands/service.md#update) command for
        information about the arguments.
        """
        ServiceCLI(self.client_config).update(
            self, detach, force, image, with_registry_authentication, quiet, replicas
        )

    def exists(self) -> bool:
        """Return whether this service can still be inspected in the Swarm."""
        return ServiceCLI(self.client_config).exists(self.id)


ValidService = str | Service


class ServiceCLI(DockerCLICaller):
    """Service commands requiring the configured CLI and a Swarm manager daemon."""

    def create(
        self,
        image: str,
        command: builtins.list[str] | None,
        cap_add: Sequence[str] = (),
        cap_drop: Sequence[str] = (),
        constraints: Sequence[str] = (),
        detach: bool = False,
        dns: Sequence[str] = (),
        dns_options: Sequence[str] = (),
        dns_search: Sequence[str] = (),
        endpoint_mode: str | None = None,
        entrypoint: str | None = None,
        envs: Mapping[str, str] = MappingProxyType({}),
        env_files: ValidPath | Sequence[ValidPath] = (),
        generic_resources: Sequence[str] = (),
        groups: Sequence[str] = (),
        healthcheck: bool = True,
        health_cmd: str | None = None,
        health_interval: int | timedelta | None = None,
        health_retries: int | None = None,
        health_start_period: int | timedelta | None = None,
        health_timeout: int | timedelta | None = None,
        hosts: Mapping[str, str] = MappingProxyType({}),
        hostname: str | None = None,
        init: bool = False,
        isolation: str | None = None,
        labels: Mapping[str, str] = MappingProxyType({}),
        limit_cpu: float | None = None,
        limit_memory: str | None = None,
        limit_pids: int | None = None,
        log_driver: str | None = None,
        network: str | None = None,
        restart_condition: RestartConditionInput | None = None,
        restart_max_attempts: int | None = None,
        secrets: builtins.list[dict[str, str]] | None = None,
        mounts: builtins.list[dict[str, str]] | None = None,
    ) -> Service:
        """Create a Docker Swarm service.

        This requires a Swarm manager and changes daemon-managed service state.
        Unless detach is True, Docker waits for service convergence.

        Args:
            image: The image to use as the base for the service.
            command: Container command arguments, or None to use the image default.
            cap_add: Linux capabilities to add.
            cap_drop: Linux capabilities to remove.
            constraints: Scheduling constraints to apply.
            detach: Return after submission instead of waiting for convergence.
            dns: Custom DNS servers.
            dns_options: DNS resolver options.
            dns_search: DNS search domains.
            endpoint_mode: Endpoint mode, or None to omit the option.
            entrypoint: Entrypoint override, or None to use the image default.
            envs: Environment variable values for service containers.
            env_files: Local files supplying container environment variables.
            generic_resources: Generic resource requirements for scheduling.
            groups: Supplementary container groups.
            healthcheck: Disable the image's health check when False.
            health_cmd: Health-check command, or None to omit the option.
            health_interval: Interval between health checks in seconds, or None
                to omit the option.
            health_retries: Failures before marking a container unhealthy.
                None omits the option.
            health_start_period: Health-check startup grace period in seconds,
                or None to omit the option.
            health_timeout: Health-check timeout in seconds, or None to omit it.
            hosts: Host entries used to build repeated --host options.
            hostname: Container hostname, or None to omit the option.
            init: Run an init process inside service containers when True.
            isolation: Container isolation mode, or None to omit the option.
            labels: Service metadata labels.
            limit_cpu: CPU limit, or None to omit the option.
            limit_memory: Memory limit with Docker-supported units, or None to omit it.
            limit_pids: PID limit, or None to omit the option.
            log_driver: Logging driver, or None to omit the option.
            network: Network attachment specification, or None to omit it.
            restart_condition: Restart policy condition, or None to omit it.
            restart_max_attempts: Maximum restart attempts, or None to omit the option.
            secrets: Secret option mappings, or None to add no secrets.
            mounts: Mount option mappings, or None to add no mounts.

        Returns:
            A handle to the created service without an initial inspect call.
        """
        full_cmd = Command([*self.docker_cmd, "service", "create", "--quiet"])

        full_cmd.add_args_iterable_or_single("--cap-add", cap_add)
        full_cmd.add_args_iterable_or_single("--cap-drop", cap_drop)
        full_cmd.add_args_iterable_or_single("--constraint", constraints)

        full_cmd.add_flag("--detach", detach)
        full_cmd.add_args_iterable_or_single("--dns", dns)
        full_cmd.add_args_iterable_or_single("--dns-option", dns_options)
        full_cmd.add_args_iterable_or_single("--dns-search", dns_search)
        full_cmd.add_simple_arg("--endpoint-mode", endpoint_mode)
        full_cmd.add_simple_arg("--entrypoint", entrypoint)

        full_cmd.add_args_iterable_or_single("--env", format_mapping_for_cli(envs))
        full_cmd.add_args_iterable_or_single("--env-file", env_files)

        full_cmd.add_args_iterable_or_single("--generic-resource", generic_resources)
        full_cmd.add_args_iterable_or_single("--group", groups)

        full_cmd.add_flag("--no-healthcheck", not healthcheck)
        full_cmd.add_simple_arg("--health-cmd", health_cmd)
        full_cmd.add_simple_arg("--health-interval", to_seconds(health_interval))
        full_cmd.add_simple_arg("--health-retries", health_retries)
        full_cmd.add_simple_arg(
            "--health-start-period", to_seconds(health_start_period)
        )
        full_cmd.add_simple_arg("--health-timeout", to_seconds(health_timeout))
        for key, value in hosts:
            full_cmd += ["--host", f"{key}:{value}"]

        full_cmd.add_simple_arg("--hostname", hostname)

        full_cmd.add_flag("--init", init)
        full_cmd.add_simple_arg("--isolation", isolation)
        full_cmd.add_args_iterable_or_single("--label", format_mapping_for_cli(labels))
        full_cmd.add_simple_arg("--limit-cpu", limit_cpu)
        full_cmd.add_simple_arg("--limit-memory", limit_memory)
        full_cmd.add_simple_arg("--limit-pids", limit_pids)
        full_cmd.add_simple_arg("--log-driver", log_driver)
        full_cmd.add_simple_arg("--restart-condition", restart_condition)
        full_cmd.add_simple_arg("--restart-max-attempts", restart_max_attempts)
        full_cmd.add_args_iterable_or_single(
            "--mount", [",".join(format_mapping_for_cli(s)) for s in mounts or []]
        )
        full_cmd.add_simple_arg("--network", network)
        full_cmd.add_args_iterable_or_single(
            "--secret", [",".join(format_mapping_for_cli(s)) for s in secrets or []]
        )

        full_cmd.append(image)
        if command is not None:
            for string in to_list(command):
                full_cmd.append(string)

        service_id = run(full_cmd)
        return Service(self.client_config, service_id, is_immutable_id=True)

    @overload
    def inspect(self, x: str) -> Service:
        pass

    @overload
    def inspect(self, x: builtins.list[str]) -> builtins.list[Service]: ...

    def inspect(self, x: str | builtins.list[str]) -> Service | builtins.list[Service]:
        """Inspect one or more Swarm services by name or ID.

        Args:
            x: One name or ID, or a list of references. An empty list returns
                without invoking the CLI.

        Returns:
            One handle for a string, otherwise handles in request order with
            duplicates retained as distinct handles. All snapshots are validated
            before any returned handle is hydrated.

        Raises:
            NoSuchService: If Docker reports that a requested service is absent.
            ParsingError: If response identities cannot be associated unambiguously
                with the requests or snapshots are inconsistent.
        """
        references = to_list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "service", "inspect", *references])
        parser = Service(self.client_config, references[0], is_immutable_id=True)
        snapshots = parse_swarm_inspect_results(parser, json_str, references)
        resources = []
        for identity, snapshot in snapshots:
            resource = Service(self.client_config, identity, is_immutable_id=True)
            resource._set_inspect_result(snapshot)
            resources.append(resource)
        match x:
            case str():
                return resources[0]
            case builtins.list():
                return resources
            case unreachable:
                assert_never(unreachable)

    def exists(self, x: str) -> bool:
        """Return whether a service can be inspected.

        Args:
            x: Service name or ID to inspect.

        Returns:
            False when Docker reports NoSuchService; True on successful inspection.
            Other inspection failures propagate.
        """
        try:
            self.inspect(x)
        except NoSuchService:
            return False
        else:
            return True

    @overload
    def logs(
        self,
        service: ValidService,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        follow: bool = False,
        raw: bool = False,
        task_ids: bool = True,
        resolve: bool = True,
        truncate: bool = True,
        stream: Literal[False] = False,
        *,
        timeout: float | None = None,
    ) -> str: ...

    @overload
    def logs(
        self,
        service: ValidService,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        follow: bool = False,
        raw: bool = False,
        task_ids: bool = True,
        resolve: bool = True,
        truncate: bool = True,
        *,
        stream: Literal[True],
        timeout: float | None = None,
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def logs(
        self,
        service: ValidService,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        follow: bool = False,
        raw: bool = False,
        task_ids: bool = True,
        resolve: bool = True,
        truncate: bool = True,
        stream: bool = False,
        *,
        timeout: float | None = None,
    ) -> str | Iterable[tuple[str, bytes]]: ...

    def logs(
        self,
        service: ValidService,
        details: bool = False,
        since: datetime | timedelta | None = None,
        tail: int | None = None,
        timestamps: bool = False,
        follow: bool = False,
        raw: bool = False,
        task_ids: bool = True,
        resolve: bool = True,
        truncate: bool = True,
        stream: bool = False,
        *,
        timeout: float | None = None,
    ) -> str | Iterable[tuple[str, bytes]]:
        """Return collected service logs or an owned byte stream.

        Service inspection runs first, outside the logs deadline. The logs CLI
        starts before this call returns, including in streaming mode. Collection
        blocks until the CLI exits and its pipes drain; follow=True can wait indefinitely
        even when stream=False. Closing a stream only cancels the local CLI,
        not the service. Exhaust the stream, use it as a context manager, or call
        close() when stopping early; a bare loop break does not release it.

        Args:
            service: Service name, ID, or handle to read logs from.
            details: Include additional log details.
            since: Lower time bound, or None to omit it.
            tail: Number of trailing lines; None leaves Docker's default of all lines.
            timestamps: Include timestamps with log records.
            follow: Keep following new log records instead of taking a snapshot.
            raw: Omit Docker's log formatting.
            task_ids: Include task IDs in formatted output.
            resolve: Resolve IDs to names in formatted output.
            truncate: Allow Docker to truncate IDs in formatted output.
            stream: Return a closeable stream when True; otherwise collect decoded
                stdout and stderr records into one string in arrival order.
            timeout: Positive finite seconds for the local logs CLI, or None for
                no deadline. Timing starts at launch and continues while iteration
                pauses. Expiry kills and reaps the local CLI, preserving captured
                output; natural post-exit pipe draining is unchanged.

        Returns:
            Collected text when stream=False; otherwise an owned stream of
            (source, content) records, with source equal to stdout or stderr and
            content containing bytes. It inherits the client's stream buffer budget.

        Raises:
            NoSuchService: If the service inspection reports that it is absent.
            ValueError: If timeout is a Boolean or is neither None nor a positive
                finite number of seconds.
            DockerTimeout: If the logs CLI exceeds its deadline, delivered during
                collection or stream consumption/clean context exit.
            BufferError: If a record or retained stderr history exceeds the
                configured stream buffer budget.
        """
        # first we verify that the service exists and raise an exception if not.
        self.inspect(str(service))

        full_cmd = Command([*self.docker_cmd, "service", "logs"])
        full_cmd.add_flag("--details", details)
        full_cmd.add_simple_arg("--since", format_time_arg(since))
        full_cmd.add_simple_arg("--tail", tail)
        full_cmd.add_flag("--timestamps", timestamps)
        full_cmd.add_flag("--follow", follow)
        full_cmd.add_flag("--raw", raw)
        full_cmd.add_flag("--no-task-ids", not task_ids)
        full_cmd.add_flag("--no-resolve", not resolve)
        full_cmd.add_flag("--no-trunc", not truncate)
        full_cmd.append(service)

        iterator = stream_stdout_and_stderr(full_cmd, timeout=timeout)
        if stream:
            return iterator
        else:
            with iterator:
                return "".join(x[1].decode() for x in iterator)

    def list(
        self, filters: Iterable[ServiceListFilter] | Mapping[str, Any] = ()
    ) -> builtins.list[Service]:
        """List services in the selected Swarm.

        Args:
            filters: Docker filter pairs; the default selects all services.
                Mapping inputs are deprecated.

        Returns:
            Service handles with full IDs. Nonempty results require a second CLI
            call to resolve the truncated IDs returned by service list.
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
        full_cmd = Command([*self.docker_cmd, "service", "list", "--quiet"])
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))

        ids_truncated = run(full_cmd).splitlines()

        # the ids are truncated because there is no single docker command that allows us to get them
        # untruncated. We must run an inspect command to get all untruncated ids.

        if ids_truncated == []:
            return []

        full_cmd = [
            *self.docker_cmd,
            "service",
            "inspect",
            *ids_truncated,
            "--format",
            "{{.ID}}",
        ]
        ids_not_truncated = run(full_cmd).splitlines()

        return [
            Service(self.client_config, x, is_immutable_id=True)
            for x in ids_not_truncated
        ]

    def ps(
        self, x: ValidService | Sequence[ValidService]
    ) -> builtins.list[pywhales.components.task.cli_wrapper.Task]:
        """List Swarm tasks associated with one or more services.

        Args:
            x: Service names, IDs, or handles to list tasks for.

        Returns:
            Task handles in CLI output order, including any task history Docker lists.

        Raises:
            NoSuchService: If Docker reports that a requested service is absent.
        """
        full_cmd = [
            *self.docker_cmd,
            "service",
            "ps",
            "--quiet",
            "--no-trunc",
            *to_list(x),
        ]
        ids = run(full_cmd).splitlines()
        return [
            pywhales.components.task.cli_wrapper.Task(
                self.client_config, id_, is_immutable_id=True
            )
            for id_ in ids
        ]

    def remove(self, services: ValidService | Sequence[ValidService]) -> None:
        """Remove one or more Swarm services.

        Args:
            services: Service names, IDs, or handles. An empty list is a no-op.

        Raises:
            NoSuchService: If Docker reports that a requested service is absent.
        """
        full_cmd = Command([*self.docker_cmd, "service", "remove"])

        if services == []:
            return

        for service in to_list(services):
            full_cmd.append(service)

        run(full_cmd)

    def rollback(self) -> None:
        """Reject service rollback because this operation is not implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError

    def scale(self, new_scales: dict[ValidService, int], detach: bool = False) -> None:
        """Scale one or more services.

        Args:
            new_scales: Mapping between services and the desired scales. For example
                use new_scales={"service1": 4, "service2": 8}.
            detach: Return after submission instead of waiting for convergence.

        Raises:
            NoSuchService: If inspection reports that a requested service is absent.
        """
        # verify that the services exists
        self.inspect([str(service) for service in new_scales])

        full_cmd = Command([*self.docker_cmd, "service", "scale"])
        full_cmd.add_flag("--detach", detach)
        for service, new_scale in new_scales.items():
            full_cmd.append(f"{service!s}={new_scale}")
        run(full_cmd, capture_stderr=False, capture_stdout=False)

    def update(
        self,
        service: ValidService,
        detach: bool = False,
        force: bool = False,
        image: str | None = None,
        with_registry_authentication: bool = False,
        quiet: bool = False,
        replicas: int | None = None,
    ) -> None:
        """Update a Swarm service.

        Args:
            service: The service to update
            detach: Exit immediately instead of waiting for the service to converge
            force: Force update even if no changes require it
            image: Service image reference, or None to leave it unchanged.
            with_registry_authentication: Send registry authentication details
                to Swarm agents.
            quiet: Request only the service ID from Docker.
            replicas: Desired replica count, or None to leave it unchanged.

        Raises:
            NoSuchService: If Docker reports that the service is absent.
        """
        full_cmd = Command([*self.docker_cmd, "service", "update"])
        full_cmd.add_flag("--force", force)
        full_cmd.add_simple_arg("--image", image)
        full_cmd.add_flag("--with-registry-auth", with_registry_authentication)
        full_cmd.add_flag("--detach", detach)
        full_cmd.add_flag("--quiet", quiet)
        full_cmd.add_simple_arg("--replicas", replicas)
        full_cmd.append(service)
        run(full_cmd, capture_stdout=False)
