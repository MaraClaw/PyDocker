"""Volume inspection snapshots whose mount paths refer to the selected daemon."""

from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from pywhales.enums import ResourceScope
from pywhales.utils import DockerCamelModel


class VolumeInspectResult(DockerCamelModel):
    name: str | None = None
    driver: str | None = None
    mountpoint: Path | None = None
    created_at: datetime | None = None
    status: dict[str, JsonValue] | None = None
    labels: dict[str, str] | None = None
    scope: ResourceScope | str | None = None
    options: dict[str, str] | None = None
