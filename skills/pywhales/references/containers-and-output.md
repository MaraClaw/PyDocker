# Containers, Output, and Lifecycle

## Translate a Foreground Run

```python
from pywhales import docker

output = docker.run(
    "alpine:3.22",
    ["printenv", "GREETING"],
    envs={"GREETING": "hello"},
    remove=True,
)
print(output)
```

This captures stdout as `str`, inherits stderr, and removes the disposable
container when it exits. Commands must be sequences, not `"printenv GREETING"`.
Use string values in `envs`.

| CLI option | Python shape |
| --- | --- |
| `-e KEY=value` | `envs={"KEY": "value"}` |
| `-p 8080:80` | `publish=[(8080, 80)]`, host port first |
| `-p 127.0.0.1:8080:80` | `publish=[("127.0.0.1:8080", 80)]` |
| `-v source:/work:ro` | `volumes=[(source, "/work", "ro")]` |
| `--rm` | `remove=True` |
| `-d` | `detach=True` |

Use an absolute host path for a bind mount; `pathlib.Path` is accepted. For
example, with `source = Path.cwd().resolve()`, mount that directory read-only
and run `["ls", "/work"]`. Import `Path` from `pathlib`. A remote engine must
have the source path on its own host.

## Choose the Result Type

| Call mode | Result |
| --- | --- |
| `docker.run(...)` | `str` containing captured stdout |
| `docker.run(..., detach=True)` | `Container` |
| `docker.run(..., stream=True)` | Owned `ProcessStream` yielding `(source, bytes)` |
| `docker.execute(container, [...])` | `str` containing stdout |
| `docker.execute(container, [...], detach=True)` | `None` |
| `docker.execute(container, [...], stream=True)` | Owned `ProcessStream` yielding `(source, bytes)` |
| `docker.logs(container)` | `str` assembled from stdout/stderr log chunks |
| `docker.logs(container, stream=True)` | Owned `ProcessStream` yielding `(source, bytes)` |
| `docker.ps(all=True)` | `list[Container]`, including stopped containers |

For stream tuples, `source` is `"stdout"` or `"stderr"`. Don't decode the tuple;
decode its byte payload. Don't combine `detach=True` with streaming. For
`execute`, `tty=True` is also incompatible with streaming. Leave TTY allocation
off for captured automation; use `interactive=True, tty=True` for an actual
interactive terminal session.

## Own Detached Containers and Streams Separately

```python
from pywhales import docker

with docker.run("alpine:3.22", ["sleep", "300"], detach=True) as container:
    print(docker.execute(container, ["echo", "ready"]))
    with docker.logs(container, tail=20, stream=True) as logs:
        for source, chunk in logs:
            print(f"{source}: {chunk.decode(errors='replace')}", end="")
```

The outer context owns a newly created disposable container. On exit it stops
the container if still running, then removes it with `volumes=True` unless
Docker auto-removal applies. Don't use this context pattern around an existing
container returned by inspect or list: exiting would remove someone else's
resource. Keep persistent data out of this disposable pattern.

The inner context owns only the local CLI process, pipes, and reader threads.
Consume the stream to exhaustion to observe a nonzero command exit as
`DockerException`. Exiting early or calling `close()` cancels and reaps the
local CLI; that cancellation isn't proof the command succeeded and doesn't
remove daemon-managed containers. A bare loop `break` doesn't close a stream.

`follow=True` can keep log collection open indefinitely. Prefer a snapshot or
bounded `tail` unless following is requested; if following, provide a deliberate
cancellation boundary and keep the stream in a context manager.

Container properties such as `name` and `state` can issue inspect calls and use
a short-lived cache. Read a property once into a local variable for a coherent
decision; use `container.reload()` when an explicit refresh is required.
