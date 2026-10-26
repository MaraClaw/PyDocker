from pywhales.utils import DockerCamelModel


class PluginMount(DockerCamelModel):
    pass


class PluginDevice(DockerCamelModel):
    pass


class PluginSettings(DockerCamelModel):
    mounts: list[PluginMount] | None = None
    env: list[str] | None = None
    args: list[str] | None = None
    devices: list[PluginDevice] | None = None


class Interface(DockerCamelModel):
    pass


class PluginConfig(DockerCamelModel):
    docker_version: str | None = None
    description: str | None = None
    documentation: str | None = None
    interface: Interface | None = None
    entrypoint: list[str] | None = None
    work_dir: str | None = None
    # TODO: add missing attributes


class PluginInspectResult(DockerCamelModel):
    id: str | None = None
    name: str | None = None
    enabled: bool | None = None
    settings: PluginSettings | None = None
    plugin_reference: str | None = None
    config: PluginConfig | None = None
