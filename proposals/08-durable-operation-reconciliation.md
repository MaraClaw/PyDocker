# Durable operation reconciliation

Status: Implemented (optional reconciliation; offline validation only; durable backend unqualified)

Owner: application controller maintainer and operator
Tier: P1

## Implementation and validation

The optional [reconciliation package](../pywhales/reconciliation/) provides a `JournalStore` protocol, conditional transitions and lease-generation checks for reconciliation. Its `MemoryJournalStore` is a NONdurable conformance fake. A production durable backend must be supplied and qualified by the application; journal fencing cannot fence Docker itself. See the [store tests](../tests/pywhales/test_reconciliation_store.py), [recovery tests](../tests/pywhales/test_reconciliation_recovery.py), [controller tests](../tests/pywhales/test_reconciliation.py), and [user guide](../docs/template/user_guide/reconciliation.md). Live recovery qualification remains blocked.

## Objective

Let an optional controller recover a named operation after interruption by
combining a durable journal with observed inventory and explicit ownership.

## Original evidence

- [`StackCLI.deploy`](../pywhales/components/stack/cli_wrapper.py) returns after
  the deploy CLI exits and doesn't promise service convergence.
- [`SystemCLI.events`](../pywhales/components/system/cli_wrapper.py) provides
  an owned event stream, not a durable journal.
- [`bulk_reload`](../pywhales/_bulk_reload.py) stages validated group results
  before publication; groups don't form a daemon transaction.
- [`test_bulk_reload.py`](../tests/pywhales/test_bulk_reload.py) covers grouping
  and failure without publishing partial cache updates.
- [`test_command_deadlines.py`](../tests/pywhales/components/test_command_deadlines.py)
  covers local timeout behavior; local process termination doesn't prove rollback.

## Proposed contract

- The caller supplies a stable operation identity, target endpoint identity and
  canonical desired specification. Store a versioned desired-spec hash and an
  immutable reference to approved input, excluding plaintext secrets.
- A store adapter provides durable create-if-absent, read and compare-and-swap
  transitions with a version and explicit failure on conflict or unavailability.
- Repeated identity plus matching spec returns or resumes the same journal entry;
  the same identity with a different spec returns a conflict without mutation.
- Entries record submitted intent, observed resource identities, attempt state,
  convergence outcome and an explicit unknown state for ambiguous mutations.
- The store contract defines durability, transaction boundaries, clock assumptions,
  retention and recovery guarantees for its chosen backend before deployment.
- A worker claims a lease with a monotonically increasing fencing generation;
  all journal writes use conditional version and generation checks.
- A stale worker must stop initiating new actions after losing its lease. Where
  the Docker endpoint can't enforce a fence, the design must describe the race
  and require reconciliation rather than claim the remote action was fenced.
- Aggregate controllers coordinate through the selected store or an operator-
  assigned single writer. Admission in 03 alone is only process-local.
- On a transport timeout or lost response, record unknown and inspect current
  owned resources and desired state before deciding whether another mutation is
  safe. Never blindly retry an ambiguous mutation.
- Events are non-durable hints for prompt refresh. Periodic inventory repairs
  missed events; convergence uses the separately defined readiness contract.
- Storage ownership uses the namespace and classifications from 11. The journal
  links resources to the operation without making 11 depend on this controller.
- Store failures stop new mutation decisions and surface a distinct recoverable
  error; journal and diagnostic records must not persist credentials or payloads.

## Compatibility and boundaries

The journal is optional and doesn't alter existing SDK calls or their exceptions.
Its adapter owner controls access, encryption, backup and retention. A journal
transition is not a Docker transaction, an exactly-once guarantee or a scheduler.
Service placement and task recovery remain with the selected orchestrator.

## Dependencies

- [02: stable inventory](02-stable-inventory-and-bounded-inspection.md).
- [03: bounded admission](03-bounded-endpoint-admission.md).
- [07: convergence](07-service-and-stack-convergence.md).
- [11: storage ownership](11-storage-ownership-and-retention.md).

## Non-goals

No automatic rollback of arbitrary remote work, universal distributed lock,
daemon fencing primitive or orchestrator replacement is included.

## Acceptance scenarios

1. Happy path: submit the same identity and spec twice; one journal entry and
   one logical operation result. Test seam: transactional fake store and inventory.
   Surface: controller driver restarts and reports the existing entry.
2. Ambiguous failure: simulate a mutation that succeeds remotely but loses its
   response; the next worker inventories first and issues no duplicate mutation.
   Test seam: fault-injected store and daemon adapter. Surface: driver trace shows
   unknown, inventory, then resolved state, with one mutation recorded.
3. Adjacent concurrency: after lease transfer, a stale generation cannot commit
   a transition; a missed event is corrected by inventory. Test seam: two workers
   and controlled clock. Surface: driver shows conflict and eventual observed state.

## Future validation and decisions

Fake-store and simulated-endpoint tests are available. Restart and lost-response drills require a disposable authorized environment and a qualified durable backend.
The operator must choose store backend, durability and lease policy, spec
canonicalization, retention and single-writer or coordination model. Those choices
block production recovery guarantees and the supported failure envelope.
