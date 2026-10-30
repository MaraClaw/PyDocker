# Test Guide

Paths are relative to `tests/`. Repository-wide checks and safety rules are in the root guide.

## Choose tests by behavior

| Area | Start here |
| --- | --- |
| Client configuration, process ownership, buffering, timeouts | `pywhales/test_client_config.py`, `pywhales/test_docker_client.py`, `pywhales/test_utils.py` |
| Bulk reload (12 kinds) and inventory capture | `pywhales/test_bulk_reload.py`, `pywhales/test_bulk_reload_bounded.py`, `pywhales/test_inventory.py`, `pywhales/test_inventory_cli.py` |
| Observation, launch limits, bounded capture | `pywhales/test_observation.py`, `pywhales/test_observation_budget_integration.py`, `pywhales/test_launch_limits.py`, `pywhales/test_bounded_capture.py` |
| Safe/raw diagnostics, validation, command-copy isolation | `pywhales/test_inspect_diagnostics.py`, `pywhales/test_logging.py`, `pywhales/test_exceptions.py`, `pywhales/test_automatic_diagnostics.py` |
| Telemetry records, exporter, streams, archives | `pywhales/test_telemetry*.py` |
| Opt-in packages | `pywhales/test_admission_*.py`, `pywhales/test_convergence.py`, `pywhales/test_profiles*.py`, `pywhales/test_storage_*.py`, `pywhales/test_reconciliation*.py` |
| Delivery chain; offline fleet and script tooling | `pywhales/test_delivery_workflows.py`; `pywhales/test_fleet_*.py`, `pywhales/test_transport_experiment.py`, `pywhales/test_plugin_installer.py`, `pywhales/test_docker_overhead_benchmark.py` |
| Launch, log/event, transfer, and push/start/watch/prune deadlines | `pywhales/components/test_command_deadlines.py`, `pywhales/components/test_deadline_forwarding.py` |
| Command groups (mocked, model, and integration tests mix) | `pywhales/components/test_<component>.py`, `pywhales/components/buildx/`, `pywhales/test_buildx_modernization.py`, `pywhales/test_compose_modernization.py` |
| Model snapshots and enum/JSON regressions | `pywhales/components/jsons/`, `pywhales/test_*enum_json*.py` |

Read the specific test, module-level code, and requested fixtures before calling a selection unit-only. Many inventory, observation,
telemetry, convergence, admission, and deadline tests drive fake CLIs or real Python/shell children: no daemon, but real processes.
`pywhales/test_delivery_workflows.py` runs a loopback HTTP server, `tar`, `python -m scripts.delivery`, and an offline `uv` install
smoke (`UV_OFFLINE=1`), so don't call that module install-free; fleet, installer, and benchmark tests need no Docker.
The automatic `docker` mark only follows a `docker_client` fixture dependency, including indirect ones. `-m 'not docker'` doesn't
guarantee a daemon-free run: some Buildx and imagetools tests use the global `docker` client without that fixture, and Compose tests use
module-level clients. `pywhales/components/test_compose.py` calls `compose.is_installed()` during collection, before marker deselection.
A model-only selection needs no runtime fixture or availability check:

```bash
uv run --locked pytest tests/pywhales/components/buildx/imagetools/test_models.py -q
```

## Runtime regression contracts

- Deadline modules cover launches (run, exec, Compose, build, bake), logs and events, pull/push/copy transfers, and
  push/start/watch/prune. Keep keyword-only forwarding in every overload, invalid timeouts rejected before any launch, inspect and
  pull preflights outside the selected budget, partial captured output, once-only errors, early close, and post-exit draining covered.
- Use real child processes for lifecycle behavior: verify reaping, closed pipes, stopped reader/watchdog threads, early close before
  consumption, and saturated-queue backpressure. A mocked launch doesn't prove cleanup or launch-time policy snapshots; keep
  command/list composition, `copy()`, and `deepcopy()` isolation across concurrent clients in `test_logging.py`.
- Assert raw failure details through `stderr`, `stdout`, or `docker_command`, not safe exception text. Test safe and raw rendering
  separately, including traceback chains and no inspect dumps in safe mode; raw opt-in must not change returned payloads.
- Bulk reload tests cover all twelve kinds including Builder (one listing per config, names never in argv, byte-identical lines
  collapse, conflicts and malformed lines reject), exact config-identity grouping, upfront rejection, unchanged caches and timestamps
  when any later group fails, and one publish per distinct handle. Inventory tests keep records detached from handles both ways.
- Observation tests keep nested minimum limits, shared response accounts across threads, and deadline and cancellation precedence.
  Telemetry tests keep records payload-free under raw diagnostics, one completion per launch, and results intact when the observer raises.

## Runtime fixtures and shared state

- `tests/conftest.py` supplies session-scoped `docker_client`; function-scoped `ctr_client` returns that same client, not an isolated
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
- Documentation tests use `pywhales/conftest.py::daemon`, which patches `docs.docs_utils.docker` with a `MagicMock` of named example
  resources (context-manager cleanup, mocked inventory and name helpers) and makes `subprocess.run` raise `AssertionError`.
  Despite its name, `pywhales/test_docs_live.py` exercises live-example code with it, not a live daemon.

## JSON fixture rules

`get_all_jsons(<category>)` in the package helper `pywhales/test_utils.py` at the repository root, not `tests/pywhales/test_utils.py`,
reads `tests/pywhales/components/jsons/<category>/` and sorts every directory entry by `int(path.stem)`, filtering nothing. Keep category
directories to numbered JSON files such as `0.json`: no `AGENTS.md`, READMEs, nonnumeric files, or subdirectories. Some tests read fixed
names (`test_parsing_events` reads `events/0.json`), so preserve filenames and snapshot meaning; add a numbered sample for a new wire format.

## Static contracts

Modules named `*_types.py` or `*_type_contracts.py` under `pywhales/` hold `_check_*` functions with `assert_type`: pytest neither collects
nor runs them, and ty validates the inferred types (deadline shapes: `test_command_deadline_types.py`). `test_inventory_types.py` and
`components/test_task.py` mix one with runtime tests. Update contracts with overloads, return types, and top-level aliases.
