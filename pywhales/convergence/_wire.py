"""Preserve complete wire templates while validating required observation fields."""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from pywhales.client_config import ClientConfig, ParsingError
from pywhales.convergence._types import Failure, ServiceTarget
from pywhales.utils import run


class Version(BaseModel):
    model_config = ConfigDict(strict=True)
    index: int = Field(alias="Index", ge=0)


class Spec(BaseModel):
    model_config = ConfigDict(strict=True)
    mode: dict[str, JsonValue] = Field(alias="Mode")
    template: dict[str, JsonValue] = Field(alias="TaskTemplate")


class UpdateStatus(BaseModel):
    model_config = ConfigDict(strict=True)
    state: str = Field(alias="State")


class ServiceWire(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(alias="ID", min_length=1)
    version: Version = Field(alias="Version")
    spec: Spec = Field(alias="Spec")
    update: UpdateStatus | None = Field(default=None, alias="UpdateStatus")


class TaskStatus(BaseModel):
    model_config = ConfigDict(strict=True)
    state: str = Field(alias="State", min_length=1)


class TaskWire(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(alias="ID", min_length=1)
    service_id: str = Field(alias="ServiceID", min_length=1)
    slot: int | None = Field(default=None, alias="Slot")
    desired: str = Field(alias="DesiredState", min_length=1)
    status: TaskStatus = Field(alias="Status")
    spec: dict[str, JsonValue] | None = Field(default=None, alias="Spec")


def canonical(value: dict[str, JsonValue], *, template: bool = False) -> str:
    """Serialize a complete JSON object, normalizing only the default runtime."""
    normalized = dict(value)
    if template and normalized.get("Runtime") == "container":
        del normalized["Runtime"]
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def fingerprint(service: ServiceWire) -> str:
    """Fingerprint the complete mode and normalized task template."""
    template = canonical(service.spec.template, template=True)
    generation = canonical(
        {"Mode": service.spec.mode, "TaskTemplate": json.loads(template)}
    )
    return hashlib.sha256(generation.encode()).hexdigest()


def inspect_service(config: ClientConfig, service_id: str) -> ServiceWire:
    """Inspect exactly one immutable service ID."""
    payload = run([*config.docker_cmd, "service", "inspect", service_id])
    results = TypeAdapter(list[ServiceWire]).validate_json(payload)
    if len(results) != 1 or results[0].id != service_id:
        raise ParsingError("Service inspect identity mismatch")
    return results[0]


def inspect_task(config: ClientConfig, task_id: str) -> TaskWire:
    """Inspect exactly one listed immutable task ID."""
    payload = run([*config.docker_cmd, "inspect", "--type", "task", task_id])
    results = TypeAdapter(list[TaskWire]).validate_json(payload)
    if len(results) != 1 or results[0].id != task_id:
        raise ParsingError("Task inspect identity mismatch")
    return results[0]


def target_from_wire(
    config: ClientConfig, service: ServiceWire
) -> ServiceTarget | Failure:
    """Capture a supported generation, or reject an unsupported mode."""
    mode = service.spec.mode
    if set(mode) != {"Replicated"} or not isinstance(mode["Replicated"], dict):
        return Failure(reason="unsupported", service_id=service.id)
    replicated = mode["Replicated"]
    replicas = replicated.get("Replicas")
    if set(replicated) != {"Replicas"} or type(replicas) is not int or replicas < 0:
        return Failure(reason="unsupported", service_id=service.id)
    template = service.spec.template
    container = template.get("ContainerSpec")
    image = container.get("Image") if isinstance(container, dict) else None
    if not isinstance(image, str) or image == "":
        return Failure(reason="malformed", service_id=service.id)
    if template.get("Runtime", "container") != "container":
        return Failure(reason="unsupported", service_id=service.id)
    template_json = canonical(template, template=True)
    return ServiceTarget(
        client_config=config,
        service_id=service.id,
        version=service.version.index,
        fingerprint=fingerprint(service),
        template=template_json,
        replicas=replicas,
    )
