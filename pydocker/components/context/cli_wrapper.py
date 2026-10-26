from __future__ import annotations

import builtins
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, overload, override

from pydocker.client_config import (
    ClientConfig,
    Command,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pydocker.components.context.models import (
    ContextEndpoint,
    ContextInspectResult,
    ContextStorage,
)
from pydocker.utils import run, to_list


class Context(ReloadableObjectFromJson[ContextInspectResult, str | None]):
    def __init__(
        self,
        client_config: ClientConfig,
        reference: str | None,
        is_immutable_id: bool = False,
    ) -> None:
        super().__init__(client_config, "name", reference, is_immutable_id)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            self.remove(force=True)
        except BaseException as cleanup_error:
            if exc_val is None:
                raise
            raise BaseExceptionGroup(
                "Context body and cleanup both failed", [exc_val, cleanup_error]
            ) from None

    @override
    def _fetch_inspect_result_json(self, reference: str | None) -> dict[str, Any]:
        full_cmd = Command([*self.docker_cmd, "context", "inspect"])
        if reference is not None:
            full_cmd.append(reference)
        json_str = run(full_cmd)
        return json.loads(json_str)[0]

    @override
    def _parse_json_object(self, json_object: dict[str, Any]) -> ContextInspectResult:
        return ContextInspectResult(**json_object)

    @property
    def name(self) -> str | None:
        return self._get_immutable_id()

    @property
    def metadata(self) -> dict[str, object] | None:
        return self._get_inspect_result().metadata

    @property
    def endpoints(self) -> dict[str, ContextEndpoint] | None:
        return self._get_inspect_result().endpoints

    @property
    def tls_material(self) -> dict[str, object] | None:
        return self._get_inspect_result().tls_material

    @property
    def storage(self) -> ContextStorage | None:
        return self._get_inspect_result().storage

    def remove(self, force: bool = False) -> None:
        """Removes this context"""
        ContextCLI(self.client_config).remove(self, force)

    def update(self) -> None:
        """Update this context, not yet implemented."""
        raise NotImplementedError
        ContextCLI(self.client_config).update(self)

    def use(self) -> None:
        """Use this context"""
        ContextCLI(self.client_config).use(self)

    @override
    def __repr__(self) -> str:
        return f"pydocker.Context(name='{self.name}', endpoints={self.endpoints})"


ValidContext = Context | str


@dataclass
class DockerContextConfig:
    from_: ValidContext | None = None
    host: str | None = None
    certificate_authority: Path | str | None = None
    certificate: Path | str | None = None
    key: Path | str | None = None
    skip_tls_verify: bool = False

    def format_for_docker_cli(self) -> str:
        list_of_args = []
        if self.from_ is not None:
            list_of_args.append(f"from={self.from_}")
        if self.host is not None:
            list_of_args.append(f"host={self.host}")
        if self.certificate_authority is not None:
            list_of_args.append(f"ca={self.certificate_authority}")
        if self.certificate is not None:
            list_of_args.append(f"cert={self.certificate}")
        if self.key is not None:
            list_of_args.append(f"key={self.key}")
        list_of_args.append(f"skip-tls-verify={self.skip_tls_verify}")

        return ",".join(list_of_args)


@dataclass
class KubernetesContextConfig:
    from_: ValidContext | None = None
    config_file: str | Path | None = None
    context_override: str | None = None
    namespace_override: str | None = None

    def format_for_docker_cli(self) -> str:
        list_of_args = []
        if self.from_ is not None:
            list_of_args.append(f"from={self.from_}")
        if self.config_file is not None:
            list_of_args.append(f"config-file={self.config_file}")
        if self.context_override is not None:
            list_of_args.append(f"context-override={self.context_override}")
        if self.namespace_override is not None:
            list_of_args.append(f"namespace-override={self.namespace_override}")
        return ",".join(list_of_args)


class ContextCLI(DockerCLICaller):
    def create(
        self,
        context_name: str,
        default_stack_orchestrator: str | None = None,
        description: str | None = None,
        from_: ValidContext | None = None,
        docker: dict[str, Any] | DockerContextConfig | None = None,
        kubernetes: dict[str, Any] | KubernetesContextConfig | None = None,
    ) -> Context:
        """Creates a new context

        Parameters:
            context_name: name of the context to create
            default_stack_orchestrator: Default orchestrator for stack operations to use with this context (swarm|kubernetes|all)
            description: Description of the context
            docker: Set the docker endpoint, you can use a dict of a class to
                specify the options. The class is `pydocker.DockerContextConfig`.
            from_: Create context from a named context
            kubernetes: Set the kubernetes endpoint. You can use a dict or a class to specify the options. The class
                is `pydocker.KubernetesContextConfig`.
        """
        if isinstance(docker, dict):
            docker = DockerContextConfig(**docker)
        if isinstance(kubernetes, dict):
            kubernetes = KubernetesContextConfig(**kubernetes)

        full_cmd = Command([*self.docker_cmd, "context", "create"])

        full_cmd.add_simple_arg(
            "--default-stack-orchestrator", default_stack_orchestrator
        )
        full_cmd.add_simple_arg("--description", description)
        full_cmd.add_simple_arg("--from", from_)
        if docker is not None:
            full_cmd.add_simple_arg("--docker", docker.format_for_docker_cli())
        if kubernetes is not None:
            full_cmd.add_simple_arg("--kubernetes", kubernetes.format_for_docker_cli())
        full_cmd.append(context_name)
        run(full_cmd)
        return self.inspect(context_name)

    @overload
    def inspect(self, x: str | None = None) -> Context: ...

    @overload
    def inspect(self, x: builtins.list[str]) -> builtins.list[Context]: ...

    def inspect(
        self, x: str | builtins.list[str] | None = None
    ) -> Context | builtins.list[Context]:
        """Returns the context object. If no argument is provided, returns the
        current context."""
        if isinstance(x, str) or x is None:
            return Context(self.client_config, x)
        else:
            return [Context(self.client_config, ref) for ref in x]

    def list(self) -> builtins.list[Context]:
        """List all Docker contexts available

        # Returns
            `List[pydocker.Context]`
        """

        full_cmd = Command([*self.docker_cmd, "context", "list", "--quiet"])

        ids = run(full_cmd).splitlines()
        return [Context(self.client_config, id_, is_immutable_id=True) for id_ in ids]

    def remove(
        self, x: ValidContext | Sequence[ValidContext], force: bool = False
    ) -> None:
        """Removes one or more contexts

        Parameters:
            x: One or more contexts, empty list means no-op.
            force: Force the removal of this context
        """
        full_cmd = Command([*self.docker_cmd, "context", "remove"])
        full_cmd.add_flag("--force", force)
        if x == []:
            return
        full_cmd += to_list(x)
        run(full_cmd)

    def update(self) -> None:
        """Not yet implemented"""
        raise NotImplementedError

    def use(self, context: ValidContext) -> Context:
        """Set the default context

        Parameters:
            context: The context to set as default
        """
        full_cmd = Command([*self.docker_cmd, "context", "use", context])
        run(full_cmd)
        return self.inspect(str(context))
