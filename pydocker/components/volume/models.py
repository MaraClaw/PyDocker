from datetime import datetime
from pathlib import Path
from typing import Any

from pydocker.utils import DockerCamelModel


class VolumeInspectResult(DockerCamelModel):
    name: str | None = None
    driver: str | None = None
    mountpoint: Path | None = None
    created_at: datetime | None = None
    status: dict[str, Any] | None = None
    labels: dict[str, str] | None = None
    scope: str | None = None
    options: dict[str, str] | None = None
