# Test Guide

Paths are relative to `tests/`. Repository-wide checks and safety rules are in the root guide.

## Choose tests by behavior

| Area | Start here |
| --- | --- |
| Client configuration, process ownership, buffering, timeouts | `pywhales/test_client_config.py`, `pywhales/test_docker_client.py`, `pywhales/test_utils.py` |
| Child environments and fake-CLI envs transport | `pywhales/test_environment.py`, `pywhales/components/test_env_forwarding.py`, `pywhales/components/test_env_branch_transport.py` |
| Bulk reload and inventory (13 kinds, including manifest lists) | `pywhales/test_bulk_reload.py`, `pywhales/test_bulk_reload_bounded.py`, `pywhales/test_inventory.py`, `pywhales/test_inventory_cli.py` |
| Observation, launch limits, bounded capture | `pywhales/test_observation.py`, `pywhales/test_observation_budget_integration.py`, `pywhales/test_launch_limits.py`, `pywhales/test_bounded_capture.py` |
| Safe/raw diagnostics, validation, command-copy isolation | `pywhales/test_inspect_diagnostics.py`, `pywhales/test_logging.py`, `pywhales/test_exceptions.py`, `pywhales/test_automatic_diagnostics.py` |
| Telemetry records, exporter, streams, archives | `pywhales/test_telemetry*.py` |
| Convergence, Compose completion, reconciliation journal/store | `pywhales/test_convergence.py`, `pywhales/test_convergence_types.py`, `pywhales/test_reconciliation.py`, `pywhales/test_reconciliation_store.py` |
| Other opt-in policy packages | `pywhales/test_admission_*.py`, `pywhales/test_profiles*.py`, `pywhales/test_storage_*.py` |
| Delivery chain; offline fleet and script tooling | `pywhales/test_delivery_workflows.py`; `pywhales/test_fleet_*.py`, `pywhales/test_transport_experiment.py`, `pywhales/test_plugin_installer.py`, `pywhales/test_docker_overhead_benchmark.py` |
| Launch, log/event, transfer, and push/start/watch/prune deadlines | `pywhales/components/test_command_deadlines.py`, `pywhales/components/test_deadline_forwarding.py` |
| Command groups (mocked, model, and integration tests mix) | `pywhales/components/test_<component>.py`, `pywhales/components/buildx/`, `pywhales/test_buildx_modernization.py`, `pywhales/test_compose_modernization.py` |
| Model snapshots and enum/JSON regressions | `pywhales/components/jsons/`, `pywhales/test_*enum_json*.py` |

Read each test, module-level code, and fixtures before calling a selection unit-only. Inventory, observation, environment,
telemetry, convergence, admission, and deadline tests can drive fake CLIs or real Python/shell children: no daemon, but real processes.
`pywhales/test_delivery_workflows.py` runs a loopback HTTP server, `tar`, `python -m scripts.delivery`, and an offline `uv` install
smoke (`UV_OFFLINE=1`), so don't call that module install-free; fleet, installer, and benchmark tests need no Docker.
The automatic `docker` mark only follows a `docker_client` fixture dependency, including indirect ones. `-m 'not docker'` doesn't
guarantee a daemon-free run: some Buildx and imagetools tests use the global `docker` client without that fixture, and Compose tests use
module-level clients. `pywhales/components/test_compose.py` calls `compose.is_installed()` during collection, before marker deselection.
Model-only, with no runtime fixture/probe: `uv run --locked pytest tests/pywhales/components/buildx/imagetools/test_models.py -q`.

## Runtime regression contracts

- Deadline modules cover run/exec/Compose/build/bake, logs/events, pull/push/copy, and push/start/watch/prune. Keep keyword-only overloads,
  reject invalid timeouts before launch, keep inspect/pull preflights outside the budget, and cover partial output, once-only errors, early close/draining.
- Use real children to prove reaping, closed pipes, stopped reader/watchdog threads, early close before consumption, and saturated-queue backpressure.
  Mocks don't prove cleanup or launch-time snapshots. `test_logging.py` keeps concurrent-client command/list composition and `copy()`/`deepcopy()` isolation.
