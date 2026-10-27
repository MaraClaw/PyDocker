"""Endpoint, topology and asset ownership declarations; never network probes."""

from ipaddress import IPv4Network, IPv6Network
from typing import Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from .common import (
    Amount,
    Contract,
    Count,
    Digest,
    Nonempty,
    Positive,
    PositiveCount,
    Text,
    Token,
)

type Orchestrator = Literal["standalone", "compose", "swarm"]


class Endpoint(Contract):
    """Effective daemon identity and private connection reference, not a URI."""

    id: Token
    connection_ref: Token
    access: Literal["local", "ssh", "tls"]
    cpu_cores: Positive
    memory_bytes: PositiveCount
    disk_bytes: PositiveCount
    storage_driver: Text
    filesystem: Text
    registry_path: Text


class Alias(Contract):
    """Explicit public alias to one effective endpoint, including shared aliases."""

    name: Token
    effective_endpoint: Token


class Platform(Contract):
    """Endpoint inventory and declared orchestration mode."""

    endpoints: Nonempty[Endpoint]
    aliases: Nonempty[Alias]
    node_count: PositiveCount
    orchestrator: Orchestrator
    mode: Literal["containers", "compose", "services", "stack"]
    required_features: Nonempty[Token]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Reject unknown/duplicate aliases and incompatible orchestration modes."""
        endpoint_ids = {endpoint.id for endpoint in self.endpoints}
        combinations = {
            ("standalone", "containers"),
            ("compose", "compose"),
            ("swarm", "services"),
            ("swarm", "stack"),
        }
        if (
            len(endpoint_ids) != len(self.endpoints)
            or len({alias.name for alias in self.aliases}) != len(self.aliases)
            or any(
                alias.effective_endpoint not in endpoint_ids for alias in self.aliases
            )
            or (self.orchestrator, self.mode) not in combinations
            or (self.orchestrator != "swarm" and self.node_count != 1)
        ):
            raise PydanticCustomError("endpoint_mode", "endpoint_mode")
        return self


class Mount(Contract):
    """Explicit mount allowlist with data access mode."""

    source_ref: Token
    destination: Text
    read_only: bool


class Topology(Contract):
    """Addressing, ingress, trust and host security responsibility."""

    network: Literal["bridge", "overlay"]
    ipv4: bool
    ipv6: bool
    ipv4_pools: tuple[IPv4Network, ...]
    ipv6_pools: tuple[IPv6Network, ...]
    mtu_bytes: PositiveCount
    dns_expectation: Text
    ingress: Literal["none", "host_publish", "routing_mesh"]
    trust: Literal["trusted_operators", "untrusted_tenants"]
    allowed_mounts: tuple[Mount, ...]
    security_controls: Nonempty[Text]
    security_owner: Text

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Require address families/pools to agree and pools not to overlap."""
        if (
            not (self.ipv4 or self.ipv6)
            or self.ipv4 != bool(self.ipv4_pools)
            or self.ipv6 != bool(self.ipv6_pools)
            or (self.ipv6 and self.mtu_bytes < 1280)
            or self.mtu_bytes > 65535
        ):
            raise PydanticCustomError("topology_bounds", "topology_bounds")
        for pools in (self.ipv4_pools, self.ipv6_pools):
            for index, pool in enumerate(pools):
                if any(pool.overlaps(other) for other in pools[index + 1 :]):
                    raise PydanticCustomError("topology_bounds", "topology_bounds")
        return self


class Asset(Contract):
    """Created/borrowed asset with persistence and explicit teardown authority."""

    id: Token
    kind: Literal["container", "service", "image", "volume", "network", "cache", "log"]
    ownership: Literal["created", "borrowed"]
    data: Literal["persistent", "ephemeral"]
    owner: Text
    teardown: Literal["retain", "remove_owned"]
    backup_obligation: Text
    restore_obligation: Text

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Forbid removing borrowed assets or unprotected persistent data."""
        if self.teardown == "remove_owned" and (
            self.ownership == "borrowed" or self.data == "persistent"
        ):
            raise PydanticCustomError("asset_teardown", "asset_teardown")
        return self


class Ownership(Contract):
    """Asset ledger, rollback protection and bounded artifact retention."""

    assets: Nonempty[Asset]
    protected_rollback_images: Nonempty[Digest]
    logs_retention_seconds: Amount
    cache_retention_seconds: Amount
    report_retention_seconds: Amount
    teardown_owner: Text
    teardown_timeout_seconds: Positive
    expected_residual_assets: tuple[Token, ...]
    backup_restore_owner: Text
    maximum_created_assets: Count

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Reject a created-asset ledger exceeding its declared cap."""
        if (
            sum(asset.ownership == "created" for asset in self.assets)
            > self.maximum_created_assets
        ):
            raise PydanticCustomError("asset_cap", "asset_cap")
        return self
