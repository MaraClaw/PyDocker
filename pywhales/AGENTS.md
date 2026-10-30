# Shared Runtime Internals

## Local Navigation

- `client_config.py`: command prefixes, Compose globals, and reloadable resource bases.
- `_execution_policy.py`: policy carrier and launch snapshots; `_bulk_reload.py`: staged cache refresh.
- `_inspect_validation.py`: whole-response validation for context, plugin, and task batches.
- `utils.py`: `run()`, stream factories/adapters, CLI value conversion, and JSON aliases.
- `_process_stream.py`: process ownership and watchdog; `_stream_buffer.py`: queue accounting.
- `_readiness.py`: wake latch, readiness wait, and child-exit helpers shared by pipe workers, capture, and archives.
- `exceptions.py`: captured command details and stderr-based exception selection. Kinds are an ordered
  table of line-start patterns; add a kind there, not as a substring test. Names and context paths
  can't pick a kind. Application stderr forwarded by `run`/`exec` can still be misclassified, by design.

## Commands And Configuration

- `DockerCLICaller` retains its `ClientConfig`; resources must keep the originating config.
- `Command.add_simple_arg()` omits only `None`, preserving values such as `0` and `False`.
  `add_flag()` emits a flag for truthy values; optional Docker globals use `is True`.
- Iterable helpers repeat the option for each value; mapping helpers produce `key=value` entries.
- Executable lookup is lazy: `get_client_call_with_path()` resolves `client_call[0]` with
  `shutil.which()` and caches the resolved executable plus the remaining prefix arguments.
  `_client_call_with_path` caches that prefix, not the global options rebuilt by `docker_cmd`.
- `docker_cmd` uses `PolicyExecutable`, a `str` carrier retaining the originating `ClientConfig`
  through `Command`/plain-list composition, `copy()`, and `deepcopy()`. Resolve `get_execution_policy()`
  before stringifying argv. Each launch snapshots the current policy; later mutations affect new launches only.
- Compose globals belong after `compose` and before the subcommand. Use `get_compose_command()`;
  its explicit `progress` argument overrides configured progress for that call only.
- Preserve `compose_env_files` order and repeated `--env-file` arguments; later files override earlier values.

## Resource Identity And Models

- `ReloadableObject` resolves a mutable reference through inspect to store an immutable ID.
  `is_immutable_id=True` skips that initial fetch; later reloads use the stored ID.
- Inspect snapshots expire after `CACHE_VALIDITY_PERIOD = 0.01` seconds; `reload()` forces a fetch.
  Properties using `_get_inspect_result()` can run inspect when the snapshot is absent or stale.
- Equality compares both immutable ID and `ClientConfig` equality, not just resource names.
- `DockerCamelModel` accepts Python field names and generated Docker aliases via `populate_by_name`.
  Keep acronym exceptions in `to_docker_camel()`, such as `ExecIDs`, `IPAddress`, `OOMKilled`,
  `RootFS`, and `DefaultAPIVersion`; ordinary title-casing doesn't reproduce these keys.

## Bulk Reload

- `client_config.bulk_reload()` delegates to `_bulk_reload.py`. Supported kinds are Container,
  Image, Network, Volume, Node, Service, Config, Secret, Task, Plugin, and Context.
- Reject unsupported kinds and missing immutable identities across all input before any CLI lookup.
  Empty input is a no-op. Group by exact `ClientConfig` object identity plus resource kind, not config equality.
- Stage and validate every group's snapshots before changing original caches or refresh timestamps.
  Publish once per distinct handle, including separate handles for the same identity; repeated handles publish once.
  Staged identities must equal stored identities exactly. Don't use freshness-triggering accessors during staging.
- Failure in any group leaves all original caches/timestamps unchanged. This isn't a daemon transaction
  or an atomic publication to concurrent readers; groups are inspected separately and handles publish sequentially.

## Subprocesses And Ownership

- `run()` uses `subprocess.run()` when neither a timeout nor byte input is supplied.
  Measured-input and deadline paths use `Popen.communicate()` with owned stdin-writer cleanup.
