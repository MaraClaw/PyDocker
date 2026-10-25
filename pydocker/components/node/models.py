from datetime import datetime
from typing import Annotated

from pydantic import Field

from pydocker.utils import DockerCamelModel


class NodeVersion(DockerCamelModel):
    index: int | None = None


class NodeSpec(DockerCamelModel):
    name: str | None = None
    labels: dict[str, str] | None = None
    role: str | None = None
    availability: str | None = None


class NodePlatform(DockerCamelModel):
    architecture: str | None = None
    os: Annotated[str | None, Field(alias="OS")] = None


class NodeNamedResourceSpec(DockerCamelModel):
    kind: str | None = None
    value: str | None = None


class NodeDiscreteResourceSpec(DockerCamelModel):
    kind: str | None = None
    value: int | None = None


class NodeGenericResource(DockerCamelModel):
    named_resource_spec: NodeNamedResourceSpec | None = None
    discrete_resource_spec: NodeDiscreteResourceSpec | None = None


class NodeResource(DockerCamelModel):
    nano_cpus: Annotated[int | None, Field(alias="NanoCPUs")] = None
    memory_bytes: int | None = None
    generic_resources: list[NodeGenericResource] | None = None


class EnginePlugin(DockerCamelModel):
    type: str | None = None
    name: str | None = None


class NodeEngine(DockerCamelModel):
    engine_version: str | None = None
    labels: dict[str, str] | None = None
    plugins: list[EnginePlugin] | None = None


class NodeTLSInfo(DockerCamelModel):
    trust_root: str | None = None
    cert_issuer_subject: str | None = None
    cert_issuer_public_key: str | None = None


class NodeDescription(DockerCamelModel):
    hostname: str | None = None
    platform: NodePlatform | None = None
    resources: NodeResource | None = None
    engine: NodeEngine | None = None
    tls_info: NodeTLSInfo | None = None


class NodeStatus(DockerCamelModel):
    state: str | None = None
    message: str | None = None
    addr: str | None = None


class NodeManagerStatus(DockerCamelModel):
    leader: bool | None = None
    reachability: str | None = None
    addr: str | None = None


class NodeInspectResult(DockerCamelModel):
    id: Annotated[str | None, Field(alias="ID")] = None
    version: NodeVersion | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    spec: NodeSpec | None = None
    description: NodeDescription | None = None
    status: NodeStatus | None = None
    manager_status: NodeManagerStatus | None = None
