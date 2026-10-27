import datetime as dt

from pydantic import Field, JsonValue

from pywhales.utils import DockerCamelModel


class SecretSpec(DockerCamelModel):
    name: str | None = None
    labels: dict[str, JsonValue] | None = None


class SecretInspectResult(DockerCamelModel):
    id: str | None = Field(alias="ID")
    version: dict[str, JsonValue] | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    spec: SecretSpec | None = None
