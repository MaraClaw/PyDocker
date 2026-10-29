# Observation Budgets And Convergence

Import bounded observation/inventory APIs from the `pywhales` package root,
and convergence APIs from `pywhales.convergence`. Both are opt-in; neither
changes ordinary `DockerClient` calls automatically.

## Bounding Batch Inspection

```python
from threading import Event
from time import monotonic

from pywhales import (
    DockerClient,
    InspectionPolicy,
    InventoryRequest,
    capture_inventory,
    observation,
)

client = DockerClient()
policy = InspectionPolicy(
    max_references=100,
    max_argv_bytes=32768,
    max_response_bytes=1_048_576,
    max_total_references=200,
    max_total_response_bytes=4_194_304,
)
cancel = Event()
request = InventoryRequest(
    kind="container",
    references=("authorized-container-name",),
    client_config=client.client_config,
)

with observation(policy, deadline=monotonic() + 30.0, cancel=cancel):
    snapshot = capture_inventory([request], policy=policy)
```

`InspectionPolicy(max_references=..., max_argv_bytes=..., max_response_bytes=...)`
requires three positive integers; there's no default policy. Optional
`max_total_references` and `max_total_response_bytes` default to `None`,
leaving aggregate accounting unbounded. The illustrative limits above aren't
a universal sizing recommendation. `observation()`
is a context manager applying those limits, plus an optional absolute
monotonic `deadline` and a `cancel` `Event`, to synchronous list/inspect
calls executed through it. Nested scopes can only tighten the active limits,
never loosen them, and the scope applies to the current context only; it
doesn't automatically propagate to worker threads you spawn inside it. It
supports the shared synchronous captured path, not streaming, TTY, input-bearing,
or uncaptured execution; don't use it as a general command sandbox.

`capture_inventory(items, *, policy)` accepts a mix of `InventoryRequest`
objects and existing resource handles, and returns an `InventorySnapshot`
whose `records` tuple preserves request order and duplicates, each with a
deeply copied, independently mutable `payload`. It never changes an existing
handle's own cache. Supported kinds are `container`, `image`, `network`,
`volume`, `node`, `service`, `config`, `secret`, `task`, `plugin`, and
`context`. Empty input launches no CLI.

`bulk_reload(handles, policy=policy)` from `pywhales.client_config` refreshes
existing handles under the same bounds; when `policy` is omitted, an active
`observation()` scope applies instead, and outside such a scope its existing
unbounded behavior is unchanged.

The per-command reference limit bounds each inspect batch; aggregate reference
accounting counts all requested occurrences, including duplicates, in one
inventory/bulk-reload call. The response limit counts combined raw stdout/stderr bytes per
invocation. Exceeding a limit raises `ObservationLimitError`; an expired
deadline raises `ObservationTimeout`; a set `cancel` event raises
`ObservationCancelled`. Failures stop/reap an active owned local CLI when one
exists; some limits reject before launch. Nothing rolls back daemon-side work,
and the regular, selected-launch
`DockerTimeout` from `default_timeout`/`timeout=` is unaffected and can still
fire independently. Aggregate response budgets apply within observation scopes
and the bounded inventory call. Snapshots aren't daemon transactions or total
memory caps. Bulk reload stages every group before updating any input cache,
but publication isn't atomic to concurrent readers.

## Waiting For Swarm Convergence

```python
from threading import Event

from pywhales import DockerClient, InspectionPolicy
from pywhales.convergence import ServiceTarget, capture_service, wait

client = DockerClient()
policy = InspectionPolicy(
    max_references=32, max_argv_bytes=65536, max_response_bytes=1_048_576
)
cancel = Event()

target = capture_service(
    client, "full-service-id", policy=policy, timeout=10, cancel=cancel
)
if isinstance(target, ServiceTarget):
    outcome = wait(target, policy=policy, timeout=120, poll_interval=1, cancel=cancel)
```

`pywhales.convergence` is an opt-in, read-only observer. `client.service.update()`,
`client.stack.deploy()`, and rollback retain their existing behavior and
never invoke it automatically; call `capture_service`/`wait` yourself after
submitting the change you want to confirm. Always pass the exact, immutable
service ID returned by your own submission, not a name; the caller must know
the complete, fixed membership of a stack before building a `StackTarget`,
never inferred from a live, possibly-changing discovery.

`capture_service(client, service_id, *, policy, timeout, cancel=None, approved_node_ids=None)`
returns a `ServiceTarget`, or a typed `Failure`/`Deadline`/`Cancelled`
outcome. It records the service's version and a fingerprint of its desired
task template and mode at that instant. Replicated services need an explicit
nonnegative replica count; Global services need `approved_node_ids`, a
nonempty tuple of distinct node IDs, at capture time, since there's no
automatic discovery of "eligible" nodes.

Use an authorized service's actual immutable ID in place of the placeholder.
Keep the target's originating `ClientConfig` stable while waiting. Unsupported
job modes and malformed service/task data fail closed.

`wait(target, *, policy, timeout, poll_interval, cancel=None)` accepts a
`ServiceTarget` or a `StackTarget(services=(...))` and returns `Success`,
`Failure`, `Deadline`, or `Cancelled`. Only current desired-running tasks
count; a changed service version or template fingerprint returns
`superseded`; `paused`/`failed`/rollback states are terminal `update_failed`
failures. Snapshot collection isn't an atomic Swarm transaction, so a final
generation recheck reduces, but can't remove, races from concurrent changes.
A successful result proves the captured task-generation conditions, not
application health or readiness. `timeout` on capture and wait is a positive
finite total budget for that call, unlike a per-launch CLI timeout; cleanup
can outlast it. `poll_interval` is also positive finite seconds.
`Deadline.detail`, when present, is a payload-free aggregate from the last
complete polling cycle, not live state at expiry.
A `Failure.cause` or a target's `template` may hold confidential wire data; don't log it without a
confidentiality policy. Cancellation or deadline expiry reaps only the local
CLI child and never cancels remote Swarm work.

## Optional Application Reconciliation

`pywhales.reconciliation` exports `Controller`, `JournalStore`, operation/lease
types, and explicit inventory/mutation/convergence adapter contracts. This is an
application-owned, one-pass journal workflow (`submit`, `claim`, `reconcile`),
not an automatic Docker scheduler or a wrapper that changes ordinary calls.
The application supplies adapters and a production durable store; the included
`MemoryJournalStore` is a thread-safe, nondurable conformance fake, not
process-crash recovery. Unknown mutation outcomes aren't automatically replayed.
Leases don't fence Docker itself; there is no exactly-once guarantee or built-in
reset/retry/delete workflow. Check installed signatures and the application's
ownership/recovery design before opting in; don't replace failed mutating calls
with blind retries.
