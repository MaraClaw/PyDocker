# Scripts Guide

Contributor tooling, not installed APIs: `pyproject.toml` packages only `pywhales*`, and `scripts/` has no `__init__.py`.
Run its packages from the repository root as `python -m scripts.<package>`. The root `AGENTS.md` owns the shared safety
rules (daemon provisioning, the destructive suite, release steps) and the repo-wide Ruff and ty setup; this guide maps
what lives here.

## Local Map

| Path | Role |
| --- | --- |
| `ci-setup.sh`, `add-local-docker-registry.sh`, `download-docker-plugins.sh`, `docker-plugin-checksums.txt` | CI provisioning and plugin pins |
| `install-uv.sh` | Pipes the remote uv installer into `sh`; nothing in the repository calls it |
| `benchmark_docker_overhead.py` | Simulated wrapper-overhead profiler |
| `fleet_contract/` | Offline fleet contract, 12 files: strict schemas, validators, non-qualifying reports |
| `fleet_harness/` | Fake-only capacity simulation, 7 files, built on the contract |
| `delivery/` | Tested delivery chain; see `delivery/AGENTS.md` |

## Shell Scripts

- The root guide covers the provisioning hazards. Read these scripts; don't run them on a workstation.
- `python-package.yml` calls `ci-setup.sh` from three jobs (component, other, coverage) and `download-docker-plugins.sh`
  from the Compose and Buildx smoke job. Read those jobs before editing either script.
- `COMPOSE_VERSION` and `BUILDX_VERSION` default to 5.6.1 and 0.38.1. The script fails before downloading unless the
  checksum file holds exactly one 64-hex pin for that name and version, and it verifies with `sha256sum -c` in a staging
  directory before `mv`. Keep that order. A bump changes the pin file and the plugin rows in
  `fleet_contract/fixtures/support-matrix.json`.

## Benchmark

- `subprocess.run` becomes a recorder and `subprocess.Popen` raises, so no Docker or child process starts. It times
  Python wrapper work, not builds. `--repetitions N` takes 1 to 1000 (default 20) and prints JSON.
- Documented run from the root: `.venv/bin/python scripts/benchmark_docker_overhead.py`.
- `SimulatedExecution.run` raises `RuntimeError` for any CLI call without a fixture response, so a scenario that adds a
  call needs one there. Test: `tests/pywhales/test_docker_overhead_benchmark.py`.

## Fleet Contract

- `fleet_contract/README.md` is authoritative for commands, units, invariants, and report semantics. Don't copy it here.
- Modules: `common.py` (strict primitives, `Decision` markers), `manifest.py` (cross-group invariants), `workload.py`,
  `platform.py`, `safety.py`, `support.py`, `results.py`, `assessment.py`, `report.py`, and `__main__.py` (`SafeParser`).
- Schema version 1 is strict and JSON Schema omits the cross-field validators, so gate on the Python validator. Changed
  semantics need a schema-version review. Fixtures are fictional; no support row is approved.
- The CLI starts no process and opens no socket. Errors expose fixed codes, but successful output keeps supplied data, so
  keep credentials, private endpoints, and confidential paths out of inputs. Report exit 0 means valid input, not passing
  criteria, and `qualification` is always `"unqualified"`.
- Root ty excludes `scripts/`. Scoped checks, as the README gives them (these are offline; the broader suite is not):

```bash
uv run --offline --locked pytest tests/pywhales/test_fleet_contract.py tests/pywhales/test_fleet_report.py \
  tests/pywhales/test_fleet_cli.py tests/pywhales/test_fleet_invariants.py tests/pywhales/test_docker_overhead_benchmark.py \
  tests/pywhales/test_client_config.py tests/pywhales/test_docker_client.py -m 'not docker' -q
uv run --offline --locked ruff check ./
uv run --offline --locked ruff format --check ./
uv run --offline --locked ty check
uv run --offline --locked ty check --config 'src.include = ["scripts/fleet_contract"]' scripts/fleet_contract
```

## Fleet Harness

- No README. Subcommands in `__main__.py`: `run --manifest M --input PLAN`, `decide --input EXPERIMENT`, and
  `schema --kind {manifest,plan,experiment}`. Exit 0 means a report was produced, even for a stopped run or a no-go; 2
  means invalid or rejected input. Fixtures are in `fleet_harness/fixtures/`. No README gives a scoped ty command for it.
- Nothing here contacts Docker, a daemon, or the network. `fake.py` holds an in-memory `FakeDriver` with a fake clock.
- `models.authorize` is the preflight and runs before the driver exists. It requires `opt_in`, rejects any provider but
  `fake`, and checks each step against the endpoint, action, fault, and owned-ephemeral asset allowlists. `Plan` caps
  samples with `maximum_samples` and reserves the `cleanup` action.
- `runner.run` stops on the earliest duration limit or on failure, unknown, resource, or asset limits. `FakeDriver.cleanup`
  runs in `finally`, removes only exact acquired identities within the cleanup budget, and reports residuals.
- `experiment.decide` always returns `no_go` with route `cli`, zero candidate launches and connections, and
  `qualification="unqualified"`. Even a declared real baseline can't change that; don't add a path that launches one.
- Tests: `test_fleet_harness.py`, `test_fleet_harness_cli.py`, and `test_transport_experiment.py` under
  `tests/pywhales/`. The CLI tests strip `PATH` so Docker is absent.
