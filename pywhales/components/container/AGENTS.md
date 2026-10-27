# Container Component

## Navigation

- `cli_wrapper.py`: `Container`, `ContainerCLI`, launch/execute overloads, and `ContainerStats`.
- `models.py`: inspect snapshots, including state, host config, mounts, and network settings.
- `top.py`: Docker `ps` option grammar used by `ContainerCLI.top()`.

## Cleanup And Launch

- `Container.__exit__()` reads host config and state, stops only when `running is True`,
  then calls `remove(volumes=True)` unless `auto_remove is True`.
  Tolerate `NoSuchContainer` during inspection, stop, or removal; keep `resource_cleanup()`.
- Both `run()` and `create()` use `ImageCLI(self.client_config)` before launching:
  `pull="missing"` calls `_pull_if_necessary()`; `pull="always"` calls `pull(str(image))`.
  Keep shared option forwarding aligned between their separate argument builders.
- `run()`, `create()`, and `execute()` require an argv sequence for `command`.
  A shell command string raises `TypeError`; don't split it implicitly.

## Output Modes

- Preserve `run()` overloads: detached calls return `Container`, streamed calls yield
  `(source, bytes)` records, and ordinary foreground calls return `str`.
  `detach=True` with `stream=True` raises `ValueError`.
- `execute()` returns `None` when detached, `ProcessStream` when streamed, otherwise `str`.
  Both TTY plus streaming and detach plus streaming are invalid combinations.
- `start()` streaming requires `attach=True` and a single container; attaching multiple
  containers is invalid. Unattached calls return `None`; attached, nonstreamed calls return `str`.
- `ContainerCLI.logs()`, its root alias `docker.logs()`, and `Container.logs()` accept keyword-only
  `timeout` for the local logs launch; the handle still returns `str`. Inspect preflight runs outside
  this deadline. Expiry raises `DockerTimeout` without stopping the container; post-exit draining is unchanged.
- Logs use the originating client's general `stream_buffer_bytes` at the shared stream boundary,
  including when collecting text. Keep the owned transport closed on collection/decoding failure.
  `follow=True` without `stream=True` can block until the container stops.

## Batch Inspection And Top

- Batch `inspect()` uses one command and preserves request order and duplicates.
  Validate response count, model data, and reference identity before caching snapshots:
  accept matching ID prefixes or names with an optional leading slash.
  An empty batch returns `[]` without resolving the executable.
- `top()` returns `ContainerTopResult` by default; `raw=True` returns CLI text.
  Docker `ps` tokens follow the container argument; don't apply the Compose grammar.
  Parsed mode supports explicit `-o`/`--format` fields, including heading aliases.
  Unsupported fields, headerless output, and ambiguous implicit-column selections require
  `raw=True`; keep free-text command fields last when requesting parsed output.

## Matching Tests

Paths below are relative to the repository root.
- `tests/pywhales/components/test_container.py`: mocked top, batch inspection, cleanup races,
  eager-log decoding cleanup, and streaming-log ownership; also contains live component tests.
- `tests/pywhales/test_container_image_enum_json.py`: mocked filter argv and enum/JSON snapshots.
- `tests/pywhales/components/test_command_deadlines.py`: launch/log validation, partial output, reaping, and output shapes.
- `tests/pywhales/components/test_deadline_forwarding.py`: execution/log budgets, handle/root aliases, and unbudgeted preflights.
- `tests/pywhales/test_command_deadline_types.py`: static run/execute/log overloads, handle forwarding, and aliases.
- `tests/pywhales/test_type_contracts.py`: static inspect and parsed/raw top return contracts.
