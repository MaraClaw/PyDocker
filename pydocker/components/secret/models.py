import datetime as dt
from typing import Any

from pydantic import Field

from pydocker.utils import DockerCamelModel


class SecretSpec(DockerCamelModel):
    name: str | None = None
    labels: dict[str, Any] | None = None


class SecretInspectResult(DockerCamelModel):
    id: str | None = Field(alias="ID")
    version: dict[str, Any] | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    spec: SecretSpec | None = None
