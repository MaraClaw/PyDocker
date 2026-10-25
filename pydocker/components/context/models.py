from typing import Annotated

import pydantic

from pydocker.utils import DockerCamelModel


class ContextEndpoint(DockerCamelModel):
    host: str | None = None
    skip_tls_verify: Annotated[bool | None, pydantic.Field(alias="SkipTLSVerify")] = (
        None
    )


class ContextStorage(DockerCamelModel):
    metadata_path: str | None = None
    tls_path: Annotated[str | None, pydantic.Field(alias="TLSPath")] = None


class ContextInspectResult(DockerCamelModel):
    name: str | None = None
    metadata: dict[str, object] | None = None
    endpoints: dict[str, ContextEndpoint] | None = None
    tls_material: Annotated[
        dict[str, object] | None, pydantic.Field(alias="TLSMaterial")
    ] = None
    storage: ContextStorage | None = None
