# PyWhales

[![Run tests](https://github.com/MaraClaw/PyWhales/actions/workflows/python-package.yml/badge.svg)](https://github.com/MaraClaw/PyWhales/actions/workflows/python-package.yml)
[![PyPI](https://img.shields.io/pypi/v/pywhales.svg)](https://pypi.org/project/pywhales/#description)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Types: ty](https://img.shields.io/badge/types-ty-261230.svg)](https://docs.astral.sh/ty/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/MaraClaw/PyWhales/blob/HEAD/LICENSE)

PyWhales is a typed Python wrapper around the external Docker CLI. It builds `docker` command lines, runs them as subprocesses, and returns the results as Python objects. It isn't an HTTP SDK and doesn't call the Docker Engine API.

* [Documentation](https://github.com/MaraClaw/PyWhales/tree/HEAD/docs)
* [GitHub repository](https://github.com/MaraClaw/PyWhales)

## Requirements

* Python 3.14.10 or newer, on Linux, macOS or Windows.
* The Docker CLI, installed separately. See [the Docker CLI guide](https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/docker_client.md#the-docker-cli).
* A reachable Docker daemon for commands that talk to it.
* The Compose and Buildx plugins for the commands that use them (`docker.compose`, `docker.build`).

Importing PyWhales or constructing a client doesn't check that the CLI, daemon or plugins are available. Problems show up when a command runs.

## Install

```bash
uv pip install pywhales
```

## Quickstart

```python
from pywhales import docker

print(docker.run("hello-world", remove=True))
containers = docker.ps()
images = docker.image.list()
```

Building and Compose need local files: a Dockerfile for `build` (`DockerClient.build` is an alias for Buildx) and a Compose file for `compose`.

```python
image = docker.build(".", tags="my-app:latest", load=True)
docker.compose.up(detach=True)
```

## Custom configuration

`docker` is a shared default client. Create your own `DockerClient` to change its settings:

```python
from pywhales import DockerClient

client = DockerClient(
    context="my-context",
    compose_files=["compose.yaml"],
    default_timeout=30,
)
```

The Docker context must already exist. `default_timeout` is in seconds and bounds each local CLI launch. Expiry kills and reaps the local CLI process, but doesn't roll back work the daemon has already started.

## Good to know

* Containers, images, volumes and other resources are typed handles. Reading a property may inspect the resource and cache the result briefly. Call `reload()` when you want a fresh copy.
* Streaming calls that own a process return iterators. Close them, or use them as context managers, if you stop reading early.
* `PYWHALES_DEBUG=1` turns on execution diagnostics that withhold command arguments, environment values and captured output. `DockerClient(raw_diagnostics=True)` opts a single client into raw diagnostics, so use it only where its logs are safe to expose.
* That redaction covers diagnostics only. Returned data, exception attributes and output PyWhales doesn't capture are not redacted.

## More documentation

* [Docker client and CLI setup](https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/docker_client.md)
* [`docker.run()` guide](https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/user_guide/docker_run.md)
* [Exceptions](https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/user_guide/exceptions.md)
* [Telemetry and diagnostics](https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/user_guide/telemetry.md)
* [All user guides](https://github.com/MaraClaw/PyWhales/tree/HEAD/docs/template/user_guide)

## License

PyWhales is released under the MIT license. See [LICENSE](https://github.com/MaraClaw/PyWhales/blob/HEAD/LICENSE).
