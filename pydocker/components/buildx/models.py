from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import Field

from pydocker.utils import DockerCamelModel


class BuilderNode(DockerCamelModel):
    name: str | None = None
    endpoint: str | None = None
    flags: list[str] | None = None
    status: str | None = None
    version: str | None = None
    ids: Annotated[list[str] | None, Field(alias="IDs")] = None
    platforms: list[str] | None = None
    labels: dict[str, str] | None = None


class BuilderInspectResult(DockerCamelModel):
    name: str | None = None
    driver: str | None = None
    last_activity: dt.datetime | None = None
    dynamic: bool | None = None
    nodes: list[BuilderNode] | None = None
