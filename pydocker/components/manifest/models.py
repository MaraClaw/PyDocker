from pydocker.components.buildx.imagetools.models import ImageVariantManifest
from pydocker.utils import DockerCamelModel


class ManifestListInspectResult(DockerCamelModel):
    name: str | None = None
    schema_version: int | None = None
    media_type: str | None = None
    manifests: list[ImageVariantManifest] | None = None