- `stream_stdout_and_stderr()` starts `Popen` immediately and returns a `ProcessStream` yielding
  `(source, bytes)` records. `MappedProcessStream` carries ownership through output transforms.
- A timeout is `None` or positive finite seconds, never `bool`; validate it before the selected launch.
  Launch-based monotonic deadlines keep advancing while stream consumption pauses or never starts.
  Natural child exit leaves post-exit pipe draining unchanged.
- Expiry kills and reaps the local CLI before raising `DockerTimeout`, retaining captured output.
  Natural nonzero exits keep stderr-selected `DockerException` types rather than becoming timeouts.
- A bare loop `break` doesn't close an owned stream. Use its context manager or call `close()`
  when stopping early, including before the first iteration. Closing cancels only the local CLI,
  not containers, services, or other daemon-managed resources.
- `close()` is idempotent and waits for process/thread cleanup. Preserve recorded terminal errors
  for one delivery through iteration or clean context exit; don't replace an active body exception.
- POSIX pipe workers wait on readiness, not timers: stream readers, the stdin writer, and bounded capture
  block on the pipe plus a `WakeLatch`, and a reaper thread publishes child exit instead of `poll()` loops.
  Only a real deadline (its remaining time), a cancel token (bounded 50 ms check), or Windows pipes
  (bounded slices) may time a wait. Every stop path must `set()` the latch; `close()` it only after the
  threads waiting on it are joined. `ArchiveDeadline` waits under its pipe lock for at most the remaining
  deadline, so expiry never closes a pipe beneath a wait.

## Buffer And Error Invariants

- `ClientConfig.stream_buffer_bytes=None` is unbounded; a budget must be a positive integer, not `bool`.
  At the shared stream boundary, omitted `buffer_bytes` uses `DEFAULT_BUFFER` to inherit that budget;
  explicit `buffer_bytes=None` stays unbounded. Don't collapse the sentinel and `None`.
- `image_stream_buffer_bytes` is an independent pull/push override: non-`None` wins, otherwise
  use the general budget. Don't normalize the fallback into the config; see the image guide for operation snapshots.
- Queued payload shares one budget across stdout/stderr; each pipe's unfinished record and retained
  stderr history have separate bounds. The budget isn't a single cap on total process memory.
- Oversized records, including their newline, or stderr history raise `BufferError`; queue pressure
  blocks producers instead of dropping output. EOF notifications don't consume payload bytes.
- Closing or failing must wake blocked readers/producers. Keep terminal errors outside the payload
  queue so saturation can't block delivery, and don't erase an already recorded error on `close()`.

## Diagnostic Confidentiality

- PyWhales-owned automatic diagnostics omit arbitrary argv, environment values, and captured payloads
  by default, including debug printing, logger records, and exception text. `raw_diagnostics=True`
  is a per-client opt-in for trusted troubleshooting output, not a process-wide switch.
- Keep exception classification, exit codes, and raw `docker_command`/`stdout`/`stderr` attributes intact.
  Those attributes remain confidential. Explicit output, uncaptured terminal output, and caller logging
  are outside this policy; safe diagnostics don't redact returned data or prevent its disclosure by callers.
- Safe inspect failures must not write response dumps or chain input-bearing validation errors.
  Raw opt-in can expose both; preserve the distinction through scalar and batch parsing.

## Related Tests

- `tests/pywhales/test_client_config.py`: env-file ordering, originating config, and safe/raw inspect parsing.
- `tests/pywhales/test_bulk_reload.py`: all supported kinds, grouping, validation, and deferred publication.
- `tests/pywhales/test_inspect_diagnostics.py` and `tests/pywhales/test_logging.py`: safe/raw boundaries and command-copy isolation.
- `tests/pywhales/test_compose_modernization.py`: `test_globals_precede_subcommand_when_explicit`.
- `tests/pywhales/test_utils.py`: `test_run_timeout_preserves_captured_output`, owned-stream cleanup,
  deadline delivery, queue budgets, and `test_process_stream_close_after_timeout_does_not_erase_error`.
