# PyWhales Agent Guide

## Overview

PyWhales is a typed Python wrapper around the external Docker CLI, not a Docker HTTP SDK.
Python >=3.14.8 and Pydantic 2 are required; `.python-version` pins 3.14.8.
The public API exports `DockerClient`, resource classes, and a shared `docker` client from `pywhales/__init__.py`.
`DockerClient` wires 18 command groups to one `ClientConfig` and exposes convenience aliases such as `run`, `ps`, and `build`.
Importing the package or constructing a client doesn't check CLI, daemon, or plugin availability.
The installed `pywhales` console entry point only prints a notice; it isn't a Docker command runner.

## Structure And Navigation

| Path | Start here for |
| --- | --- |
| `pywhales/docker_client.py` | Public client, command-group wiring, top-level aliases |
| `pywhales/client_config.py` | Shared client options, command assembly, reloadable resource bases |
| `pywhales/_execution_policy.py` | Originating-config carrier and per-launch execution policy |
| `pywhales/_inspect_validation.py` | Context, plugin, and task batch-response validation |
| `pywhales/_bulk_reload.py` | Grouped inspection and staged resource-cache publication |
| `pywhales/utils.py` | Subprocess execution, common conversions, Pydantic model base |
| `pywhales/_process_stream.py` | Process-backed streaming and lifecycle ownership |
| `pywhales/_stream_buffer.py` | Thread-safe output buffering and byte accounting |
| `pywhales/components/` | Docker command groups; start with each group's `cli_wrapper.py` and `models.py` where present |
| `tests/` | Pytest suite, shared runtime fixtures, static type contracts |
| `docs/` | Editable templates, API-doc generation, MkDocs configuration |
| `scripts/` | CI daemon setup, plugin installation, overhead benchmark |
| `skills/pywhales/` | Consumer skill for using the installed library, not a contributor workflow |
| `.github/workflows/` | Authoritative CI checks, docs build, package release jobs |
| `pyproject.toml`, `uv.lock`, `.python-version` | Project/tool configuration, locked dependencies, interpreter pin |

## Code Map

| Symbol | File | Role |
| --- | --- | --- |
| `DockerClient` | `pywhales/docker_client.py` | Public facade and shared-config command-group wiring |
| `ClientConfig` | `pywhales/client_config.py` | Dataclass holding CLI, connection, Compose, and execution settings |
| `Command` | `pywhales/client_config.py` | Argument-list builder with option/flag helpers |
| `ReloadableObject` | `pywhales/client_config.py` | Resource identity and refreshed inspection data |
| `DockerCamelModel` | `pywhales/utils.py` | Pydantic base mapping Python names to Docker JSON aliases |
| `ProcessStream` | `pywhales/_process_stream.py` | Iterator owning a CLI process and streamed output |

## Guide Scope

- Read this root guide first, then the nearest child `AGENTS.md` for files you're changing.
- Shared runtime internals belong in `pywhales/AGENTS.md`; command-group patterns belong in `pywhales/components/AGENTS.md`.
- Buildx, Compose, container, and image work should also follow the guide in that component's directory.
- Test selection and fixture details belong in `tests/AGENTS.md`.
- Generation mechanics and documentation pipeline details belong in `docs/AGENTS.md`.

## Commands From CI

Run from the repository root. These commands mirror `.github/workflows/python-package.yml` and `python-publish.yml`.
The strict coverage command is an integration run, not a safe default for a workstation daemon.

| Purpose | Command |
| --- | --- |
| Install locked development environment | `uv sync --locked` |
| Lint | `uv run --locked ruff check ./` |
| Check formatting | `uv run --locked ruff format --check ./` |
| Check types and static contracts | `uv run --locked ty check` |
| Full strict suite with coverage, disposable daemon only | `uv run --locked coverage run -m pytest -vv -ra --no-runtime-skip --durations=10` |
| Enforce coverage floor | `uv run --locked coverage report` |
| Generate documentation sources | `uv run --locked python -m docs.autogen` |
| Build documentation site | `uv run --locked mkdocs build --config-file docs/mkdocs.yml` |
| Build source and wheel distributions | `uv build --no-sources --sdist --wheel` |

## Conventions And Checks

- Treat Ruff and ty settings in `pyproject.toml` as authoritative, not legacy `.flake8` or `.isort.cfg` files.
- Ruff targets Python 3.14 and handles import sorting as well as linting and formatting.
- ty checks `pywhales`, `tests`, and `docs`; all rules are errors and warnings fail the check.
- ty doesn't respect type-ignore suppression comments; fix types rather than adding those comments.
- Static `assert_type` contracts in `tests/pywhales/test_type_contracts.py` and related files are checked by ty, not exercised by pytest.
- Coverage is measured for `pywhales` with a 90% minimum; `coverage report` enforces it.
- Keep dependency changes consistent with `uv.lock` and the interpreter pin; normal checks use `--locked`.
- Follow existing CLI argument and return-type contracts; check convenience aliases when changing component APIs.

## Safety And Generated Files

- The integration suite is destructive: use a disposable Docker daemon, not one holding valuable containers, images, volumes, or Swarm state.
- Missing runtime support can skip tests locally; CI uses `--no-runtime-skip` to turn unavailable runtime checks into failures.
- `scripts/ci-setup.sh` calls `scripts/add-local-docker-registry.sh`, which overwrites `/etc/docker/daemon.json` and restarts Docker with sudo.
- That setup also installs plugins; it's CI provisioning, not safe workstation setup. Don't run it casually.
- `PYDOCKER_DEBUG=1` enables execution diagnostics that withhold command, environment, and captured-output values by default.
  `DockerClient(raw_diagnostics=True)` opts that client into raw diagnostics; see `pywhales/AGENTS.md` for the confidentiality boundary.
- Documentation generation is Docker-free by default. `--live-examples` explicitly opts into real Docker operations.
- Edit `docs/template/` and source docstrings, not generated documentation output.
- `docs/generated_sources/`, `site/`, and `docs/site/` are ignored outputs; don't commit generated sources or site files.
- Keep build output, caches, virtual environments, and package metadata out of source changes.
