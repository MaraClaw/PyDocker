# Changelog

## 1.0.6

### New Features

- `InspectionPolicy.max_total_stream_bytes` optionally bounds combined stdout and
  stderr read by streams and image archive transfers in an observation scope.
  Nested scopes charge every enclosing stream budget independently of captured
  response budgets. Overflow stops the local CLI and raises
  `ObservationLimitError` with reason `total_stream`.
- Service create, update, rollback, and scale; Swarm CA and join; stack deploy;
  and plugin install and upgrade accept keyword-only local CLI timeouts. The
  service update/scale and plugin upgrade handles forward these deadlines.
  Preflight and postflight inspection remain outside the selected launch budget;
  each Swarm CA launch receives a fresh budget.

### Behavior Changes

- Buildx builder reload/list, Compose config/ls/events, system disk-free/events/info,
  and imagetools manifest parsing raise `pywhales.client_config.ParsingError` for
  malformed responses. This is not a `ValueError` subclass. Safe diagnostics
  suppress payload-bearing exception chains; `raw_diagnostics=True` chains the
  original parsing error. Event streams snapshot this setting at launch.
- Telemetry records use schema version 4. Observation cancellation reports
  `cancelled`, and observation-limit rejection after launch reports `limit`,
  including stream-byte budget overflow. Observation deadlines still report
  `timeout`; stream-buffer errors and `KeyboardInterrupt` remain `failure`.
  Prelaunch rejections emit no records.

### Internals

- Added an internal JSON utility with a native orjson pair and a standard-library
  compatible pair. Existing CLI decoders, canonical hashes, diagnostic files,
  delivery REST helpers, and the overhead benchmark use the compatible pair to
  preserve accepted inputs, error types, and serialized bytes.
  The fleet contract and harness, the isolated distribution smoke child, and
  test oracles stay on the standard library.

## 1.0.5

### New Features

- Capture selected Compose project services and wait for their containers to be
  running and healthy. Changed membership, service labels, or configuration
  hashes supersede the capture; a one-shot container that exits 0 remains a
  blocker.
- Inventory captures and bulk reloads support Buildx builders. Each builder
  group uses one strictly parsed listing, and cache updates publish only after
  all groups succeed.
- Image and Compose push, container start, Compose watch, and Buildx prune accept
  per-call timeouts for the local CLI. Each image transfer has its own deadline;
  the preceding image inspection and queueing are excluded. Timeout does not
  undo daemon or registry work.

### Release Process

- GitHub delivery pagination now enforces item and byte caps, rejects duplicate
  identities, and validates consistent `total_count` values for jobs and
  artifacts. Truncation of bare-array release and commit-pull listings remains
  undetectable.

## 1.0.0

### Behavior Changes

- Multi-image `pull()` and `push()` now behave like single-image transfers
  inside `observation()`: each pool task and stream worker runs in its own copy
  of the caller's context, so `pull(quiet=False)` in a scope raises the same
  `ValueError` for one image or many.
- Streams and archive transfers started inside `observation()` now honor its
  cancel tokens, deadline, and launch limits. When the scope ends first, the
  local CLI is killed and iteration raises `ObservationTimeout` or
  `ObservationCancelled`. Each transfer keeps the scope it launched with after
  the `with` block exits, and a lazy `save()` archive uses the scope active at
  its first iteration.
- Missing-resource errors are now classified only from the kind phrase that
  starts a stderr line, so a resource name or a file path no longer selects the
  exception type. For example, a missing network named `service-foo` now raises
  `NoSuchNetwork` instead of `NoSuchService`.

### New Features

- Add `NoSuchConfig`, `NoSuchSecret`, `NoSuchPlugin`, and `NoSuchContext`.
- Config, secret, plugin, and context handles gain `exists()`.
- Config, secret, plugin, and service context managers exit cleanly when their
  object was already removed; other cleanup failures still propagate.
- `config.list()`, `system.events()`, `system.prune()`, `buildx.prune()`, and
  `compose.ls()` accept an iterable of `(key, value)` filter pairs as well as a
  mapping, so a filter key can be repeated.

### Release Process

- GitHub release bodies are now the sealed `CHANGELOG.md` section for the
  released version instead of generated notes. A release fails to seal when that
  section is missing, empty, duplicated, or oversized.

## 0.6.0

### Breaking Changes

- `stack.deploy()` no longer accepts `orchestrator`; omit the argument.
- Use `client_call` instead of `client_binary`.
- Use `compose_env_files` instead of `compose_env_file`.
- Container, image, network, volume, service, and secret filters now accept an
  iterable of `(key, value)` tuples instead of a mapping.
- Set `PYWHALES_DEBUG=1` to enable execution diagnostics.
