# Registry Transfers and Archives

`docker.pull`, `docker.push`, `docker.save`, and `docker.load` are aliases of
`docker.image.pull`, `docker.image.push`, `docker.image.save`, and
`docker.image.load`. They move images between the daemon, a registry, and
local tar archives.

## Pull And Push

```python
from pywhales import docker

image = docker.pull("alpine:3.22")
images = docker.pull(["alpine:3.22", "busybox:latest"])
docker.push("example.registry/app:release")
```

Iterable references are deduplicated in first-seen order before transfer.
Blocking calls run eagerly: one reference pulls or pushes during the call;
several unique references use up to four concurrent workers. `pull([])`
returns `[]` without starting a CLI. `push([])` returns `None`, even with
streaming; this is a deliberate asymmetry between the two methods, not a bug
to normalize away.

`pull([], stream_logs=True)` instead returns an empty plain generator, with no
CLI launch; use `contextlib.closing` rather than assuming a stream context manager.

`push` batch-inspects every deduplicated reference before starting any
transfer, including streamed ones, so a missing local image raises
`NoSuchImage` before a registry connection is attempted.

| Call | Blocking result | `stream_logs=True` result |
| --- | --- | --- |
| `docker.pull("x")` | `Image` | Owned `(image_reference, bytes)` stream |
| `docker.pull(["x", "y"])` | `list[Image]`, even for one unique reference | Owned stream across up to four workers |
| `docker.push("x")` | `None` | Owned `(image_reference, bytes)` stream |
| `docker.push(["x", "y"])` | `None` | Owned stream, or `None` for `[]` |

Streaming starts eagerly for one unique reference; for several, workers start
on first consumption of the stream, not at construction or context entry, and
at most four concurrent transfers run at once. A bare loop `break` doesn't
close the stream; exhaust it, use its context manager, or call `close()`.
For a multi-reference stream, the first worker's error stops sibling and
queued transfers without waiting for further consumption; that error is
delivered once through iteration, including after an explicit `close()`.
Closing cancels only local CLI processes, not registry pushes or daemon-side
transfers already in progress.

`pull` accepts a keyword-only `timeout` in positive finite seconds, replacing
`default_timeout` for that call. Each concurrent transfer gets its own
deadline, and image inspection or executor queueing doesn't consume it. `None`
inherits the client default rather than disabling it.
`push` has no `timeout` parameter; its preflight inspection and transfers
follow the client's `default_timeout` unmodified. `quiet=True` can't be
combined with `stream_logs=True` on either call.

`docker.manifest.push(..., timeout=30)` and
`docker.plugin.push(..., timeout=30)` also accept keyword-only local CLI
deadlines; a `Plugin` resource's `push(timeout=30)` forwards the same override.
Manifest preflight inspection is outside the selected push budget. These
blocking operations return `None`. Plugin `enable(timeout=...)` is a different,
daemon-side option, not this per-launch deadline.

`image_stream_buffer_bytes` on `DockerClient`/`ClientConfig` is an
independent override for pull/push progress-stream budgets; when it's `None`
(the default), the general `stream_buffer_bytes` applies instead. This progress
budget is selected when the public pull/push operation starts. Direct archive
bytes aren't progress records and aren't limited by these budgets; path-based
`load` still uses the general budget for its progress output.

## Save And Load

```python
from contextlib import closing

from pywhales import docker

docker.save("alpine:3.22", output="alpine.tar")
docker.load("alpine.tar")

with closing(docker.save("alpine:3.22")) as chunks:
    with open("alpine-streamed.tar", "wb") as output:
        for chunk in chunks:
            output.write(chunk)
```

`save(images, output=None)` inspects every requested image eagerly, raising
`NoSuchImage` immediately for a missing one, before doing anything else. An
explicit `output` path blocks and returns `None`. Omitted `output` returns a
lazy generator of raw archive-byte chunks, not `(source, bytes)` progress
records: its save subprocess starts on first iteration, not on the call
itself. Exhaust or explicitly close that generator to leave its subprocess
context; if it's discarded unconsumed, no save subprocess or archive-save
telemetry starts. The eager inspection can still emit its own telemetry.

`load(input, quiet=False)` accepts a path, complete bytes, or an iterator of
byte chunks. Path input runs an eager, process-backed progress stream,
printing decoded lines unless `quiet=True`. Byte or iterator input is written
to a local CLI's own stdin and fully consumed during the call; these archive
bytes aren't progress-log records, so a configured `default_timeout` still
reaps the local CLI even if the input iterator stalls, though the iterator's
own Python code can't be interrupted. Both forms return `list[str]` of the
image tags/IDs parsed from `Loaded image` output lines, in output order.

## Telemetry Families

With `telemetry_observer` set, the byte-based `save()` generator reports under
`archive_save`, and the byte/iterator `load()` path under `archive_load`.
Path-based `--input`/`--output` transfers and ordinary
pull/push progress streams report under the regular `captured`/`stream`
families instead. See
[Telemetry and admission](telemetry-and-admission.md) for the full schema.

## Other Operations

`docker.tag(...)` and `docker.images()` (`image.list()`) cover local tagging
and discovery. `image.remove(...)` and `image.prune(...)` delete images;
`prune` suppresses Docker's confirmation prompt, so require explicit deletion
authorization. An `Image` context manager removes the image on exit: never use
it as automatic cleanup for a borrowed image. `Image.copy_from()`/`Image.copy_to()` use a
temporary container, removed on exit, to move files between the local
filesystem and an image; both forward `pull`, defaulting to `"missing"`, to
container creation. `docker.import_(source, tag=...)` imports a tarball's
contents as a new filesystem image; it is unrelated to `load`, which restores
images previously produced by `save`/`docker save`.
