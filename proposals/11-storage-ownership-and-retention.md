# Storage ownership and retention

Status: Implemented (opt-in planning, pressure and approval protocols; offline validation only)

Owner: application platform operator and controller maintainer
Tier: P1

## Implementation and validation

The [storage package](../pywhales/storage/) provides protective retention planning, pressure checks and approval-store protocols with read-only rechecks. It has no deletion executor or durable approval backend. See [planning tests](../tests/pywhales/test_storage_planning.py), [pressure tests](../tests/pywhales/test_storage_pressure.py), [validation tests](../tests/pywhales/test_storage_validation.py), and the [user guide](../docs/template/user_guide/storage.md). Production qualification, including provider behavior and backup/restore drills, remains blocked.

## Objective

Define storage ownership, retention planning and pressure response so a future
controller can identify protected data and stop expensive admission safely.
This proposal delivers planning and pressure contracts, not automatic pruning.

## Original evidence

- [`SystemCLI.disk_free`](../pywhales/components/system/cli_wrapper.py) reports
  Docker category summaries with rounded bytes, not filesystem free bytes or
  inode headroom. A host or storage-provider source is needed for those measures.
- The same [`system` wrapper](../pywhales/components/system/cli_wrapper.py) calls
  prune with `--force`; this is already destructive without a prompt.
- [`VolumeCLI`](../pywhales/components/volume/cli_wrapper.py) accepts labels and
  driver options and also offers forced prune, including unused named volumes.
- [`ContainerCLI.create`](../pywhales/components/container/cli_wrapper.py)
  forwards logging and storage options; effectiveness depends on the runtime.
- [`test_system.py`](../tests/pywhales/components/test_system.py) includes live
  operations; its fixture and destructive cases need a disposable daemon.

## Proposed contract

- Define a versioned ownership-label namespace for endpoint, owner and resource
  class, with an optional operation identity compatible with proposal 08.
  A label alone is metadata, never tenant authentication or deletion authority.
- Classify observed volumes and other retained objects as borrowed, persistent,
  ephemeral or rollback-protected. Unknown ownership is protected by default.
- An inventory plan includes resource identity, observed attachment, class,
  retention deadline, protection reason and evidence timestamp. It performs no
  deletion and doesn't infer eligibility from Docker's unused state alone.
- A separate durable approval record names an exact owned candidate, authority,
  policy version, reason and expiry. Approval and execution are distinct acts.
- Specify a future execution boundary that rechecks ownership, current attachment,
  active operations, expiry and protection immediately before deletion; this
  proposal does not implement that destructive executor.
- Never mark borrowed or persistent data or protected rollback material eligible
  solely because it is detached, old or reported reclaimable.
- Pressure input comes from an operator-configured host or storage provider with
  freshness and failure status for free bytes and inodes. Docker category totals
  remain approximate inventory context and aren't a substitute for headroom.
- Missing or stale required pressure data follows an explicit fail-closed or
  operator-approved degraded policy. Thresholds trigger admission throttling or
  rejection through 03, with a distinct reason and a documented recovery threshold.
- Plans and approvals contain no credentials or application data. The operator
  owns their access, retention and backup; authorization lives outside labels.
- Validate backup and restore for persistent data before any future deletion
  approval. Preserve rollback images, caches or logs where policy requires them.

## Compatibility and boundaries

The planner is opt-in and doesn't change current forced prune or remove methods.
No daemon-wide prune is invoked by a planning or pressure decision. Retained
persistent data can require more capacity instead of deletion.

## Dependencies

- [01: workload and support contract](01-fleet-workload-and-support-contract.md).
- [03: bounded admission](03-bounded-endpoint-admission.md).
- [09: security and resource profiles](09-security-and-resource-profiles.md).

Proposal [08](08-durable-operation-reconciliation.md) consumes this ownership
contract; 11 doesn't require the journal to exist.

## Non-goals

No automatic prune, deletion executor, blanket cleanup, quota guarantee,
tenant authorization by label or unapproved backup/restore operation.

## Acceptance scenarios

1. Happy path: inventory of owned ephemeral and protected persistent objects
   produces a plan with evidence and no deletion calls. Test seam: fake inventory
   and durable approval store. Surface: driver prints the plan and zero mutations.
2. Pressure failure: stale or missing required host headroom causes the configured
   reject or degraded outcome; it never invokes prune. Test seam: fake provider and
   admission owner. Surface: driver reports the pressure reason and zero deletes.
3. Adjacent safety: an unused borrowed volume and rollback image stay protected,
   while an approved candidate whose attachment changes fails the proposed recheck.
   Test seam: fake inventory snapshots and approval. Surface: driver shows the
   invalidated approval, protected entries and no destructive call.

## Future validation and decisions

Offline tests use fake provider and inventory data. Validate pressure and a
backup/restore drill on explicitly authorized disposable storage, without pruning
a shared host, after separate authorization.
Operators must select label authority, retention, approval, data provider,
thresholds and backup standards. Those choices block production eligibility,
pressure automation and any future destructive execution design.
