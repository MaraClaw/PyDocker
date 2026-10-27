"""Typed local-image inspection snapshots using shared container config models."""

from datetime import datetime

from pydantic import JsonValue

import pywhales.components.container.models
from pywhales.utils import DockerCamelModel


class ImageHealthcheck(DockerCamelModel):
    test: list[str] | None = None
    interval: int | None = None
    timeout: int | None = None
    retries: int | None = None
    start_period: int | None = None


class ImageGraphDriver(DockerCamelModel):
    name: str | None = None
    data: JsonValue = None


class ImageRootFS(DockerCamelModel):
    type: str | None = None
    layers: list[str] | None = None
    base_layer: str | None = None


class ImageInspectResult(DockerCamelModel):
    id: str | None = None
    repo_tags: list[str] | None = None
    repo_digests: list[str] | None = None
    parent: str | None = None
    comment: str | None = None
    created: datetime | None = None
    container: str | None = None
    container_config: pywhales.components.container.models.ContainerConfig | None = None
    docker_version: str | None = None
    author: str | None = None
    config: pywhales.components.container.models.ContainerConfig | None = None
    architecture: str | None = None
    os: str | None = None
    os_version: str | None = None
    variant: str | None = None
    size: int | None = None
    virtual_size: int | None = None
    graph_driver: ImageGraphDriver | None = None
    root_fs: ImageRootFS | None = None
    metadata: dict[str, str] | None = None
