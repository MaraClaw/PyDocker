# Compose Contracts

`cli_wrapper.py` defines `ComposeCLI`; `models.py` holds configuration, project, and event models.
Build progress overrides and Compose globals follow [the package guide](../../AGENTS.md).

## Configuration Models

- Compose models inherit ordinary Pydantic `BaseModel`, not `DockerCamelModel`.
- `config()` returns `ComposeConfig`; `config(return_json=True)` returns
  `dict[str, JsonValue]`, preserving fields that aren't declared in the typed model.
- Keep nested JSON fields typed as `JsonValue`, including scalar/list values where allowed.
- Output enums retain `EnumType | str | None` so unfamiliar wire strings survive parsing.
- Preserve `Field(default_factory=...)` declarations and independent mutable defaults.

## Service Selection

- `None` selects all services and `[]` skips execution for `build`, `create`, `down`,
  `kill`, `pause`, `pull`, `push`, `restart`, `rm`, `start`, `stop`, `unpause`, `up`, and `watch`.
- `top([])` returns an empty `ComposeTopResult`, or `""` with `raw=True`, without CLI lookup.
- Don't generalize empty-selection behavior: `ps([])`, `logs([])`, and `events([])` select all.

## Execution And Returns

- `build`, `down`, `pull`, and `up` reject `quiet=True` with `stream_logs=True`.
- `build(print=True)` still returns `None` when blocking, or an output stream with `stream_logs=True`.
- Streamed `create()` prints its command only with `raw_diagnostics=True`; returned log records are unchanged.
- `run()` returns `str` when attached, `Container` when detached, or an iterable of
  `(source, bytes)` with `stream=True`; detached resources retain the originating config.
- `run()` defaults to `tty=True`. Reject TTY plus streaming, detach plus streaming,
  and detach plus TTY; callers must set `tty=False` for detached or streamed runs.
- Its keyword-only `timeout` is validated before launch and forwarded to blocking/streaming
  execution. This local CLI budget isn't a Compose shutdown timeout or `up(wait_timeout=...)`.
- `ps()` returns container resources. Reject `ps()` and detached `run()` in dry-run mode
  before execution; attached `run(tty=False)` can return preview text instead.

## Logs And Events

- `logs()` and `events()` accept keyword-only `timeout` for a launch-based local CLI deadline.
  Expiry raises `DockerTimeout`; it doesn't stop services or change post-exit pipe draining.
- Logs retain collected text or an owned `(source, bytes)` stream. Events retain
  `MappedProcessStream[ComposeEvent]`, parsing stdout JSON only and owning the underlying transport.
- General `stream_buffer_bytes` is selected at the shared stream boundary, including for collected logs.
  Don't stringify away the originating policy carrier or add wrapper-specific budget defaults.

## Top And Watch

- `top()` returns `ComposeTopResult` with `raw_output` and ordered sections;
  `raw=True` returns untouched CLI text instead of parsing it.
- Grouped sections retain container names. Flat `SERVICE`/`#` tables retain attribution
  in headings/cells with `container=None`; don't apply Docker container-top grammar.
- `watch()` returns an owned `ProcessStream`, or `None` for `[]`. Use a context manager
  or explicit `close()` under the package guide's ownership contract.
- Always emit explicit `--prune=false` by default, or `--prune=true` when requested.
  Watch's `quiet=True` hides build output but remains compatible with its output stream.

## Regression References

Paths below are relative to `tests/pywhales/`; collection/runtime cautions are in [the test guide](../../../tests/AGENTS.md).
- `test_compose_modernization.py`: argv, per-build progress, empty selection, dry-run resources, watch ownership.
- `test_compose_build_enum_json.py`: nested JSON, enum/string outputs, independent defaults, raw config.
- `components/test_compose.py`: top layouts/raw/no-op/error cases, logs/events ownership, legacy integration.
- `components/test_command_deadlines.py` and `components/test_deadline_forwarding.py`: run/log/event deadline execution and forwarding.
- Static contracts: `test_compose_type_contracts.py`, `test_type_contracts.py`,
  `test_command_deadline_types.py`, and `test_enum_json_type_contracts.py`.
