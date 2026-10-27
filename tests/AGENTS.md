# Test Guide

Paths below are relative to `tests/`. Repository-wide checks and tooling are in the root guide.

## Choose tests by behavior

| Area | Start here |
| --- | --- |
| Client configuration and mocked CLI regressions | `pywhales/test_client_config.py`, `pywhales/test_docker_client.py` |
| Process ownership, buffering, timeouts, and conversions | `pywhales/test_utils.py` |
| Buildx and Compose option/return regressions | `pywhales/test_buildx_modernization.py`, `pywhales/test_compose_modernization.py` |
| Deadline execution and forwarding | `pywhales/components/test_command_deadlines.py`, `pywhales/components/test_deadline_forwarding.py` |
| Command groups | `pywhales/components/test_<component>.py`; these can mix mocked, model, and integration tests |
| Buildx and imagetools | `pywhales/components/buildx/test_buildx_cli_wrapper.py`, `pywhales/components/buildx/imagetools/` |
| Model snapshots and enum/JSON regressions | `pywhales/components/jsons/`, `pywhales/test_container_image_enum_json.py` |
| Mocked documentation resource lifecycles | `pywhales/test_docs_live.py`, `pywhales/conftest.py` |

Read the specific test, module-level code, and requested fixtures before calling a selection unit-only.
For example, `pywhales/test_utils.py` starts real shell/Python children; deadline execution tests use a temporary Python CLI driver.
The automatic `docker` mark only follows a `docker_client` fixture dependency, including indirect dependencies.
`-m 'not docker'` doesn't guarantee a daemon-free run: some Buildx tests use the global `docker` client without that fixture.
Compose tests also use module-level clients, and `pywhales/components/test_compose.py` calls `compose.is_installed()` during collection.
Marker deselection happens after module import, so it doesn't prevent that availability check.

A focused model selection, with no runtime fixture or module-level availability check:

```bash
uv run --locked pytest tests/pywhales/components/buildx/imagetools/test_models.py -q
```

## Runtime fixtures and shared state

- This directory's `conftest.py` supplies a session-scoped `docker_client`; function-scoped `ctr_client` returns that same client, not an isolated daemon.
- The session fixture probes `<executable> version` with a 10-second timeout before returning the client.
- A missing executable, failed probe, or timeout skips by default and fails with `--no-runtime-skip`.
- `--docker-exe` configures only this fixture's client/probe. It doesn't redirect global or separately constructed clients.
- `--no-runtime-skip` isn't a universal runtime/plugin gate; Compose's collection-time skip doesn't consult it.
- Registry fixtures can pull `mhenry07/apache2-utils` and `registry:2`, bind host port 5000, and yield `localhost:5000`.
- `docker_registry` logs in with test credentials and logs out afterward, changing local login state; `docker_registry_without_login` omits both steps.
- `swarm_mode` initializes with fixed advertise address `172.31.155.129`, then force-leaves Swarm during teardown.
- Integration tests include daemon-wide pruning in `pywhales/components/test_system.py` and builder-cache pruning in the Buildx suite.
- `test_compose_build_with_dependencies` copies `pywhales/components/build_with_dependencies_test/` into `tmp_path` before building.
  It still uses project name `withdeps-test` and deletes/rebuilds fixed image tags `withdeps_db` and `withdeps_app`; the temporary copy doesn't isolate image state.

## Documentation mocks

`pywhales/conftest.py::daemon` patches `docs.docs_utils.docker` with a `MagicMock` and provides named example resources.
Those resources support context-manager cleanup; inventory/name helpers and acquisition responses are mocked too.
The fixture patches `subprocess.run` to raise `AssertionError("external command")`, catching accidental external calls through that path.
Despite its name, `pywhales/test_docs_live.py` exercises live-example code with this mocked fixture, not a live daemon.

## JSON fixture rules

The repository helper `pywhales/test_utils.py::get_all_jsons` reads `tests/pywhales/components/jsons/<category>/`.
It sorts every directory entry by `int(path.stem)` and doesn't filter extensions or directories.
Keep category directories limited to numbered JSON files such as `0.json`; don't place `AGENTS.md`, READMEs, nonnumeric files, or subdirectories there.
Some tests read specific filenames, such as `pywhales/components/test_system.py::test_parsing_events` reading `pywhales/components/jsons/events/0.json`.
Preserve existing filenames and snapshot meaning; add a new numbered sample for a new wire-format case.

## Static contracts

`pywhales/test_type_contracts.py`, `pywhales/test_buildx_modern_types.py`, and `pywhales/test_compose_type_contracts.py` hold `_check_*` functions with `assert_type` calls.
These functions aren't pytest tests and aren't collected or executed by pytest; the type checker validates their inferred types.
When changing overloads or return types, update the relevant static contracts alongside runtime regressions, including top-level convenience aliases.
