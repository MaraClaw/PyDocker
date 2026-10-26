from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType, TracebackType
from typing import Any, Self, overload, override

from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components.plugin.models import (
    PluginConfig,
    PluginInspectResult,
    PluginSettings,
)
from pywhales.utils import ValidPath, run, to_list


class Plugin(ReloadableObjectFromJson[PluginInspectResult]):
    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        super().__init__(client_config, "id", reference, is_immutable_id)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        with resource_cleanup(exc_val, "Plugin body and cleanup both failed"):
            self.remove(force=True)

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, Any]:
        json_str = run([*self.docker_cmd, "plugin", "inspect", reference])
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: dict[str, Any]) -> PluginInspectResult:
        return PluginInspectResult(**json_object)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def name(self) -> str | None:
        return self._get_inspect_result().name

    @property
    def enabled(self) -> bool | None:
        return self._get_inspect_result().enabled

    @property
    def settings(self) -> PluginSettings | None:
        return self._get_inspect_result().settings

    @property
    def plugin_reference(self) -> str | None:
        return self._get_inspect_result().plugin_reference

    @property
    def config(self) -> PluginConfig | None:
        return self._get_inspect_result().config

    @property
    def _docker_plugin(self) -> PluginCLI:
        return PluginCLI(self.client_config)

    @override
    def __repr__(self) -> str:
        return f"pywhales.Plugin(id='{self.id[:12]}', name='{self.name}')"

    def disable(self, force: bool = False) -> None:
        """Disable this plugin"""
        self._docker_plugin.disable(self, force=force)

    def enable(self, timeout: int | None = None) -> None:
        """Enable this plugin"""
        self._docker_plugin.enable(self, timeout=timeout)

    def push(self, disable_content_trust: bool = True) -> None:
        """Push this plugin"""
        self._docker_plugin.push(self, disable_content_trust=disable_content_trust)

    def remove(self, force: bool = False) -> None:
        """Remove this plugin"""
        self._docker_plugin.remove(self, force=force)

    def set(self, configuration: dict[str, str]) -> None:
        """Set the configuration for this plugin"""
        self._docker_plugin.set(self, configuration)

    def upgrade(
        self,
        remote: str | None = None,
        disable_content_trust: bool = True,
        skip_remote_check: bool = False,
    ) -> None:
        """Upgrade this plugin"""
        self._docker_plugin.upgrade(
            self, remote, disable_content_trust, skip_remote_check
        )


ValidPlugin = Plugin | str