- Empty streaming image pull is a repeatedly closeable/context-managed `EmptyImageStream` without executable resolution:
  runtime cases are in `components/test_image.py`; static returns in `test_type_contracts.py` and `test_command_deadline_types.py`.
- Assert raw failure details through `stderr`, `stdout`, or `docker_command`, not safe exception text. Test safe and raw rendering
  separately, including traceback chains and no inspect dumps in safe mode; raw opt-in must not change returned payloads.
- Bulk reload covers 13 kinds. Manifest lists use singleton inspect per reference, handle-specific `insecure`, and strict safe validation;
  public manifest inspect stays lenient. Builder uses one listing per config, names never in argv, identical lines collapse, malformed/conflicting lines reject.
  Preserve exact config-identity grouping, upfront rejection, unchanged caches/timestamps on later failure, and one publish per handle.
  Inventory returns ordered staged records without cache publication, detached from handles both ways, including hydrated manifest flags.
- Environment tests keep per-launch snapshots/precedence, mandatory Buildx values, safe diagnostics, and argv/environment limits;
  fake-CLI tests distinguish environment forwarding from literal fallback across captured, streamed, detached, and TTY branches.
- Completion tests keep exited-zero success, safe nonzero failure, unfinished counters, and final-pass revalidation. Preserve closed
  `completion_failed` journal reasons, generation-fenced deadline-detail round-trips in the store, and static completion types.
- Observation tests keep nested minimum limits, shared response accounts across threads, and deadline and cancellation precedence.
  Telemetry tests keep records payload-free under raw diagnostics, one completion per launch, and results intact when the observer raises.

## Runtime fixtures and shared state

- `conftest.py` supplies session-scoped `docker_client`; function-scoped `ctr_client` returns that same client, not an isolated
  daemon. It probes `<executable> version` for 10 seconds; a missing executable, failed probe, or timeout skips, or fails with
  `--no-runtime-skip`. `--docker-exe` configures only this fixture, not global or separately built clients, and `--no-runtime-skip`
  isn't a universal gate: Compose's collection-time skip ignores it.
- Registry fixtures can pull `mhenry07/apache2-utils` and `registry:2`, bind host port 5000, and yield `localhost:5000`. Colocate the
  runner with the disposable daemon so `localhost` registries, published ports, and bind mounts resolve; a remote `DOCKER_HOST` alone
  doesn't. `docker_registry` logs in and out with test credentials, changing local login state; `docker_registry_without_login` doesn't.
  Don't run host CI provisioning scripts to fix workstation access. `swarm_mode` initializes Swarm with fixed advertise address
  `172.31.155.129` and force-leaves during teardown.
- Integration tests include daemon-wide pruning in `pywhales/components/test_system.py` and builder-cache pruning in the Buildx suite.
  `test_compose_build_with_dependencies` copies its project into `tmp_path` but still uses project `withdeps-test` and rebuilds fixed
  tags `withdeps_db` and `withdeps_app`, so image state isn't isolated. Pydantic 2 deprecation warnings are errors.
- Documentation `pywhales/conftest.py::daemon` mocks `docs.docs_utils.docker` (named resources, context cleanup, inventory/name helpers)
  and makes `subprocess.run` raise `AssertionError`. `pywhales/test_docs_live.py` exercises that mock, not a live daemon.

## JSON fixture rules

`get_all_jsons(<category>)` in repository-root `pywhales/test_utils.py`, not `tests/pywhales/test_utils.py`, sorts every entry in
`tests/pywhales/components/jsons/<category>/` by `int(path.stem)`, filtering nothing. Only numbered JSON files: no `AGENTS.md`,
READMEs, nonnumeric files, or subdirectories. Preserve fixed names (`test_parsing_events` reads `events/0.json`) and snapshot meaning.

## Static contracts

Modules named `*_types.py` or `*_type_contracts.py` under `pywhales/` hold `_check_*` functions with `assert_type`: pytest neither collects
nor runs them, and ty validates the inferred types (deadline shapes: `test_command_deadline_types.py`). `test_inventory_types.py` and
`components/test_task.py` mix one with runtime tests. Update contracts with overloads, return types, and top-level aliases.
