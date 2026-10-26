from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from pywhales.utils import DockerCamelModel


class CPUMemoryQuotas(DockerCamelModel):
    nano_cpus: Annotated[int | None, Field(alias="NanoCPUs")] = None
    memory_bytes: int | None = None


class Resources(DockerCamelModel):
    limits: CPUMemoryQuotas | None = None
    reservations: CPUMemoryQuotas | None = None


class ContainerSpec(DockerCamelModel):
    image: str | None = None
    labels: dict[str, str] | None = None
    privileges: dict[str, str | bool | None] | None = None
    stop_grace_period: int | None = None
    isolation: str | None = None
    env: list[str] | None = None


class TaskTemplate(DockerCamelModel):
    container_spec: ContainerSpec | None = None
    resources: Resources | None = None


class ChangeConfig(DockerCamelModel):
    parallelism: int | None = None
    failure_action: str | None = None
    monitor: int | None = None
    max_failure_ratio: float | None = None
    order: str | None = None


class ServiceSpec(DockerCamelModel):
    name: str | None = None
    labels: dict[str, str] | None = None
    mode: dict[str, Any] | None = None
    update_config: ChangeConfig | None = None
    rollback_config: ChangeConfig | None = None
    task_template: TaskTemplate | None = None


class ServiceVersion(DockerCamelModel):
    index: int | None = None


class EndpointPortConfig(DockerCamelModel):
    name: str | None = None
    protocol: str | None = None
    target_port: int | None = None
    published_port: int | None = None
    publish_mode: str | None = None


class ServiceEndpointSpec(DockerCamelModel):
    mode: str | None = None
    ports: list[EndpointPortConfig] | None = None


class VirtualIP(DockerCamelModel):
    network_id: str | None = None
    addr: str | None = None


class ServiceEndpoint(DockerCamelModel):
    spec: ServiceEndpointSpec | None = None
    ports: list[EndpointPortConfig] | None = None
    virtual_ips: list[VirtualIP] | None = None


class ServiceUpdateStatus(DockerCamelModel):
    state: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    message: str | None = None


class ServiceInspectResult(DockerCamelModel):
    id: Annotated[str | None, Field(alias="ID")] = None
    version: ServiceVersion | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    spec: ServiceSpec | None = None
    previous_spec: ServiceSpec | None = None
    endpoint: ServiceEndpoint | None = None
    update_status: ServiceUpdateStatus | None = None
