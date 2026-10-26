from datetime import datetime
from typing import Annotated, Any

import pydantic

from pywhales.utils import DockerCamelModel


class ObjectVersion(DockerCamelModel):
    index: int | None = None


class NamedResourceSpec(DockerCamelModel):
    kind: str | None = None
    value: str | None = None


class DiscreteResourceSpec(DockerCamelModel):
    kind: str | None = None
    value: int | None = None


class AssignedGenericResources(DockerCamelModel):
    named_resource_spec: NamedResourceSpec | None = None
    discrete_resource_spec: DiscreteResourceSpec | None = None


class ContainerStatus(DockerCamelModel):
    container_id: Annotated[str | None, pydantic.Field(alias="ContainerID")] = None
    pid: Annotated[int | None, pydantic.Field(alias="PID")] = None
    exit_code: int | None = None


class TaskStatus(DockerCamelModel):
    timestamp: datetime | None = None
    state: str | None = None
    message: str | None = None
    err: str | None = None
    container_status: ContainerStatus | None = None


class LogDriver(DockerCamelModel):
    name: str | None = None
    options: dict[str, str] | None = None


class NetworkAttachmentConfig(DockerCamelModel):
    target: str | None = None
    aliases: list[str] | None = None
    driver_opts: dict[str, str] | None = None


class Platform(DockerCamelModel):
    architecture: str | None = None
    os: Annotated[str | None, pydantic.Field(alias="OS")] = None


class Spread(DockerCamelModel):
    spread_descriptor: str | None = None


class Placement(DockerCamelModel):
    constraints: list[str] | None = None
    preferences: list[Spread] | None = None
    max_replicas: int | None = None
    platforms: list[Platform] | None = None


class RestartPolicy(DockerCamelModel):
    condition: str | None = None
    delay: int | None = None
    max_attempts: int | None = None
    window: int | None = None


class PluginPrivilege(DockerCamelModel):
    name: str | None = None
    description: str | None = None
    value: list[str] | None = None


class PluginSpec(DockerCamelModel):
    name: str | None = None
    remote: str | None = None
    disabled: bool | None = None
    plugin_privilege: list[PluginPrivilege] | None = None


class ContainerSpec(DockerCamelModel):
    image: str | None = None
    labels: dict[str, str] | None = None
    command: list[str] | None = None
    args: list[str] | None = None
    hostname: str | None = None
    env: list[str] | None = None
    dir: str | None = None
    user: str | None = None
    groups: list[str] | None = None
    privileges: Any = None
    tty: Annotated[bool | None, pydantic.Field(alias="TTY")] = None
    open_stdin: bool | None = None
    read_only: bool | None = None
    mounts: list[Any] | None = None
    stop_signal: str | None = None
    stop_grace_period: int | None = None
    health_check: Any = None
    hosts: list[str] | None = None
    dns_config: Any = None
    secrets: list[Any] | None = None
    configs: list[Any] | None = None
    isolation: str | None = None
    init: bool | None = None
    sysctls: Any | None = None


class NetworkAttachmentSpec(DockerCamelModel):
    container_id: Annotated[str | None, pydantic.Field(alias="ContainerID")] = None


class ResourceObject(DockerCamelModel):
    nano_cpus: Annotated[int | None, pydantic.Field(alias="NanoCPUs")] = None
    memory_bytes: int | None = None
    generic_resources: list[AssignedGenericResources] | None = None


class Resources(DockerCamelModel):
    limits: ResourceObject | None = None
    reservation: ResourceObject | None = None


class TaskSpec(DockerCamelModel):
    # TODO: set types for Any
    plugin_spec: PluginSpec | None = None
    container_spec: ContainerSpec | None = None
    network_attachment_spec: NetworkAttachmentSpec | None = None
    resources: Resources | None = None
    restart_policy: Any = None
    placement: Placement | None = None
    force_update: int | None = None
    runtime: str | None = None
    networks: list[NetworkAttachmentConfig] | None = None
    log_driver: LogDriver | None = None


class TaskInspectResult(DockerCamelModel):
    id: Annotated[str | None, pydantic.Field(alias="ID")] = None
    version: ObjectVersion | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    name: str | None = None
    labels: dict[str, str] | None = None
    spec: TaskSpec | None = None
    service_id: Annotated[str | None, pydantic.Field(alias="ServiceID")] = None
    slot: int | None = None
    node_id: Annotated[str | None, pydantic.Field(alias="NodeID")] = None
    assigned_generic_resources: list[AssignedGenericResources] | None = None
    status: TaskStatus | None = None
    desired_state: str | None = None
