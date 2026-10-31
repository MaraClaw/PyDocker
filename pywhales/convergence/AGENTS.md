# Convergence Contracts

Read-only observation of a captured Swarm service generation, a fixed stack, or selected Compose services. Nothing here creates, updates, or rolls back daemon state. Observation scopes, launch limits, and local process cleanup are in [the shared runtime guide](../AGENTS.md).

## Local Navigation

- `__init__.py` re-exports the public names. `_wait.py` holds `capture_service`, `capture_compose`, `wait`, and the Swarm polling loop.
- `_types.py`: frozen targets and outcomes. `_wire.py`: strict Pydantic wire models and exact-identity inspect helpers.
- `_service.py`: `check_service` and `ServiceObservation` for Swarm. `_compose.py`: Compose capture, per-cycle `check_compose`, and `wait_compose`.

## Types And Outcomes

- Dataclasses are frozen and slotted. `client_config` is `repr=False, compare=False` on `ServiceTarget` and `ComposeTarget`, as is `Failure.cause`; `template` and `approved_node_ids` leave `repr` but stay in equality. Preserve that distinction between client settings and captured generation data.
- `Deadline` accepts only a concrete `DeadlineDetail` (`type(...) is`), so a subclass can't smuggle a payload. The detail holds per-reason affected-service counts, each bounded by `captured_services`, with no identities, tasks, containers, settings, or causes.
- Detail is the latest complete cycle, never a partial one or a fresh inspection at expiry. It is absent when capture expired or no cycle finished.
- Swarm readiness is `ServiceObservation.ready`; Compose success is `_settled`, an all-zero `DeadlineDetail`. A new Swarm blocker needs a flag plus `summarize`. A new counter blocks Compose by default once `check_compose` fills it. Any new counter also joins the bounds tuple in `DeadlineDetail.__post_init__`.

## Wire And Identity

- Wire models are strict and keep only evaluated fields. `ComposeLabels` drops other labels, `Healthcheck` shrinks to presence (a non-empty `Test` whose first item isn't `NONE`), and container environment and command are dropped.
- Inspect helpers need exactly one result whose full ID equals the request, else `ParsingError`. A service name or abbreviated ID therefore fails capture as `observation`.
- A generation is `Version.Index` plus a SHA-256 over the complete `Mode` and `TaskTemplate`. The only normalization drops `Runtime == "container"` from the template. Ignore nothing else, and compare task specs through `canonical(..., template=True)`.
- `target_from_wire` fails closed. Replicated must be exactly `{Replicas: int >= 0}` and takes no approved nodes; Global requires them. Other modes or runtimes are `unsupported`, and a missing image is `malformed`.

## Swarm Evaluation

- `check_service` checks the generation first (`superseded`), then update state: paused, rollback, or failed states are `update_failed` and unknown states `malformed`. Only desired-`running` tasks count, and an unknown task state is a `ParsingError`.
- Replicated needs every slot `1..replicas` running on the captured template. Global needs running matching tasks on exactly the approved nodes; duplicate, unassigned, or outside nodes block.
- Once every target looks ready, `wait` re-inspects each service with the same generation and update checks, and an `updating` state there clears readiness. That recheck, then `check_observation`, must precede publishing `last_complete`.

## Compose Evaluation

- Capture lists with `compose ps --all --quiet`, so stopped containers count, and skips containers labelled `com.docker.compose.oneoff=True`, in capture and every cycle. A service with no containers or mixed hashes is `unsupported`; a missing hash or a container outside the selection is `malformed`. A dry-run client raises `ValueError` at capture and is `unsupported` in `check_compose`.
- `check_compose` re-lists each cycle. A changed ID set, service label, or config hash, or a vanished container, is `superseded`. Ready means `running`, not restarting, not paused, and `healthy` whenever a healthcheck is configured; a missing Health block blocks. An exited-0 one-shot also blocks, so it must stay out of the selection.
- `wait_compose` re-runs the check after the first all-clear and succeeds only if that final pass is clear too. `_pause` sleeps in `SLEEP_SLICE` (0.05 s) steps and checks the observation each time.
- Compose turns `ValidationError` and `ParsingError` into `malformed` with no cause. The Swarm path keeps `observation` with its cause. Keep that split.

## Tests

`tests/pywhales/test_convergence.py` drives a fake Python CLI (`fake_cli`, backed by `data.json` and a `calls` log) and needs no daemon. `test_convergence_types.py` holds `assert_type` checks for `ty`.

- Tests swap `time` in `_wait`, `_compose`, and `pywhales._observation`, patch `_wait.check_service`, and spy on `_wire.run`. Keep those module-level call sites or update the tests.
- Hung-child tests assert reaping, so cancellation and deadline paths must still kill the local CLI.

```bash
uv run --locked pytest tests/pywhales/test_convergence.py -q -k compose
```

The full file also covers the existing Swarm paths; the Compose selection still starts real local CLI children.
