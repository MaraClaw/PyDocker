# Client, Setup, and Errors

## Prerequisites

Use Python >=3.14.8. In a uv-managed application, add the dependency with
`uv add pywhales`. The package doesn't supply the Docker CLI: Docker must be on
PATH, and the selected engine must be reachable. Compose and Buildx commands
also need their respective Docker CLI plugins.

Check `docker version` for client/server connectivity. Check `docker compose
version` or `docker buildx version` only when that plugin is needed. A successful
Python import or client construction doesn't test any of these prerequisites;
CLI lookup is deferred until a command needs it.

## Choose a Client

```python
from pywhales import docker

version = docker.version()
print(version.client.version)
```

`docker` is a ready-made `DockerClient(client_type="docker")`. For a separate
target, construct a scoped client rather than changing the active CLI context:

```python
from pywhales import DockerClient

remote = DockerClient(host="ssh://user@host", client_type="docker")
staging = DockerClient(context="staging", client_type="docker")
```

Those are alternatives; select the host or context the user supplied. SSH
authentication and access to the remote engine must already work. Bind-mount
source paths refer to the daemon host, not automatically to the Python machine.

Keep Compose globals on its client: `compose_files`, `compose_profiles`,
`compose_project_name`, `compose_project_directory`, and `compose_env_files`.
Use plural `compose_env_files`, not deprecated `compose_env_file`.

## Handle the Right Failure

```python
from pywhales import ClientNotFoundError, DockerException, docker

try:
    version = docker.version()
except ClientNotFoundError as error:
    raise SystemExit("Docker CLI is missing or isn't on PATH.") from error
except DockerException as error:
    print(f"Docker command failed with exit code {error.return_code}")
    raise
else:
    print(version.client.version)
```

`ClientNotFoundError` is separate from `DockerException`. The latter represents
a nonzero CLI exit and exposes `docker_command: list[str]`, `return_code: int`,
and `stdout`/`stderr` as `str | None`. Output can be `None` when it wasn't
captured. More specific errors, such as `NoSuchContainer` and `NoSuchImage`,
are imported from `pywhales.exceptions`.

Use the exit code and relevant, redacted output to distinguish daemon access,
missing resources, registry authentication, and unsupported plugin flags.
Don't retry a mutating command just because it failed. Invalid Python arguments
may raise `TypeError` or `ValueError` before a subprocess is started.

For registry login, `docker.login(server=..., username=..., password=...)`
passes the password through stdin. Obtain credentials from the application's
existing secret mechanism; don't embed them in generated examples or reports.

## Troubleshooting Without Credential Leaks

Don't enable `PYDOCKER_DEBUG=1` in a credential-bearing environment: it prints
the command and the entire merged subprocess environment. The legacy
`PYTHON_ON_WHALES_DEBUG` is consulted only if `PYDOCKER_DEBUG` is unset.
Prefer a redacted command and error excerpt over an environment dump.

If an argument is rejected, inspect that method in the installed package before
changing versions or guessing SDK-style names. New Compose flags are forwarded
to the plugin without a compatibility probe or automatic fallback.
