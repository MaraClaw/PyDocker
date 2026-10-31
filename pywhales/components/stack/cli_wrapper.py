"""Swarm stack deployment and discovery through the configured Docker CLI."""

from __future__ import annotations

import builtins
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import override

import pywhales.components.service.cli_wrapper
import pywhales.components.task.cli_wrapper
from pywhales.client_config import ClientConfig, Command, DockerCLICaller
from pywhales.utils import ValidPath, read_env_files, run, to_list, validate_timeout


class Stack:
    """Named stack handle delegating operations to its originating client.

    Attributes:
        client_config: Shared mutable client configuration used for each operation.
        name: Stack name used in subsequent commands.
    """

    def __init__(self, client_config: ClientConfig, name: str) -> None:
        self.client_config = client_config
        self.name = name

    @override
    def __str__(self) -> str:
        return self.name

    @override
    def __eq__(self, other: object) -> bool:
        if isinstance(other, Stack):
            return self.client_config == other.client_config and self.name == other.name
        else:
            return False

    @override
    def __repr__(self) -> str:
        return f"pywhales.Stack(name='{self.name}')"

    def remove(self) -> None:
        """Remove this stack through the configured Docker CLI."""
        StackCLI(self.client_config).remove(self)

    def ps(self) -> list[pywhales.components.task.cli_wrapper.Task]:
        """Return task handles associated with this stack."""
        return StackCLI(self.client_config).ps(self)

    def services(self) -> list[pywhales.components.service.cli_wrapper.Service]:
        """Return service handles associated with this stack."""
        return StackCLI(self.client_config).services(self)


ValidStack = str | Stack


class StackCLI(DockerCLICaller):
    """Stack administration against a Swarm manager, with local deployment files."""

    def deploy(
        self,
        name: str,
        compose_files: ValidPath | Sequence[ValidPath] = (),
        prune: bool = False,
        resolve_image: str = "always",
        with_registry_auth: bool = False,
        env_files: Sequence[ValidPath] = (),
        variables: Mapping[str, str] = MappingProxyType({}),
        *,
        timeout: float | None = None,
    ) -> Stack:
        """Deploy a stack from Compose files.

        This changes daemon-managed services and displays Docker's stdout.
        Failure or local interruption does not undo daemon-side mutations.

        Args:
            name: The name of the stack to deploy. Mandatory.
            compose_files: One or more docker-compose files. If there are more than
                one, they will be fused together.
            prune: Prune services that are no longer referenced
            resolve_image: Query the registry to resolve image digest
                and supported platforms `"always"|"changed"|"never"` (default `"always"`).
                Note that if the registry cannot be queried when using `"always"`, it's
                going to try to use images present locally on the nodes.
            with_registry_auth: Send registry authentication details to Swarm agents.
                Required if you need to run `docker login` to pull the docker images
                in your stack.
            env_files: Similar to `.env` files in docker-compose. Loads `variables` from
                `.env` files. If both `env_files` and `variables` are used, `variables`
                have priority. This behavior is similar to the one you would experience with
                compose.
            variables: Environment overrides for interpolating Compose file
                variables, taking precedence over values loaded from `env_files`.
            timeout: Positive finite seconds from the deploy CLI's own launch,
                replacing the client default; None inherits it. It is validated
                before the executable is resolved or any env file is read.

        Returns:
            A named stack handle after the deploy CLI exits, not a guarantee that
            all deployed service tasks have converged.

        Raises:
            ValueError: If timeout is a Boolean or is neither None nor a positive
                finite number of seconds.
            DockerTimeout: If the CLI exceeds its deadline. It is killed and reaped,
                without undoing the deploy or guaranteeing daemon-side work stops.
        """
        validate_timeout(timeout)
        full_cmd = Command([*self.docker_cmd, "stack", "deploy"])

        full_cmd.add_args_iterable_or_single("--compose-file", compose_files)
        full_cmd.add_flag("--prune", prune)
        full_cmd.add_simple_arg("--resolve-image", resolve_image)
        full_cmd.add_flag("--with-registry-auth", with_registry_auth)
        full_cmd.append(name)

        env = read_env_files([Path(x) for x in env_files])
        env.update(variables)

        run(full_cmd, capture_stdout=False, env=env, timeout=timeout)
        return Stack(self.client_config, name)

    def list(self) -> builtins.list[Stack]:
        """List stacks using the configured Docker CLI.

        Returns:
            Stack handles in CLI output order.
        """
        full_cmd = Command([*self.docker_cmd, "stack", "ls", "--format", "{{.Name}}"])
        stacks_names = run(full_cmd).splitlines()
        return [Stack(self.client_config, name) for name in stacks_names]

    def ps(
        self, x: ValidStack
    ) -> builtins.list[pywhales.components.task.cli_wrapper.Task]:
        """List Swarm tasks associated with a stack.

        Args:
            x: Stack name or handle to list tasks for.

        Returns:
            Task handles in CLI output order, including any task history Docker lists.
        """
        full_cmd = Command(
            [*self.docker_cmd, "stack", "ps", "--quiet", "--no-trunc", x]
        )

        ids = run(full_cmd).splitlines()
        return [
            pywhales.components.task.cli_wrapper.Task(
                self.client_config, id_, is_immutable_id=True
            )
            for id_ in ids
        ]

    def remove(self, x: ValidStack | Iterable[ValidStack]) -> None:
        """Remove one or more stacks.

        Args:
            x: One or more stacks, an empty selection means nothing will be done.
        """
        stacks = to_list(x)
        if not stacks:
            return
        full_cmd = Command([*self.docker_cmd, "stack", "remove", *stacks])
        run(full_cmd)

    def services(
        self, stack: ValidStack
    ) -> builtins.list[pywhales.components.service.cli_wrapper.Service]:
        """List the services present in the stack.

        Args:
            stack: A docker stack or the name of a stack.

        Returns:
            Service handles in CLI output order, inspected individually to resolve
            their full IDs.
        """
        full_cmd = Command([*self.docker_cmd, "stack", "services", "--quiet", stack])
        ids = run(full_cmd).splitlines()
        return [
            pywhales.components.service.cli_wrapper.Service(self.client_config, id_)
            for id_ in ids
        ]
