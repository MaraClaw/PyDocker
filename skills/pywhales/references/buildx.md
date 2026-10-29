# Buildx

`docker.build` is an alias of `docker.buildx.build`, not a legacy builder or a
Docker SDK method. A Buildx plugin, suitable builder, and build context are
required. Examples below assume the working directory contains a Dockerfile.

## Build for Local Use

```python
from pywhales import docker

image = docker.build(
    ".",
    tags="example-app:dev",
    load=True,
    progress="plain",
)
print(image)
```

Use `load=True` when the result must be available in the local daemon. Keep a
tag when using the `docker-container` driver so PyWhales can recover the loaded
image. Without an explicit exporter, loading depends on the builder's driver.

| Intent | Arguments |
| --- | --- |
| Choose Dockerfile/stage | `file="Dockerfile.dev", target="test"` |
| Pass build arguments | `build_args={"VERSION": "dev"}` |
| Disable cache | `cache=False`, not `no_cache=True` |
| Select target platforms | `platforms=["linux/amd64", "linux/arm64"]` |
| Export files | `output={"type": "local", "dest": "./dist"}` |
| Push to a registry | `push=True, tags="registry.example.com/team/app:release"` |

Choose an exporter for the requested result. Only push to a destination the
task authorizes, with existing registry authentication. Multi-platform builds
need a capable builder/exporter; don't assume the local image store can load
every multi-platform result.

Both `build` and `bake` accept a keyword-only `timeout` in positive finite
seconds, replacing the client's `default_timeout` for that one local CLI
launch; `None` inherits it. Builder inspection before launch and image
inspection after completion fall outside this budget, and each of `bake`'s
build/follow-up-config calls gets its own fresh budget rather than one shared
deadline. A streaming deadline keeps advancing before and between iterations.
Expiry raises `DockerTimeout` without guaranteeing that the daemon-side build
stops.

## Return Contracts

| Mode | Result |
| --- | --- |
| Blocking normal build with an identifiable locally loaded image | `Image` |
| Blocking build with push/nonlocal export and no local load | `None` |
| Blocking `call="check"`, `"outline"`, or `"targets"` | Captured report `str` |
| `stream_logs=True` | Owned `MappedProcessStream[str]`, not an image |

Don't access `.id` on an arbitrary build result. A successful build can return
`None`. Report calls bypass image recovery, and consuming a log stream doesn't
produce a final `Image` return value.

A normal blocking `bake()` returns `BakeResult` configuration by default: it
builds, then runs a separate `--print` query. That query isn't a build-time
snapshot. `return_config=False` skips it and returns `None`; `print=True` only
queries configuration and conflicts with streaming, report calls, and
`return_config=False`. Bake reports return `str`, and Bake streams yield decoded
`str`; neither performs the follow-up query. `variables={...}` overlays the
Bake subprocess environment on each invocation.

## Stream Decoded Build Logs

```python
from pywhales import docker

with docker.buildx.build(
    ".",
    tags="example-app:dev",
    load=True,
    progress="plain",
    stream_logs=True,
) as lines:
    for line in lines:
        print(line, end="")
```

These items are already `str`, unlike container and Compose `(source, bytes)`
streams. Drain the stream to detect CLI failure and use its context manager for
early-exit cleanup. `stream_logs=True` rejects `progress=False`, `"tty"`,
`"none"`, and `"quiet"`; use `"plain"` for readable automation logs.

## Check Without Building an Image

```python
from pywhales import docker

report = docker.buildx.build(".", call="check")
print(report)
```

`call="outline"` and `call="targets"` also return report text in blocking mode.
These frontend methods require Buildx support for `--call`; they aren't a
generic offline or side-effect-free sandbox. Check installed signatures and
plugin support before using newer options such as `no_cache_filter` and
`annotations`.
