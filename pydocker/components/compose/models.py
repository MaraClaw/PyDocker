from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field


class ServicePlacement(BaseModel):
    constraints: list[str] | None = None


class ResourcesLimits(BaseModel):
    cpus: float | None = None
    memory: int | None = None


class ResourcesReservation(BaseModel):
    cpus: float | int | None = None
    memory: int | None = None


class ServiceResources(BaseModel):
    limits: ResourcesLimits | None = None
    reservations: ResourcesReservation | None = None


class ServiceDeployConfig(BaseModel):
    labels: dict[str, str] | None = None
    resources: ServiceResources | None = None
    placement: ServicePlacement | None = None
    replicas: int | None = None


class DependencyCondition(BaseModel):
    condition: str | None = None


class ComposeServiceBuild(BaseModel):
    context: Path | None = None
    dockerfile: Path | None = None
    args: dict[str, Any] | None = None
    labels: dict[str, Any] | None = None


class ComposeServicePort(BaseModel):
    mode: str | None = None
    protocol: str | None = None
    published: int | None = None
    target: int | None = None


class ComposeServiceVolume(BaseModel):
    bind: dict[str, Any] | None = None
    source: str | None = None
    target: str | None = None
    type: str | None = None


class ComposeServiceNetwork(BaseModel):
    aliases: list[str] | None = None
    ipv4_address: str | None = None
    ipv6_address: str | None = None
    link_local_ips: list[str] | None = None
    mac_address: str | None = None
    driver_opts: dict[str, Any] | None = None
    gw_priority: int | None = None
    priority: int | None = None


class ComposeConfigService(BaseModel):
    deploy: ServiceDeployConfig | None = None
    blkio_config: Any | None = None
    cpu_count: float | None = None
    cpu_percent: float | None = None
    cpu_shares: int | None = None
    cpuset: str | None = None
    build: ComposeServiceBuild | None = None
    cap_add: Annotated[list[str] | None, Field(default_factory=list)]
    cap_drop: Annotated[list[str] | None, Field(default_factory=list)]
    cgroup_parent: str | None = None
    command: list[str] | str | None = None
    configs: Any = None
    container_name: str | None = None
    depends_on: Annotated[dict[str, DependencyCondition], Field(default_factory=dict)]
    device_cgroup_rules: Annotated[list[str], Field(default_factory=list)]
    devices: Any = None
    environment: dict[str, str | int | None] | list[str] | None = None
    expose: Annotated[list[int] | list[str], Field(default_factory=list)]
    entrypoint: list[str] | str | None = None
    hostname: str | None = None
    image: str | None = None
    labels: Annotated[dict[str, str] | None, Field(default_factory=dict)]
    ports: list[ComposeServicePort] | None = None
    volumes: list[ComposeServiceVolume] | None = None
    networks: dict[str, ComposeServiceNetwork | None] | None = None


class ComposeConfigNetwork(BaseModel):
    driver: str | None = None
    name: str | None = None
    external: bool | None = False
    driver_opts: dict[str, Any] | None = None
    attachable: bool | None = None
    enable_ipv6: bool | None = None
    ipam: Any = None
    internal: bool | None = None
    labels: Annotated[dict[str, str], Field(default_factory=dict)]


class ComposeConfigVolume(BaseModel):
    driver: str | None = None
    driver_opts: dict[str, Any] | None = None
    external: bool | None = None
    labels: Annotated[dict[str, str] | None, Field(default_factory=dict)]
    name: str | None = None


class ComposeConfig(BaseModel):
    services: dict[str, ComposeConfigService] | None = None
    networks: Annotated[
        dict[str, ComposeConfigNetwork] | None, Field(default_factory=dict)
    ]
    volumes: Annotated[
        dict[str, ComposeConfigVolume] | None, Field(default_factory=dict)
    ]
    configs: Any = None
    secrets: Any = None


class ComposeProject(BaseModel):
    name: str | None
    created: int | None = 0
    running: int | None = 0
    restarting: int | None = 0
    exited: int | None = 0
    paused: int | None = 0
    dead: int | None = 0
    config_files: list[Path] | None = None


class ComposeEvent(BaseModel):
    action: Annotated[str | None, Field(alias="action")] = None
    attributes: Annotated[dict[str, str] | None, Field(alias="attributes")] = None
    id: Annotated[str | None, Field(alias="id")] = None
    service: Annotated[str | None, Field(alias="service")] = None
    time: Annotated[datetime | None, Field(alias="time")] = None
    type: Annotated[str | None, Field(alias="type")] = None
