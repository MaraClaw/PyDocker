"""Typed Swarm config inspection snapshots, including encoded config data."""

from datetime import datetime
from typing import Annotated

import pydantic

from pywhales.utils import DockerCamelModel


class DockerObjectVersion(DockerCamelModel):
    index: int | None = None


class ConfigSpecDriver(DockerCamelModel):
    name: str | None = None
    options: dict[str, pydantic.JsonValue] | None = None


class ConfigSpec(DockerCamelModel):
    name: str | None = None
    labels: dict[str, str] | None = None
    data: str | None = None
    templating: ConfigSpecDriver | None = None


class ConfigInspectResult(DockerCamelModel):
    id: Annotated[str | None, pydantic.Field(alias="ID")] = None
    version: DockerObjectVersion | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    spec: ConfigSpec | None = None
