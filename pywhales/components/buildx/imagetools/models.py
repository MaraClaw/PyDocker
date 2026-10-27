"""Registry manifest and index models with explicit Docker wire aliases."""

from __future__ import annotations

from typing import Annotated

import pydantic


class ManifestConfig(pydantic.BaseModel):
    media_type: Annotated[str | None, pydantic.Field(alias="mediaType")] = None
    digest: str | None = None
    size: int | None = None


class ManifestLayer(pydantic.BaseModel):
    media_type: Annotated[str | None, pydantic.Field(alias="mediaType")] = None
    digest: str | None = None
    size: int | None = None


class ManifestPlatform(pydantic.BaseModel):
    architecture: str | None = None
    os: str | None = None
    os_version: str | None = None
    variant: str | None = None


class ImageVariantManifest(pydantic.BaseModel):
    media_type: Annotated[str | None, pydantic.Field(alias="mediaType")] = None
    size: int | None = None
    digest: str | None = None
    platform: ManifestPlatform | None = None


class Manifest(pydantic.BaseModel):
    """Manifest or index snapshot, not a daemon-local image or pull operation."""

    media_type: Annotated[str | None, pydantic.Field(alias="mediaType")]
    schema_version: Annotated[int | None, pydantic.Field(alias="schemaVersion")]
    layers: list[ManifestLayer] | None = None
    manifests: list[ImageVariantManifest] | None = None
    config: ManifestConfig | None = None
    annotations: dict[str, pydantic.JsonValue] | None = None
