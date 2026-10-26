# Build performance and measurement

PyWhales forwards Docker CLI options. Build time usually depends on the build
graph, inputs, network, storage, and available CPU and memory. Fewer wrapper
subprocesses can reduce orchestration overhead, but command counts alone do not
measure a Docker speedup. This guide separates those measurements.

## Latest verified plugin versions

The repository installs Compose `5.6.1` and Buildx `0.38.1`, the latest verified
stable releases as of October 26, 2026. These exact Linux amd64 release assets
are SHA256-pinned, not floating `latest` downloads. Both downloads must pass
verification before either installed plugin is replaced. CI runs each of the
16 component selections once and all 18 smoke cases against these pins;
missing required capabilities fail rather than skip. Record the actual CLI,
Engine, Buildx, Compose, BuildKit, Dockerfile frontend, and driver versions on
your workstation. Compose's plugin version does not select a Compose file schema.

New wrapper options are additive and keyword-only. Unset options retain existing
behavior. Explicit flags are forwarded to the installed plugin without version
probes, automatic fallback, or retries with flags removed. Consult the
[Compose](https://docs.docker.com/reference/cli/docker/compose/),
[Buildx build](https://docs.docker.com/reference/cli/docker/buildx/build/), and
[Bake](https://docs.docker.com/reference/cli/docker/buildx/bake/) help for your
pinned versions. CLI support alone is insufficient
if the builder or Dockerfile frontend lacks the feature.

The examples below are recipes to adapt, not commands run by documentation
generation. They can build, push, or start services when you explicitly run them.

## Compose controls

```python
from pywhales import DockerClient

client = DockerClient(
    compose_files=["compose.yaml"],
    compose_parallel=2,
    compose_progress="plain",
)
client.compose.build(builder="ci-builder", progress="plain")

preview = DockerClient(compose_files=["compose.yaml"], compose_dry_run=True)
preview.compose.build()
```

`compose_dry_run`, `compose_parallel`, and `compose_progress` are constructor
options and emit no new flags by default. Dry-run previews operations; it does
not build images or create containers. Do not request returned container objects from a
dry-run. Method-level build `progress` overrides the global progress option for
that invocation only.

`compose.build()` also accepts `builder`, `check`, `print`, `provenance`, `sbom`,
and `push`. `check=True` asks the CLI to run build checks. `print=True` prints
the equivalent Bake definition to stdout, and the blocking wrapper still
returns `None`; it is not a parsed config API. Ordinary blocking builds also
return `None`. Streaming returns a closeable stdout/stderr byte-chunk stream,
including for printed config. Provenance and SBOM options accept booleans or
CLI-style strings such as `provenance="mode=min"`; consult the API reference for types.

Compose's `compose_parallel` / `--parallel` bounds concurrent Engine calls,
such as pulls and builds across services. Bake's `jobs` limits target execution
and can select failure/output scheduling behavior supported by that Buildx
release. Neither controls the internal BuildKit DAG's step concurrency. Raising
all concurrency limits can increase memory pressure and disk contention; change
one limit at a time and measure throughput as well as peak resource use.

### Watch, readiness, and process lifetime

Configure `develop.watch` in the Compose file. Prefer `sync` for source changes
when the application reloads itself, `sync+restart` for supported restart cases,
and `rebuild` for dependency changes. Ignore `.git`, virtual environments,
dependency trees, generated files, and editor artifacts. Check target ownership
and the required container utilities. Large trees, many small files, bind-mount
or VM filesystem boundaries, and application reloaders can dominate watch I/O.
Do not infer watch latency from an image-build benchmark.

```python
from pywhales import DockerClient

client = DockerClient(compose_files=["compose.yaml"])
with client.compose.watch(no_up=True, quiet=False, prune=False) as logs:
    for source, chunk in logs:
        print(f"{source}: {chunk.decode()}", end="")
```

`no_up=True` watches services already started by you; it skips watch's initial
build/start. `quiet` suppresses build output. `prune=False` is PyWhales' default
and explicitly disables removal of superseded images, unlike the CLI default.
Watch returns an owned log stream directly; unlike build/up, it has no
`stream_logs` switch. Iterating it follows the long-running CLI process. Context
exit closes/reaps that process if you stop iterating, without removing services
or reversing completed rebuilds. Passing `services=[]` is a no-op returning `None`.

`compose.up(watch=True)` combines startup with watch. For an attached test stack,
`compose.up(abort_on_container_failure=True)` stops the stack if a container
fails. `exit_code_from="tests"` chooses the service whose exit status the CLI
uses and implies abort-on-container-exit behavior. Nonzero CLI exits propagate
as Docker exceptions, not integer return codes. These modes have CLI constraints,
including incompatibilities with detached operation; choose one lifecycle recipe
instead of combining every switch.

`compose.up(wait=True, wait_timeout=60)` waits for services to be running or
healthy and implies detached mode. A running process is not necessarily ready
to serve requests. A healthcheck proves only its configured probe, so use a
probe that exercises the application contract and dependency readiness, with
bounded timeouts. Application-level smoke checks may still be necessary.
The CLI's `docker compose wait` and container wait operations wait for
termination; they are not readiness checks.

Bound logs and runtime resources per service, without changing the daemon:

```yaml
services:
  app:
    build: .
    cpus: "2.0"
    mem_limit: 1g
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Choose limits for the workload and check platform support. Application log volume
and synchronous log consumers can affect latency; streaming logs is not free.
Docker Desktop VM CPU, memory, disk capacity, and filesystem settings may impose
additional bounds. Check current subscription requirements and organizational
policy before using Desktop or paid synchronized-file-sharing/remote-build
features; basic Compose Watch should not be confused with those optional services.
This guide does not change daemon settings or assume paid features are available.

## Buildx and Bake controls

```python
from pywhales import DockerClient

client = DockerClient()
report = client.buildx.build(".", builder="ci-builder", call="check")
outline = client.buildx.build(".", builder="ci-builder", call="outline")
targets = client.buildx.build(".", builder="ci-builder", call="targets")
```

Report calls return captured text and skip image inspection and IID-file recovery.
They can still contact BuildKit or fetch frontend/base metadata: report-only is
not synonymous with offline. Leaving `call` unset preserves `Image | None` for
blocking builds; explicit `call="build"` retains normal building behavior.
Use `stream_logs=True` for an owned stream rather than captured report text.

`no_cache_filter=["dependencies"]` invalidates named Dockerfile stages instead
of disabling all layer cache. `annotations=["index:org.opencontainers.image.description=Example"]`
sets exporter metadata; it is distinct from `labels` in image configuration and
requires an output/registry that supports the selected annotation scope.

```python
config = client.buildx.bake(files=["docker-bake.hcl"], print=True)
client.buildx.bake(files=["docker-bake.hcl"], jobs=2, return_config=False)
checks = client.buildx.bake(files=["docker-bake.hcl"], call="check")
```

Default `bake()` builds and then queries `--print`, returning the merged config.
`return_config=False` preserves the build but skips the extra query and returns
`None`. `print=True` only queries configuration. Print cannot be combined with
streaming, report calls, or `return_config=False`. Report call modes return text without the
follow-up config query. `call="build"` follows the ordinary build/config rules.
Streaming returns a stream and does not run a post-stream config query.
For supported releases, `jobs=2` sets a target concurrency bound, while a string
such as `jobs="defer-error,parallel=2"` selects scheduling behavior explicitly.
This does not impose per-target CPU/memory limits.

## Cache, contexts, and layer layout

Start with small contexts and a reviewed `.dockerignore`. Exclude dependency
directories, VCS data, build output, and credentials. Copy dependency manifests
and lockfiles before frequently changing source, then install dependencies in
their own layer. Use multi-stage builds and narrow named `build_contexts` when
they avoid sending unrelated trees. Keep dependency versions and base image
digests stable while measuring; a moving base tag changes the experiment.

[External cache backends](https://docs.docker.com/build/cache/backends/) depend
on the builder driver, selected backend, BuildKit version, and image store.
The default `docker` driver supports `inline`, `local`, `registry`, and `gha`
with the containerd image store enabled. Do not assume every backend works with
every driver. A separately provisioned `docker-container` builder supports
external-cache workflows without relying on the Engine's classic image store;
its output is not automatically loaded into the local image store.

[Fresh Engine 29 installations](https://docs.docker.com/engine/storage/containerd/)
use the containerd image store by default, but upgraded classic-store installations
remain classic until explicitly migrated. An Engine version number alone does
not prove store capability. Inspect the actual setup; no image-store migration,
daemon restart, or default-builder change is part of this guide.

```python
client.buildx.build(
    ".",
    builder="ci-builder",
    tags="registry.example/team/app:revision",
    push=True,
    cache_from=[
        {"type": "registry", "ref": "registry.example/team/app-cache:branch"},
        {"type": "registry", "ref": "registry.example/team/app-cache:main"},
    ],
    cache_to={
        "type": "registry",
        "ref": "registry.example/team/app-cache:branch",
        "mode": "max",
    },
)
```

Use branch-scoped writable cache references. Concurrent jobs writing the same
reference overwrite each other's cache metadata; serialize those writers or use
run-specific destinations and an explicit promotion policy. Import branch and
main caches, but do not let untrusted branches overwrite trusted caches. Cache
exports can contain intermediate build material, so apply registry access policy.

For backends supporting it, `mode=min` exports layers needed for the final result;
`mode=max` also exports intermediate layers. Max can improve reuse but increases
transfer/storage and may be slower overall. Inline cache supports min mode, not
max. Measure import/export cost, not only cached build-step time.

Dockerfile `RUN --mount=type=cache` keeps package-manager/compiler working data
on that builder. These cache mounts are separate from exported layer cache and
are not automatically included by `cache_to`. An ephemeral builder importing
layer cache can still have empty cache mounts. Some package managers need
`sharing=locked`. See Docker's [cache optimization guide](https://docs.docker.com/build/cache/optimize/).

## Secrets, attestations, and multiple architectures

Use `secrets="id=token,src=/secure/token"` with Dockerfile secret mounts, or
`ssh="default"` with SSH mounts for private fetches. Do not put credentials in
`ARG`, `ENV`, copied files, URLs, or log output. Mounts keep the credentials out
of image layers when used correctly; a build command can still leak them by
printing or copying their contents. Secret contents do not invalidate layer
cache on rotation. If rotation must rerun a stage, use a non-secret revision
argument or a targeted `no_cache_filter`; never use the secret value as a cache key.

`provenance={"mode": "min"}` and `sbom=True` on Buildx request attestations.
[Max provenance](https://docs.docker.com/build/metadata/attestations/slsa-provenance/)
exposes build-argument values and detailed build information. Review metadata
before publication. Minimal provenance is less detailed, not an excuse to pass
credentials as build arguments. An attestation is metadata, not automatic trust
verification or vulnerability remediation.

The classic Docker image store cannot retain attestation-bearing image indices
or multi-platform images as the containerd store can. Registry push is a common
way to preserve attestations and multi-platform output without local loading;
check registry/exporter support. Do not infer retention merely from a successful
`load=True`. [SBOM scanning](https://docs.docker.com/build/metadata/attestations/sbom/)
defaults to the final stage. To scan build dependencies, declare
`ARG BUILDKIT_SBOM_SCAN_STAGE=true` in relevant stages (or the documented global
form); context scanning uses `BUILDKIT_SBOM_SCAN_CONTEXT`. Only built stages are
scanned. Measure the additional scan and upload costs.

`platforms=["linux/amd64", "linux/arm64"]` requests multi-platform output.
Compare native builder nodes, supported cross-compilation using `BUILDPLATFORM`
and `TARGETPLATFORM`, and QEMU emulation explicitly. Emulation can be expensive
for compilation and compression; cross-compilation must handle native libraries
and target toolchains correctly. Record the strategy and per-platform cache
state. See Docker's [multi-platform guide](https://docs.docker.com/build/building/multi-platform/).

## Daemon-free wrapper benchmark

From the repository root, with its existing dependencies installed:

```bash
.venv/bin/python scripts/benchmark_docker_overhead.py --help
.venv/bin/python scripts/benchmark_docker_overhead.py --repetitions 20
.venv/bin/python -m pytest -q tests/pywhales/test_docker_overhead_benchmark.py
```

The default is 20 iterations per scenario; the accepted range is 1..1000.
The script runs the actual public library calls and response parsing, replacing
only subprocess execution with controlled responses. Python's executable is a
resolvable client sentinel, never launched. No Docker binary, daemon, build
context, registry, images, or builder is needed. The image-return scenario uses
an owned temporary IID file, removed by the normal wrapper. There is no live mode.

JSON schema version 1 identifies the measurement as
`simulated_wrapper_overhead_not_docker_speedup` and reports `docker_executed=false`.
Each scenario contains its iteration count, total execution requests, builder
inspect/list request totals, and informational min/median/max timings in
nanoseconds. Divide request totals by iterations to obtain counts per operation:

| Scenario | Requests per operation | Builder inspect / list requests |
| --- | --- | --- |
| Bake, default config return | 2 | 0 / 0 |
| Bake, `return_config=False` | 1 | 0 / 0 |
| Image-returning build, simulated `docker` driver | 4 | 1 / 1 |
| Push-only build | 1 | 0 / 0 |
| Explicit OCI export | 1 | 0 / 0 |

The image-returning path includes the build and image inspection as well as the
builder lookup. The driver is resolved once per build when needed, not cached
globally across calls. The benchmark counts requests that would launch CLI
processes, not real subprocesses. Timings include Python command construction,
fake responses, parsing, and temporary-file work where applicable. They omit
process startup, Docker, BuildKit, network, and real build work; they are noisy
informational samples and must not be converted into claimed Docker speedups.

## Reproducible real-build measurement recipe

Run this separately and explicitly on a disposable CI runner or builder provisioned
for the experiment. It can consume CPU, network, disk, and registry quota. The
benchmark script above does not run it. Select an already provisioned builder by
name on each call; do not change the default builder or prune shared caches.

1. Save the source revision, dependency lockfiles, Dockerfile/frontend digest,
   base image digests, and full build options. Capture `docker version`,
   `docker compose version`, `docker buildx version`, `docker info`, and
   `docker buildx inspect YOUR_BUILDER` without `--bootstrap`. Record BuildKit,
   driver, image store, host/VM CPU and memory limits, architecture, output type,
   registry location, network conditions, and any background workload.
2. Use separate owned builders/runners for a genuinely cold state. A new cache
   destination alone does not make the builder cold. `cache=False` bypasses layer
   reuse but does not clear base images or cache mounts, so label such a run
   "layer-cache bypass", not "fully cold". Never run global prune as preparation.
3. Perform one cold build, then repeated unchanged warm builds on that builder.
   Export an external cache to an owned empty destination. Import it on a second
   fresh owned builder for the external-cache case; keep inputs and output equal.
   Run the baseline and candidate with the same cache-state sequence.
4. From a clean source fixture, change only one application source file and
   measure a source-only rebuild. In a separate fixture, change a dependency
   manifest and its lockfile together and measure the dependency rebuild.
   Preserve the patch and restore the fixture between samples. Do not edit the
   developer's checkout or mix these cases with changing base tags.
5. Repeat single-platform and multi-platform cases separately. Keep platforms,
   builder nodes, cross-compilation/emulation strategy, attestations, and exporter
   constant within each comparison. Include image load/push and cache export in
   end-to-end wall time, or report them separately with explicit boundaries.
6. Save plain or raw-JSON progress, metadata files, exit status, wall time, and
   CPU/peak-memory/I/O observations on the builder host or VM. Capture context
   transfer bytes, cache import/export bytes, and image bytes from build logs
   or registry/network instrumentation. Count cache-hit steps from progress; do
   not infer bytes from elapsed time. Report unavailable metrics as unavailable.
7. Use multiple samples per case (for example five), retain raw observations,
   and report median plus spread. Note failures and throttling. Alternate candidate
   order where practical without contaminating the intended cache states. Compare
   like-for-like outputs before claiming an improvement. Clean up only resources
   owned by this experiment after retaining evidence.

For example, this opt-in single-platform call captures metadata and uses a
run-owned local cache destination. Supply an existing dedicated builder; the call
does not create a builder or change the default. Keep output outside the build
context. This example retains a new temporary directory for inspection:

```python
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter
from pywhales import DockerClient

client = DockerClient()
measurement_dir = Path(mkdtemp(prefix="pywhales-measurement-"))
start = perf_counter()
client.buildx.build(
    ".",
    builder="measurement-builder",
    platforms=["linux/amd64"],
    progress="plain",
    output={"type": "oci", "dest": measurement_dir / "image.tar"},
    cache_to={"type": "local", "dest": measurement_dir / "cache", "mode": "max"},
    metadata_file=measurement_dir / "metadata.json",
)
print({"measurement_dir": str(measurement_dir), "wall_seconds": perf_counter() - start})
```

Save stdout/stderr with the run. For the external-cache comparison, set
`cache_from={"type": "local", "src": measurement_dir / "cache"}` on the second
fresh builder and use a new owned output directory. This timer is end-to-end
wrapper wall time; it does not by itself provide CPU, peak memory, transferred
bytes, or attribution of time to individual build phases.
