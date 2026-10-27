"""Swarm secret metadata and creation commands requiring a manager daemon."""

import builtins
import datetime as dt
import json
import warnings
from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import (
    Any,
    Literal,
    Self,
    assert_never,
    overload,
    override,
)

from pydantic import JsonValue

from pywhales.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components._cleanup import resource_cleanup
from pywhales.components._swarm_inspect import parse_swarm_inspect_results
from pywhales.components.secret.models import SecretInspectResult, SecretSpec
from pywhales.utils import (
    ValidPath,
    custom_parse_object_as,
    format_mapping_for_cli,
    run,
    to_list,
)

type SecretListFilter = (
    tuple[Literal["id"], str]
    | tuple[Literal["label"], str]
    | tuple[Literal["name"], str]
)


class Secret(ReloadableObjectFromJson[SecretInspectResult]):
    """Reloadable Swarm secret handle with context-exit removal.

    Cleanup is attempted even if the body raises. If cleanup also fails, both
    exceptions are preserved together rather than suppressing either failure.
    """

    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        if client_config.raw_diagnostics:
            print("reference: ", reference)
        super().__init__(client_config, "id", reference, is_immutable_id)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        with resource_cleanup(exc_val, "Secret body and cleanup both failed"):
            self.remove()

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "secret", "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> SecretInspectResult:
        return SecretInspectResult.model_validate(json_object)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def created_at(self) -> dt.datetime | None:
        return self._get_inspect_result().created_at

    @property
    def updated_at(self) -> dt.datetime | None:
        return self._get_inspect_result().updated_at

    @property
    def spec(self) -> SecretSpec | None:
        return self._get_inspect_result().spec

    def remove(self) -> None:
        """Remove this Docker secret.

        See the [`docker.secret.remove`](../sub-commands/secret.md#remove) command for
        information about the arguments.
        """
        SecretCLI(self.client_config).remove(self)


ValidSecret = Secret | str


class SecretCLI(DockerCLICaller):
    """Secret commands that retain metadata but cannot retrieve secret payloads."""

    def create(
        self,
        name: str,
        file: ValidPath,
        driver: str | None = None,
        labels: Mapping[str, str] = {},
        template_driver: str | None = None,
    ) -> Secret:
        """Create a Swarm secret from a file.

        Args:
            name: Name for the secret.
            file: Local file containing the secret payload.
            driver: Secret driver, or None to omit the option.
            labels: Metadata labels; the default adds none.
            template_driver: Template driver, or None to omit the option.

        Returns:
            A handle to the new secret without an initial inspect call.
        """
        full_cmd = Command([*self.docker_cmd, "secret", "create"])
        full_cmd.add_simple_arg("--driver", driver)
        full_cmd.add_args_iterable_or_single("--label", format_mapping_for_cli(labels))
        full_cmd.add_simple_arg("--template-driver", template_driver)
        full_cmd += [name, file]
        return Secret(self.client_config, run(full_cmd), is_immutable_id=True)

    @overload
    def inspect(self, x: str) -> Secret: ...

    @overload
    def inspect(self, x: Iterable[str]) -> builtins.list[Secret]: ...

    def inspect(self, x: str | Iterable[str]) -> Secret | builtins.list[Secret]:
        """Inspect one or more Swarm secrets by name or ID.

        Args:
            x: One name or ID, or an iterable of references. Empty input returns
                without invoking the CLI.

        Returns:
            One handle for a string, otherwise handles in request order with
            duplicates retained as distinct handles. All snapshots are validated
            before any returned handle is hydrated.

        Raises:
            ParsingError: If response identities cannot be associated unambiguously
                with the requests or snapshots are inconsistent.
        """
        references = to_list(x)
        if not references:
            return []
        json_str = run([*self.docker_cmd, "secret", "inspect", *references])
        parser = Secret(self.client_config, references[0], is_immutable_id=True)
        snapshots = parse_swarm_inspect_results(parser, json_str, references)
        resources = []
        for identity, snapshot in snapshots:
            resource = Secret(self.client_config, identity, is_immutable_id=True)
            resource._set_inspect_result(snapshot)
            resources.append(resource)
        match x:
            case str():
                return resources[0]
            case Iterable():
                return resources
            case unreachable:
                assert_never(unreachable)

    def list(
        self, filters: Iterable[SecretListFilter] | Mapping[str, Any] = ()
    ) -> builtins.list[Secret]:
        """List secrets in the selected Swarm.

        Args:
            filters: Docker filter pairs; the default selects all secrets.
                Mapping inputs are deprecated.

        Returns:
            Secret handles in CLI output order.
        """
        filter_items = filters.items() if isinstance(filters, Mapping) else filters
        if isinstance(filters, Mapping):
            warnings.warn(
                "Passing filters as a mapping is deprecated, replace with an "
                "iterable of tuples instead, as so:\n"
                f"filters={list(filter_items)}",
                DeprecationWarning,
                stacklevel=2,
            )
        full_cmd = Command([*self.docker_cmd, "secret", "list", "--quiet"])
        full_cmd.add_args_iterable("--filter", (f"{f[0]}={f[1]}" for f in filter_items))
        ids = run(full_cmd).splitlines()
        return [Secret(self.client_config, id_, is_immutable_id=True) for id_ in ids]

    def remove(self, x: ValidSecret | Iterable[ValidSecret]) -> None:
        """Remove one or more secrets.

        Args:
            x: One or more secrets.
                Name, ids or `pywhales.Secret` objects are valid inputs.
                An empty iterable does not invoke the CLI.
        """
        secrets = [x] if isinstance(x, str | Secret) else list(x)
        if not secrets:
            return
        full_cmd = Command([*self.docker_cmd, "secret", "remove", *secrets])
        run(full_cmd)
