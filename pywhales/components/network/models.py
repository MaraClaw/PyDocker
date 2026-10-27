from datetime import datetime

from pydantic import JsonValue

from pywhales.enums import ResourceScope
from pywhales.utils import DockerCamelModel


class NetworkIPAM(DockerCamelModel):
    driver: str | None = None
    config: list[dict[str, JsonValue]] | None = None
    options: dict[str, JsonValue] | None = None


class NetworkContainer(DockerCamelModel):
    name: str | None = None
    endpoint_id: str | None = None
    mac_address: str | None = None
    ipv4_address: str | None = None
    ipv6_address: str | None = None


class NetworkInspectResult(DockerCamelModel):
    name: str | None = None
    id: str | None = None
    created: datetime | None = None
    scope: ResourceScope | str | None = None
    driver: str | None = None
    enable_ipv6: bool | None = None
    ipam: NetworkIPAM | None = None
    internal: bool | None = None
    attachable: bool | None = None
    ingress: bool | None = None
    containers: dict[str, NetworkContainer] | None = None
    options: dict[str, JsonValue] | None = None
    labels: dict[str, str] | None = None
    config_from: dict[str, JsonValue] | None = None
    config_only: bool | None = None
