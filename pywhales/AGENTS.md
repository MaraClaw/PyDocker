# Shared Runtime Internals

## Local Navigation

- `client_config.py`: command prefixes, Compose globals, and reloadable resource bases.
- `utils.py`: `run()`, stream factories/adapters, CLI value conversion, and JSON aliases.
- `_process_stream.py`: process ownership and watchdog; `_stream_buffer.py`: queue accounting.
- `exceptions.py`: captured command details and stderr-based exception selection.

## Commands And Configuration

- `DockerCLICaller` retains its `ClientConfig`; resources must keep the originating config.
- `Command.add_simple_arg()` omits only `None`, preserving values such as `0` and `False`.
  `add_flag()` emits a flag for truthy values; optional Docker globals use `is True`.
- Iterable helpers repeat the option for each value; mapping helpers produce `key=value` entries.
- Executable lookup is lazy: `get_client_call_with_path()` resolves `client_call[0]` with
  `shutil.which()` and caches the resolved executable plus the remaining prefix arguments.
  `_client_call_with_path` caches that prefix, not the global options rebuilt by `docker_cmd`.
- Compose globals belong after `compose` and before the subcommand. Use `get_compose_command()`;
  its explicit `progress` argument overrides configured progress for that call only.
- Nonempty `compose_env_files` wins over deprecated `compose_env_file`, warning if both are set.
  Preserve file order and repeated `--env-file` arguments; later files override earlier values.

## Resource Identity And Models

- `ReloadableObject` resolves a mutable reference through inspect to store an immutable ID.
  `is_immutable_id=True` skips that initial fetch; later reloads use the stored ID.
- Inspect snapshots expire after `CACHE_VALIDITY_PERIOD = 0.01` seconds; `reload()` forces a fetch.
  Properties using `_get_inspect_result()` can run inspect when the snapshot is absent or stale.
- Equality compares both immutable ID and `ClientConfig` equality, not just resource names.
- `DockerCamelModel` accepts Python field names and generated Docker aliases via `populate_by_name`.
  Keep acronym exceptions in `to_docker_camel()`, such as `ExecIDs`, `IPAddress`, `OOMKilled`,
  `RootFS`, and `DefaultAPIVersion`; ordinary title-casing doesn't reproduce these keys.

## Subprocesses And Ownership

- `run()` uses `subprocess.run()` without a timeout, or `Popen.communicate()` with a deadline.
- `stream_stdout_and_stderr()` starts `Popen` immediately and returns a `ProcessStream` yielding
  `(source, bytes)` records. `MappedProcessStream` carries ownership through output transforms.
- A timeout is `None` or positive finite seconds, never `bool`; validate it before launching.
  Launch-based monotonic deadlines keep advancing while stream consumption pauses or never starts.
- Expiry kills and reaps the local CLI before raising `DockerTimeout`, retaining captured output.
  Natural nonzero exits keep stderr-selected `DockerException` types rather than becoming timeouts.
- A bare loop `break` doesn't close an owned stream. Use its context manager or call `close()`
  when stopping early, including before the first iteration. Closing cancels only the local CLI,
  not containers, services, or other daemon-managed resources.
- `close()` is idempotent and waits for process/thread cleanup. Preserve recorded terminal errors
  for one delivery through iteration or clean context exit; don't replace an active body exception.

## Buffer And Error Invariants

- `buffer_bytes=None` is unbounded; a configured budget must be a positive integer, not `bool`.
- Queued payload shares one budget across stdout/stderr; each pipe's unfinished record and retained
  stderr history have separate bounds. The budget isn't a single cap on total process memory.
- Oversized records, including their newline, or stderr history raise `BufferError`; queue pressure
  blocks producers instead of dropping output. EOF notifications don't consume payload bytes.
- Closing or failing must wake blocked readers/producers. Keep terminal errors outside the payload
  queue so saturation can't block delivery, and don't erase an already recorded error on `close()`.

## Related Tests

- `tests/pywhales/test_client_config.py`: env-file precedence and originating-config reloads.
- `tests/pywhales/test_compose_modernization.py`: `test_globals_precede_subcommand_when_explicit`.
- `tests/pywhales/test_utils.py`: `test_run_timeout_preserves_captured_output`, owned-stream cleanup,
  deadline delivery, queue budgets, and `test_process_stream_close_after_timeout_does_not_erase_error`.
