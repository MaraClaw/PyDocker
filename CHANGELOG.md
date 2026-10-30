# Changelog

## 0.7.0

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
