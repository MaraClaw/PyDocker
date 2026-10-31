# Usage Validation

## Inspect the Installed Package First

Use the application's Python environment. This works outside a PyWhales
checkout and doesn't invoke Docker:

```python
from importlib.metadata import version
from inspect import signature

from pywhales import DockerClient, docker

print(version("pywhales"))
print(signature(DockerClient))
print(signature(docker.compose.up))
help(docker.compose.up)
```

Select only the methods needed by the task. `signature` establishes supported
keywords and help text explains intent. Match these checks to the output and
lifecycle guidance in the relevant reference rather than copying an example
from another Docker library. Client construction and introspection don't
establish daemon or plugin availability.

## Check the Matching Surface

1. Verify imports and the installed version without Docker. Parse or compile
   generated Python to catch syntax errors without running its Docker calls.
2. When engine access is authorized, check the selected target with
   `docker.version()` and check only the required CLI plugins.
3. Exercise the requested operation with owned disposable resources. For a
   foreground smoke check, use
   `docker.run("alpine:3.22", ["echo", "ok"], remove=True)` and expect `"ok"`.
   This can pull an image; don't run it under a no-Docker or no-network constraint.
4. Check the contract that matters: text content, detached object, exhausted
   stream, exported files, or a local image. Add a deliberate nonzero command
   such as `["false"]` only in an authorized disposable run to check error handling.
5. Report checks performed separately from checks blocked by unavailable
   engines, credentials, builders, or plugin versions. Don't call static
   signature checks an integration test.

For builds, validate the chosen exporter instead of assuming an `Image`.
For Compose, use a dedicated project and preserve persistent volumes unless
their deletion is requested. Bound log collection and release local streams
even when a reader stops early.

These examples target PyWhales 1.0.5. Recheck the installed version and supported
arguments when versions differ. Verify the user's application or script, not
the PyWhales repository's contributor tests.

Checking a `default_timeout`, `telemetry_observer`, or `environment` argument
follows the same signature-first rule: confirm it in
`signature(DockerClient)` before assuming it is supported. Opt-in admission
(`pywhales.admission`), bounded observation (the root `observation`,
`capture_inventory`, and `InspectionPolicy` exports, plus `bulk_reload` from
`pywhales.client_config`), and convergence (`pywhales.convergence`, covering
Swarm services/stacks and Compose projects) are separate imports that don't
change default `DockerClient` behavior; verify their presence the same way
before building on them. `pywhales.reconciliation`, `pywhales.profiles`, and
`pywhales.storage` are likewise opt-in imports that the package root doesn't
re-export.

If `signature()` raises `NameError` on an annotation that is imported only for
type checking, as it does for `bulk_reload`'s `InspectionPolicy`, pass
`annotation_format=annotationlib.Format.FORWARDREF` (or `Format.STRING`) after
`import annotationlib`.
