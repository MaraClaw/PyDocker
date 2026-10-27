# Stable inventory and bounded inspection

Status: Implemented (opt-in SDK API; offline validation only)
Tier: P1. Owner: SDK maintainer, with an operator defining observation budgets.

## Objective

Provide detached inventory values and opt-in bounded inspection without changing
the behavior of existing live resource handles.

## Implemented surface

`capture_inventory()` accepts `InventoryRequest` values and existing handles for
containers, images, networks, volumes, nodes, services, configs, secrets, tasks,
plugins, and contexts. `InventorySnapshot` records the UTC capture interval and
ordered `InventoryRecord` values with frozen identities and independently deep
copied typed payloads. Callers may mutate their payloads without changing live
handles or other records.

`InspectionPolicy` requires explicit reference, encoded argv, and combined raw
stdout/stderr byte limits. `observation()` adds a context-local absolute monotonic
deadline and cancellation token to synchronous captured launches, including list
and inspect calls. `bulk_reload(..., policy=...)` or an enclosing observation
selects bounded staging; ordinary calls retain their existing behavior.

Offline evidence is in `test_inventory.py`, `test_inventory_cli.py`,
`test_inventory_types.py`, `test_bulk_reload_bounded.py`,
`test_bounded_capture.py`, `test_launch_limits.py`, and `test_observation.py` under
`tests/pywhales/`. Fake CLI and local child tests cover the acceptance scenarios,
including later-chunk failure before cache publication and child cleanup.
Native child execution was validated on macOS; Windows pipe readiness still
requires execution on Windows. No live Docker workload or fleet qualification
was performed.

## Current evidence

- [`ReloadableObject`](../pywhales/client_config.py) stores an identity and a
  short-lived inspect cache; property access can trigger another CLI call.
- [`bulk_reload`](../pywhales/_bulk_reload.py) groups by exact config object
  identity and kind, validates all groups before publication, then updates each
  distinct handle sequentially. This is not a daemon transaction or atomic
  visibility to concurrent readers.
- Task, context, and plugin list-valued `inspect` already use plural calls:
  [task](../pywhales/components/task/cli_wrapper.py),
  [context](../pywhales/components/context/cli_wrapper.py), and
  [plugin](../pywhales/components/plugin/cli_wrapper.py).
- [`run`](../pywhales/utils.py) has an optional launch timeout. The
  [`ProcessStream`](../pywhales/_process_stream.py) watchdog and
  [`StreamBuffer`](../pywhales/_stream_buffer.py) provide separate stream
  controls, not a bound on synchronous captured output or total memory.
- [`test_bulk_reload.py`](../tests/pywhales/test_bulk_reload.py) specifies
  grouping, duplicate handles, and failure-before-publication behavior.
  [`test_task.py`](../tests/pywhales/components/test_task.py) covers plural
  task aliases and whole-response validation. These tests were read, not run.

## Proposed contract

- Add an opt-in inventory capture returning typed records with immutable IDs,
  recursively immutable or deeply detached values, and capture start/end times.
- Reads of a record never inspect or mutate a live handle. An explicit new
  capture produces a new result; callers own the returned values.
- Record times delimit a capture interval, not a simultaneous daemon snapshot.
  Concurrent resource changes can make observations internally inconsistent.
- Retain request order and multiplicity. Each alias resolves by its resource
  namespace's existing rules, including positional aliases where applicable.
- Specify a caller-supplied policy for maximum references, encoded argv bytes,
  and captured response bytes per invocation. Account for the complete encoded
  command and platform limits, and reject an individually oversized reference
  before launch instead of attempting an impossible chunk.
- Split deterministically into ordered chunks and enforce the response limit
  while reading, before unbounded capture. On excess, stop and reap the local
  child and raise a distinct limit error; never return a truncated success.
- Validate cross-chunk duplicates and alias associations across the complete
  request. A mismatch, missing item, malformed response, CLI error, or limit
  failure returns no inventory and changes no original handle caches.
- If bulk reload uses chunking, stage every chunk of every group before any
  publication; retain exact-config grouping and one publication per handle.
- Add an optional bounded observation invocation that accepts remaining
  monotonic time and cancellation from its caller, including list and inspect
  subprocesses. This is needed by the future convergence waiter.
- Keep existing timeout defaults and selected-launch semantics unchanged.
  Timeout or cancellation terminates and reaps the local CLI only; remote
  daemon state is not rolled back. Preserve safe per-client diagnostics.
- Chunk limits do not bound the complete result, staged snapshots, decoded JSON,
  Python object overhead, or aggregate process memory. Publish these exclusions.

## Dependencies and non-goals

Depends on [workload and support contract](01-fleet-workload-and-support-contract.md).
This work does not add admission control, a scheduler, daemon transactions,
automatic retry, or a fleet capacity guarantee.

## Acceptance scenarios

1. Happy path: an ordered request with aliases and repeated identities crossing
   chunks returns matching detached records in the original order; mutating live
   caches afterward cannot change them. Seam: fake CLI responses and clock.
   Surface: call the inventory API with a local fake CLI.
2. Failure: an oversized response or inconsistent duplicate in a later chunk
   raises the declared error and leaves every original cache and timestamp
   unchanged. Seam: bounded-output child and bulk reload mocks. Surface: invoke
   capture and reload against a local child, then inspect child termination.
3. Adjacent regression: empty input launches nothing, equal but distinct configs
   stay separate, and existing scalar/plural inspect and timeout behavior remain
   unchanged. Seam: existing inspection and deadline tests. Surface: call both
   existing APIs and the new path with a local fake CLI.

## Future validation and decisions

First run focused mocked tests and local child-process tests, including byte
boundaries and cleanup; do not infer whole-process memory bounds from them.
Only with separate authorization, inspect disposable Docker resources and
record configuration, observations, and teardown of resources created.

The operator must still choose workload-derived byte, item, and time budgets;
this blocks deployment defaults and capacity claims. Public record and policy
names and the eleven supported resource kinds are specified above. Per-launch
limits do not bound aggregate memory or turn the capture interval into a daemon
transaction.
