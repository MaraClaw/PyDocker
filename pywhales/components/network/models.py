"""Typed network inspection snapshots, address pools, and attached containers."""

from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pywhales.enums import ResourceScope
from pywhales.utils import DockerCamelModel


class NetworkIPAMPool(BaseModel):
    """Creation-time IPAM pool; addresses are interpreted by Docker.

    Auxiliary mappings are revalidated when a network is created.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    subnet: str
    gateway: str | None = None
    ip_range: str | None = None
    auxiliary_addresses: Mapping[str, str] = Field(default_factory=dict)


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
