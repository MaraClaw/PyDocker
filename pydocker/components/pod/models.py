from collections.abc import Mapping
from datetime import datetime
from typing import Annotated

import pydantic
from pydantic import AliasChoices

from pydocker.components.container.models import PortBinding
from pydocker.utils import DockerCamelModel


class PodInfraConfig(DockerCamelModel):
    port_bindings: Mapping[str, list[PortBinding] | None] | None = None
    host_network: bool | None = None
    static_ip: str | None = None
    static_mac: str | None = None
    no_manage_resolv_conf: bool | None = None
    dns_server: list[str] | None = None
    dns_search: list[str] | None = None
    dns_option: list[str] | None = None
    no_manage_hosts: bool | None = None
    host_add: list[str] | None = None
    networks: list[str] | None = None
    network_options: list[str] | None = None
    pid_ns: str | None = None
    userns: str | None = None
    uts_ns: str | None = None


class PodContainer(DockerCamelModel):
    id: str | None = None
    name: str | None = None
    state: str | None = None


class PodInspectResult(DockerCamelModel):
    id: str | None = None
    name: str | None = None
    created: Annotated[
        datetime | None,
        pydantic.Field(validation_alias=AliasChoices("Created", "CreatedAt")),
    ] = None
    create_command: list[str] | None = None
    exit_policy: str | None = None
    state: str | None = None
    hostname: str | None = None
    labels: Mapping[str, str] | None = None
    create_cgroup: bool | None = None
    cgroup_parent: str | None = None
    cgroup_path: str | None = None
    create_infra: bool | None = None
    infra_container_id: str | None = None
    infra_config: PodInfraConfig | None = None
    shared_namespaces: list[str] | None = None
    num_containers: int | None = None
    containers: list[PodContainer] | None = None
