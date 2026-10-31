# Shared Runtime Internals

## Configuration And Policy

- Resources keep the originating `ClientConfig`; equality is immutable ID plus config equality. `add_simple_arg()` omits only
  `None` (keeping `0`, `False`); `add_flag()` emits truthy values; optional Docker globals use `is True`. Compose globals sit
  between `compose` and the subcommand (`get_compose_command()`; explicit `progress` wins per call); keep `compose_env_files` order.
- Executable lookup is lazy: `get_client_call_with_path()` caches the `shutil.which()` result plus prefix arguments, not the
  global options `docker_cmd` rebuilds. `docker_cmd` starts with `PolicyExecutable`, a `str` carrier of the originating config
  that survives `Command`/list composition, `copy()`, and `deepcopy()`; call `get_execution_policy()` before stringifying argv.
- Each launch snapshots buffer budget, diagnostics flag, telemetry observer (by reference), environment, and `default_timeout`
  or the innermost `execution_timeout()` scope for the identical config. Later mutations affect new launches only.
- `ReloadableObject` stores an immutable ID (`is_immutable_id=True` skips the first inspect); snapshots expire after
  `CACHE_VALIDITY_PERIOD = 0.01` seconds and `reload()` forces a fetch. `DockerCamelModel` accepts Python names and aliases;
  keep the acronym exceptions in `to_docker_camel()` (`ExecIDs`, `IPAddress`, `OOMKilled`, `RootFS`, `DefaultAPIVersion`).
- `exceptions.py` selects a kind from an ordered table of line-start stderr patterns; add a kind there, not as a substring
  test. Names and context paths can't pick one. Application stderr forwarded by `run`/`exec` can be misclassified, by design.

## Bulk Reload And Inventory

- `bulk_reload()` covers 12 kinds: Container, Image, Network, Volume, Node, Service, Config, Secret, Task, Plugin, Context,
  Builder. Reject unsupported kinds and missing identities across all input before any CLI lookup; empty input is a no-op.
  Group by exact `ClientConfig` object identity plus kind, never config equality; inspect each distinct handle once.
- Builders have no batch inspect: each group runs one strict `buildx ls --format "{{json . }}"` (`parse_builder_listing`).
  Names match exactly, reach no argv, and yield one handle per request in order. Only byte-identical lines collapse (node rows
  repeat them); a non-builder line, null or empty name, or conflicting entry rejects the listing. Safe mode withholds names.
- Stage and validate every group before touching original caches or timestamps; staged identity must equal stored identity
  exactly, without freshness-triggering accessors. Publish once per distinct handle (separate handles for one identity each
  publish). Any group failure leaves every cache unchanged. It's no daemon transaction and isn't atomic for concurrent
  readers: groups inspect separately and handles publish sequentially.
- `capture_inventory()` reuses that staging but never publishes: it returns ordered, detached `InventoryRecord`s (deep copies,
  duplicates kept), leaving inputs untouched. Reference totals are checked before any launch, chunks respect `max_references` and launch
  limits, and each group's measured environment is frozen for its inspect launches. Alias drift, inconsistent snapshots, or
  Swarm ambiguity across chunks reject it. `bulk_reload()` stages under `observation()` only with a `policy` or an active scope.

## Observation And Subprocesses

- `observation(policy, deadline=, cancel=)` nests by minimum: per-command limits take the smaller value, optional totals the
  smaller non-`None`, the earliest finite monotonic deadline wins, and every enclosing token stays effective. Entry and exit
  check cancellation, then the deadline. A scope with `max_total_response_bytes` adds a shared `_ResponseAccount`; captured
  bytes charge every enclosing account under its lock before the first overflow is reported. `max_total_stream_bytes` adds a
  separate `_StreamAccount` the same way (`ObservationState.stream_accounts`, default `()`): captured output never charges it
  and streams never charge a response account. `binds_launches` (deadline, token, or stream account) decides whether a stream or
  archive keeps the snapshot; a budget alone adds no timer to a wait.
- `run()` consults `get_observation()` before its timeout/input branches: an observed call always takes `bounded_capture`, even
  with no timeout or input, and rejects `tty`, uncaptured output, or `input` with `ValueError`. Only unobserved calls use
  `subprocess.run()` (no timeout, no input) or `Popen.communicate()` (timeout or input, owned stdin-writer cleanup).
- `check_scoped_launch()` runs before `Popen` and telemetry; `E2BIG` maps to `ObservationLimitError("native_argv")`. Capture
  precedence: cancel token, then a command deadline strictly earlier than the scope's (only while the child runs), then the
  scope; tokens add only the bounded `CANCEL_CHECK` (50 ms) wait. Streams and archives snapshot the scope once at launch (lazy
  `save` at first iteration), and the watchdog or `ArchiveDeadline` keeps applying it after the `with` exits.