class PluginCLI(DockerCLICaller):
    def create(
        self, plugin_name: str, plugin_data_directory: ValidPath, compress: bool = False
    ) -> Plugin:
        """Create a plugin from a rootfs and configuration.

        Parameters:
            plugin_name: The name you want to give to your plugin
            plugin_data_directory: Must contain config.json and rootfs directory.
            compress: Compress the context using gzip
        """
        full_cmd = Command([*self.docker_cmd, "plugin", "create"])
        full_cmd.add_flag("--compress", compress)
        full_cmd += [plugin_name, plugin_data_directory]
        run(full_cmd)
        return Plugin(self.client_config, plugin_name)

    def disable(self, plugin: ValidPlugin, force: bool = False) -> None:
        """Disable a plugin

        Parameters:
            plugin: The plugin to disable
            force: Force the disable of an active plugin
        """
        full_cmd = Command([*self.docker_cmd, "plugin", "disable"])
        full_cmd.add_flag("--force", force)
        full_cmd.append(plugin)
        run(full_cmd)

    def enable(self, plugin: ValidPlugin, timeout: int | None = None) -> None:
        """Enable a plugin

        Parameters:
            plugin: The plugin to enable
            timeout: HTTP client timeout (in seconds) (default 30)
        """
        full_cmd = Command([*self.docker_cmd, "plugin", "enable"])
        full_cmd.add_simple_arg("--timeout", timeout)
        full_cmd.append(plugin)
        run(full_cmd)

    @overload
    def inspect(self, x: str) -> Plugin: ...

    @overload
    def inspect(self, x: builtins.list[str]) -> builtins.list[Plugin]: ...

    def inspect(self, x: str | builtins.list[str]) -> Plugin | builtins.list[Plugin]:
        """Returns a `pywhales.Plugin` object from a string
        (name or id of the plugin)

        Parameters:
            x: One id or hostname or a list of name or ids

        # Returns
            One or a list of `pywhales.Plugin`
        """
        if isinstance(x, str):
            return Plugin(self.client_config, x)
        else:
            return [Plugin(self.client_config, reference) for reference in x]

    def install(
        self,
        plugin_name: str,
        configuration: Mapping[str, str] = MappingProxyType({}),
        alias: str | None = None,
        disable: bool = False,
        disable_content_trust: bool = True,
    ) -> Plugin:
        """Installs a Docker plugin

        Warning: `--grant-all-permissions` is enabled, which means the program won't
        stop to ask you to grant the permissions.

        Parameters:
            plugin_name: The name of the plugin you want to install
            configuration: A `dict` adding configuration options to the plugin
            alias: Local name for plugin
            disable: Do not enable the plugin on install
            disable_content_trust: Skip image verification (default `True`)

        # Returns
            A `pywhales.Plugin`.
        """
        full_cmd = Command(
            [*self.docker_cmd, "plugin", "install", "--grant-all-permissions"]
        )
        full_cmd.add_simple_arg("--alias", alias)
        full_cmd.add_flag("--disable", disable)
        if not disable_content_trust:
            full_cmd.append("--disable-content-trust=false")
        full_cmd.append(plugin_name)
        for key, value in configuration.items():
            full_cmd.append(f"{key}={value}")
        run(full_cmd)
        if alias is not None:
            return Plugin(self.client_config, alias)
        return Plugin(self.client_config, plugin_name)

    def list(self) -> builtins.list[Plugin]:
        """Returns a `List[pywhales.Plugin` that are installed on the daemon."""
        full_cmd = Command(
            [
                *self.docker_cmd,
                "plugin",
                "list",
                "--no-trunc",
                "--quiet",
            ]
        )
        ids = run(full_cmd).splitlines()
        return [Plugin(self.client_config, id_, is_immutable_id=True) for id_ in ids]

    def push(self, plugin: ValidPlugin, disable_content_trust: bool = True) -> None:
        """Push a plugin to a registry.


        Parameters:
            plugin: The plugin to push
            disable_content_trust: Skip image signing (default `True`)
        """
        full_cmd = Command([*self.docker_cmd, "plugin", "push"])
        if not disable_content_trust:
            full_cmd.append("--disable-content-trust=false")
        full_cmd.append(plugin)
        run(full_cmd)

    def remove(
        self, x: ValidPlugin | Sequence[ValidPlugin], force: bool = False
    ) -> None:
        """Removes one or more plugins

        Parameters:
            x: One or more plugins to remove.
            force: Force the removal of this plugin.
        """
        full_cmd = Command([*self.docker_cmd, "plugin", "remove"])
        full_cmd.add_flag("--force", force)
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)

    def set(self, plugin: ValidPlugin, configuration: dict[str, str]) -> None:
        """Change the settings for a plugin

        Parameters:
            plugin: The plugin that needs its settings changed
            configuration: The new configuration options.
        """
        full_cmd = Command([*self.docker_cmd, "plugin", "set"])
        full_cmd.append(plugin)
        for key, value in configuration.items():
            full_cmd.append(f"{key}={value}")
        run(full_cmd)

    def upgrade(
        self,
        plugin: ValidPlugin,
        remote: str | None = None,
        disable_content_trust: bool = True,
        skip_remote_check: bool = False,
    ) -> None:
        """Upgrade a plugin

        Warning: `--grant-all-permissions` is enabled, which means the program won't
        stop to ask you to grant the permissions.

        Parameters:
            plugin: The plugin to upgrade
            remote: The remote to fetch the upgrade from
            disable_content_trust: Skip image verification (default `True`)
            skip_remote_check: Do not check if specified remote plugin matches
                existing plugin image
        """
        full_cmd = Command(
            [*self.docker_cmd, "plugin", "upgrade", "--grant-all-permissions"]
        )
        if not disable_content_trust:
            full_cmd.append("--disable-content-trust=false")
        full_cmd.add_flag("--skip-remote-check", skip_remote_check)
        full_cmd.append(plugin)
        if remote is not None:
            full_cmd.append(remote)
        run(full_cmd)
