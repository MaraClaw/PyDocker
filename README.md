# PyWhales

------------------------------------------------------------------------

[![Run tests](https://github.com/MaraClaw/PyWhales/actions/workflows/python-package.yml/badge.svg)](https://github.com/MaraClaw/PyWhales/actions/workflows/python-package.yml)
[![PyPI](https://img.shields.io/pypi/v/pywhales.svg)](https://pypi.org/project/pywhales/#description)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Types: ty](https://img.shields.io/badge/types-ty-261230.svg)](https://docs.astral.sh/ty/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/MaraClaw/PyWhales/blob/HEAD/LICENSE)

------------------------------------------------------------------------
The Docker and its composer client for Python, designed to be fun and intuitive!

Works on Linux, macOS and Windows, for Python 3.14.8 and above.

* [Documentation](https://github.com/MaraClaw/PyWhales/tree/HEAD/docs)
* [GitHub repository](https://github.com/MaraClaw/PyWhales)

------------------------------------------------------------------------

## How to install?

```bash
uv pip install pywhales
```

See [the Docker CLI guide](https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/docker_client.md#the-docker-cli)
for instructions on installing Docker.

## Some cool examples

Start by doing

```python
from pywhales import docker
```

and then:

* `docker run hello-world` -> `docker.run("hello-world")`
* `docker pull ubuntu` -> `docker.pull("ubuntu")`
* `docker build ./` -> `docker.build("./")`
* `docker compose up my_service` -> `docker.compose.up(["my_service"])`
* `docker image ls` -> `docker.image.list()`
* `docker ps` -> `docker.ps()`
* `docker cp` -> `docker.copy()`

```python
>>> from pywhales import docker

>>> output = docker.run("hello-world")
>>> print(output)

Hello from Docker!
This message shows that your installation appears to be working correctly.

...
```

```python
>>> from pywhales import docker
>>> my_image = docker.build(".", tags="some_name")  # uses Buildx/buildkit by default
[+] Building 1.6s (17/17) FINISHED
...
```

## Some more `docker.run()` advanced examples with postgres

```bash
docker run --name some-postgres -e POSTGRES_PASSWORD=mysecretpassword -d postgres
```

becomes

```python
from pywhales import docker

docker.run(
    "postgres:9.6",
    name="some-postgres",
    envs={"POSTGRES_PASSWORD": "mysecretpassword"},
    detach=True,
)
print(docker.ps())
# [pywhales.Container(id='f5fb939c409d', name='some-postgres')]
```

-----

```bash
docker run -it --rm --network some-network postgres psql -h some-postgres -U postgres
```

becomes

```python
from pywhales import docker

# since it's interactive, you'll be dropped into the psql shell. The python code
# will continue only after you exit the shell.
docker.run(
    "postgres:9.6",
    ["psql", "-h", "some-postgres", "-U", "postgres"],
    networks=["some-network"],
    interactive=True,
    tty=True,
    remove=True,
)
```

----------

```bash
docker run -d --name some-postgres -e POSTGRES_PASSWORD=mysecretpassword -e PGDATA=/var/lib/postgresql/data/pgdata -v /custom/mount:/var/lib/postgresql/data -v myvolume:/tmp/myvolume postgres -c shared_buffers=256MB -c max_connections=200
```

becomes

```python
from pywhales import docker

docker.run(
    "postgres:9.6",
    ["-c", "shared_buffers=256MB", "-c", "max_connections=200"],
    name="some-postgres",
    envs={
        "POSTGRES_PASSWORD": "mysecretpassword",
        "PGDATA": "/var/lib/postgresql/data/pgdata",
    },
    volumes=[
        ("/custom/mount", "/var/lib/postgresql/data"),
        ("myvolume", "/tmp/myvolume"),
    ],
    detach=True,
)
```


Any Docker object can be used as a context manager to ensure it's removed even if an exception occurs:

```python
from pywhales import docker

with docker.volume.create("random_name") as some_volume:
    docker.run(
        "postgres:9.6",
        ["-c", "shared_buffers=256MB", "-c", "max_connections=200"],
        name="some-postgres",
        envs={
            "POSTGRES_PASSWORD": "mysecretpassword",
            "PGDATA": "/var/lib/postgresql/data/pgdata",
        },
        volumes=[
            (some_volume, "/var/lib/postgresql/data"),
            ("myvolume", "/tmp/myvolume"),
        ],
        detach=True,
    )
    # so some stuff here

# here we are out of the context manager, so the volume has been removed, even if there was an exception.
```

## Main features

* 1 to 1 mapping between the CLI interface and the Python API. No need to look in the docs
what is the name of the function/argument you need.
* Support for the latest Docker features:
[Docker buildx/buildkit](https://github.com/docker/buildx),
`docker run --gpu=all ...`
* Support for Docker stack, services and Swarm (same API as the command line).
* Progress bars and progressive outputs when pulling, pushing, loading, building...
* Support for some other CLI commands that are not in [Docker-py](https://docker-py.readthedocs.io/en/stable/):
`docker cp`, `docker run --cpus ...` and more.
* Nice SSH support for remote daemons.
* Docker object as Python objects: Container, Images, Volumes, Services... and their
attributes are updated in real-time!
* Each Docker object can be used as a context manager. When getting out of the
context, the Docker object is removed automatically, even if an exception occurs.
* A fully typed API (ty and IDE-friendly) compatible with `pathlib` and `os.path`
* All Docker objects and the Docker client are safe to use with multithreading and multiprocessing.
* Enable execution diagnostics by setting `PYDOCKER_DEBUG=1`. Diagnostics omit command arguments,
  environment values, and captured output by default. Use `DockerClient(raw_diagnostics=True)`
  only when detailed diagnostics are appropriate for that client's logs.
  The legacy `PYTHON_ON_WHALES_DEBUG` variable is a fallback only when `PYDOCKER_DEBUG` is unset.
  Setting `PYDOCKER_DEBUG=0` or leaving its value explicitly empty disables debug output even if the legacy variable is set.

## What about the license?

It's a MIT license, so quite permissive.

The license can be found [in the git repository](https://github.com/MaraClaw/PyWhales/blob/HEAD/LICENSE).
