"""Validate and snapshot child environments without changing process state."""

import os
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_WINDOWS = os.name == "nt"
_frozen_environment: ContextVar[tuple[object, Mapping[str, str]] | None] = ContextVar(
    "pywhales_frozen_environment", default=None
)


def copy_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Return a validated copy without exposing invalid entry contents in errors."""
    if not isinstance(environment, Mapping):
        raise TypeError("environment must be a mapping of strings")
    result: dict[str, str] = {}
    names: set[str] = set()
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("environment keys and values must be strings")
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise ValueError("invalid environment entry")
        name = key.upper() if _WINDOWS else key
        if name in names:
            raise ValueError("duplicate environment name")
        names.add(name)
        result[key] = value
    return result


def _merge_environment(base: dict[str, str], layer: Mapping[str, str]) -> None:
    if _WINDOWS:
        names = {key.upper(): key for key in base}
        for key, value in layer.items():
            previous = names.get(key.upper())
            if previous is not None:
                del base[previous]
            base[key] = value
            names[key.upper()] = key
    else:
        base.update(layer)


def resolve_environment(
    environment: Mapping[str, str] | None,
    overrides: Mapping[str, str] | None = None,
    *,
    frozen: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Select the full child environment before argument stringification.

    A frozen full environment replaces ambient and client layers; operation
    overrides still apply. Return a new mapping owned by the caller.
    """
    if frozen is not None:
        result = dict(frozen)
    else:
        result = copy_environment(os.environ)
        if environment is not None:
            _merge_environment(result, copy_environment(environment))
    if overrides is not None:
        _merge_environment(result, copy_environment(overrides))
    return result


def finalize_environment(
    environment: Mapping[str, str], args: Sequence[str]
) -> dict[str, str]:
    """Apply mandatory Buildx settings after argv conversion without rereading inputs."""
    result = dict(environment)
    if len(args) > 1 and args[1] == "buildx":
        _merge_environment(result, {"DOCKER_CLI_EXPERIMENTAL": "enabled"})
    return result


@contextmanager
def frozen_environment_scope(
    source: object, environment: Mapping[str, str]
) -> Generator[None]:
    """Install a one-use full environment for the identical policy source.

    The next matching policy snapshot consumes the value, so nested launches
    triggered after that snapshot use their own current environment.
    """
    token = _frozen_environment.set(
        (source, MappingProxyType(copy_environment(environment)))
    )
    try:
        yield
    finally:
        _frozen_environment.reset(token)


def consume_frozen_environment(source: object) -> Mapping[str, str] | None:
    """Consume the planned environment only when its source is identical."""
    scope = _frozen_environment.get()
    if scope is None or scope[0] is not source:
        return None
    _frozen_environment.set(None)
    return scope[1]
