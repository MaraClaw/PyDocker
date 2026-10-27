# Buildx and imagetools

This guide also applies to `imagetools/`; paths below are relative to this directory unless prefixed with `tests/`.
`cli_wrapper.py` holds `BuildxCLI`, `Builder`, and `stream_buildx_logs()`; `models.py` holds builder snapshots.

## Build returns and aliases

- `docker.build()` and `docker.image.build()` route to `BuildxCLI.build()`. Keep their overloads aligned.
- Normal blocking builds (`call=None` or `"build"`) return one `Image` only when the result is
  loaded into the daemon and can be identified; otherwise they return `None`.
- `_build_will_load_image()` checks `load=True` (load), then `push=True` (no load), then `output`:
  only `type="docker"` without `dest` loads. With no explicit choice, driver `"docker"` loads by default.
- Driver `"docker"` recovers the immutable image ID through a temporary `--iidfile`.
  Other drivers recover loaded images through the first tag. Untagged `"docker-container"` loads return `None`.
- Blocking `call="check"`, `"outline"`, or `"targets"` returns captured report text and skips builder/image recovery.
- `stream_logs=True` returns `MappedProcessStream[str]` before recovery or report capture.
  `stream_buildx_logs()` decodes `(source, bytes)` tuples with replacement and discards the source labels.
- Build streaming rejects `progress=False`, `"tty"`, `"none"`, and `"quiet"`.

## Bake configuration

- A normal blocking bake returns `BakeResult` by default: it builds, then runs a second command with `--print`.
  Keep options, targets, and the `variables` environment on both calls; `jobs=0` must be forwarded.
- `return_config=False` skips that second call and returns `None`; it doesn't suppress report text.
- `print=True` only queries config. It conflicts with streaming, `return_config=False`, and report calls.
- Reports return text; streams return `MappedProcessStream[str]`. Neither performs a follow-up config query.

## Builder snapshots

- `Builder` reloads by matching its name in line-delimited JSON from `buildx ls --format "{{json . }}"`.
- `list()` deduplicates JSON lines and seeds each builder snapshot. `inspect()` reads the name from
  the first human-readable output line, then finds the matching builder through `list()`.
- `Builder` context exit calls `remove()`, which delegates to `BuildxCLI.remove()`.
- `disk_usage()` raises `NotImplementedError`; don't document it as a working command.

## Registry manifests

- Imagetools works on registry manifests, not local `Image` objects; inspecting doesn't pull an image.
- `imagetools/cli_wrapper.py::inspect()` runs `imagetools inspect --raw` and parses a `Manifest`.
- `create()` requires sequences for `sources`, `tags`, and `files`; a bare string is rejected.
  `annotations` must be a mapping. Sources already exist in the registry.
- `dry_run=True` parses the proposed manifest into `Manifest`; normal create returns `None`.
- `imagetools/models.py` supports a manifest's `config`/`layers` and an index's `manifests` variants,
  with explicit `mediaType` and `schemaVersion` aliases.

## Related tests

- `tests/pywhales/test_buildx_modernization.py`: mocked build aliases, recovery, reports, and bake command counts.
- `tests/pywhales/test_buildx_modern_types.py` and `test_type_contracts.py` in the same directory:
  static return-type contracts, including dynamic flags and aliases; checked by ty, not pytest.
- `tests/pywhales/components/buildx/imagetools/test_contracts.py`: mocked create defaults.
  Its sibling `test_models.py` parses manifest JSON fixtures.
- `tests/pywhales/components/buildx/test_buildx_cli_wrapper.py` and sibling `imagetools/test_imagetools_cli_wrapper.py`
  cover integration behavior. Global-client builder switches and pruning follow the safety rules in `tests/AGENTS.md`.