- Stream budget: a `ProcessStream` reader charges stdout and stderr at the raw pipe read under the queue lock, cutting each read
  to the smallest remaining allowance plus one byte (`stream_allowance`) so overflow measures limit + 1. An overflow records
  `ObservationLimitError("total_stream")` as the scope error, stops, kills and reaps the child, and is delivered once; no reader
  thread raises. `ArchiveDeadline` charges only `read()` on the caller's thread and takes the same expiry path (`_Stop`
  `"limit"`); its watchdog drain and the post-exit stderr `record_read` never charge, so `NoSuchImage` still wins.
- A timeout is `None` or positive finite seconds, never `bool`, validated before launch. Deadlines are monotonic from launch and
  advance while consumption is paused; natural exit leaves post-exit draining unchanged. Expiry kills and reaps the local CLI
  before `DockerTimeout` keeps captured output; natural nonzero exits keep stderr-selected `DockerException` types.
- A bare loop `break` doesn't close an owned stream; use its context manager or `close()`, even before the first iteration.
  Closing cancels only the local CLI. `close()` is idempotent, waits for cleanup, delivers a recorded error once, and never
  replaces an active body exception.
- POSIX pipe workers wait on readiness (pipe plus `WakeLatch`; a reaper thread publishes child exit); only a real deadline, a
  cancel token, or Windows pipe slices may time a wait. Every stop path `set()`s the latch; `close()` it after waiters are
  joined. `ArchiveDeadline` waits under its pipe lock, so expiry never closes a pipe beneath a wait.

## Buffers, Errors, And Confidentiality

- `stream_buffer_bytes=None` is unbounded; a budget is a positive integer, not `bool`. Omitted `buffer_bytes` (`DEFAULT_BUFFER`)
  inherits it at the shared stream boundary; explicit `None` stays unbounded. `image_stream_buffer_bytes` is a separate override
  (non-`None` wins) never folded into the config; the image guide covers operation snapshots.
- Queued payload shares one budget across stdout/stderr; each pipe's unfinished record and the stderr history have own bounds, so
  it isn't a total-memory cap. Oversized records raise `BufferError`; pressure blocks producers rather than dropping output.
  Closing or failing wakes blocked parties; keep terminal errors outside the payload queue and never erase a recorded one.
- Automatic diagnostics omit argv, environment values, and captured payloads by default; `raw_diagnostics=True` is a per-client
  opt-in. Raw `docker_command`/`stdout`/`stderr` attributes stay intact and confidential; explicit output, uncaptured terminal
  output, and caller logging are outside the policy. Safe inspect failures dump no response and chain no input-bearing errors.

## JSON Utility

- `_json.py` offers explicit native `dumps`/`loads` using orjson and compatible
  `dumps_compat`/`loads_compat` using the standard library; neither falls back.
  Native dumps returns compact bytes, writes non-finite floats as null, and rejects
  oversized integers and lone surrogates; native loads rejects non-finite tokens
  and can decode oversized integers as floats. Compat dumps returns ASCII-escaped
  text and supports `sort_keys`, `indent`, `separators`, and `allow_nan`, preserving
  standard-library number handling and errors.
- Existing CLI decoders, canonical hashes, raw diagnostic files, delivery REST
  serialization, and the benchmark use compat to preserve accepted inputs,
  exact JSON bytes, error types, and JSON-mode Pydantic validation. Fleet contract and harness stay on the
  standard library to avoid SDK imports; the isolated distribution smoke child
  stays independent of the checkout. Test fixture oracles and Pydantic JSON
  methods also remain unchanged.

## Telemetry

- `telemetry/_records.py`: schema-4 `LaunchRecord`/`CompletionRecord` and `TelemetryObserver`; `_lifecycle.py`:
  `ExecutionTelemetry` and `deliver()`; `_exporter.py`: `BoundedExporter`. This section is the package's only guide.
- Records hold only a fixed family (`captured`, `stream`, `archive_load`, `archive_save`), opaque attempt ID, closed outcome,
  duration, byte counts, and return code. Never add argv, endpoints, environment, output, or exception text, even with raw diagnostics.
- The observer is snapshotted at launch; none means no clock, ID, or callback. `start()` runs at the launch seam after preflight
  (observation rejections emit nothing); `finish()` publishes the first completion once, and observer exceptions are swallowed.
- Outcomes are `success`, `failure`, `timeout`, `closed`, `cancelled`, `limit`. `outcome_of()` in `_lifecycle.py` is the one
  exception-to-outcome map for captured runs, archives, the stream launcher (so a `native_argv` refusal after `start()` is
  `limit`), and the error a stream delivers: `DockerTimeout`/`ObservationTimeout` timeout, `ObservationCancelled` cancelled,
  `ObservationLimitError` limit, `GeneratorExit` closed, anything else (`KeyboardInterrupt`, `BufferError`) failure.
  `ProcessStream._stop_outcome()` picks the stream's by delivery order (expired, scope error, reader error) at terminal delivery
  and in `close()`; with no recorded stop `close()` is `closed`, and one it finds itself (a set token) keeps its outcome.
- `BoundedExporter` is application-owned and creates no worker, timer, or network: calls enqueue or drop newest, and
  `flush()`/`shutdown()` run the sink on the caller's thread over a finite snapshot, counting failures without retry.
