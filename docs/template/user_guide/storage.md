# Storage Ownership, Retention And Pressure

Import the opt-in APIs from `pywhales.storage`. They plan from inert snapshots,
record immutable approvals through a store protocol, and gate deferred admission.
There is no inventory collector, deletion executor, approval consumption, prune,
backup/restore implementation or reconciliation dependency. Existing low-level
remove and forced-prune methods are unchanged; they are not called here.

## Metadata Is Not Authority

`OwnershipLabels` emits the versioned keys `io.pywhales.storage.v1.endpoint`,
`io.pywhales.storage.v1.owner`, `io.pywhales.storage.v1.class`, and optionally
`io.pywhales.storage.v1.operation`. Classes are `borrowed`, `persistent`,
`ephemeral` and `rollback-protected`. Operation identifiers can also identify
future reconciliation work; no journal is required or integrated here.

Labels are metadata, never authentication or deletion authority. An operator's
inventory provider must independently verify ownership, exact endpoint and
resource identity, incarnation, attachment, active operations and rollback state.
Unknown ownership, unknown attachment/operations/deadline, stale/future evidence
and missing incarnation are protected. Docker volume names are reusable: a name
alone is not sufficient incarnation evidence. An unused, old or reclaimable object
is not thereby eligible. Borrowed and persistent data and protected rollback
material stay protected regardless of age or detachment.

## Offline Planning And Approval Example

The following example performs no Docker I/O or deletion. The map is a serialized
**test fake**, not durable storage or a production authorization mechanism.
Use non-sensitive resource/owner/operation identifiers; plans and approvals must
not contain credentials, secret values or application payloads.

```python
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from pywhales.admission import EndpointIdentity
from pywhales.storage import (
    ApprovalAuthority,
    ApprovalKey,
    ApprovalRecord,
    OwnershipLabels,
    RecheckContext,
    RecheckReason,
    ResourceClass,
    ResourceIdentity,
    ResourceObservation,
    RetentionPolicy,
    plan_retention,
    read_approval,
    recheck_approval,
    record_approval,
)


class ExampleApprovalStore:
    def __init__(self) -> None:
        self.records: dict[ApprovalKey, ApprovalRecord] = {}

    def create_if_absent(self, record: ApprovalRecord) -> bool:
        if record.key in self.records:
            return False
        self.records[record.key] = record
        return True

    def read(self, key: ApprovalKey) -> ApprovalRecord | None:
        return self.records.get(key)


now = datetime(2026, 1, 1, tzinfo=UTC)
endpoint = EndpointIdentity("offline")
policy = RetentionPolicy(endpoint, "operator", "v1", timedelta(seconds=30))
ephemeral = ResourceObservation(
    resource=ResourceIdentity(endpoint, "volume", "scratch", "incarnation-1"),
    ownership=OwnershipLabels(endpoint, "operator", ResourceClass.EPHEMERAL, "op-1"),
    ownership_verified=True,
    attached=False,
    active_operations=(),
    retention_deadline=now - timedelta(days=1),
    observed_at=now,
    rollback_protected=False,
)
persistent = replace(
    ephemeral,
    ownership=OwnershipLabels(
        endpoint,
        "operator",
        ResourceClass.PERSISTENT,
    ),
)
borrowed = replace(
    ephemeral,
    ownership=OwnershipLabels(
        endpoint,
        "operator",
        ResourceClass.BORROWED,
    ),
)
rollback = replace(
    ephemeral,
    resource=ResourceIdentity(
        endpoint,
        "image",
        "rollback-image",
        "image-incarnation",
    ),
    rollback_protected=True,
)
entries = plan_retention((ephemeral, persistent, borrowed, rollback), policy, now)
print([entry.reason.value for entry in entries])
approval = ApprovalRecord(
    entries[0],
    ApprovalAuthority(
        "approval-1",
        "operator",
        "retention-expired",
        now,
        now + timedelta(minutes=5),
    ),
)
store = ExampleApprovalStore()
record_approval(store, approval)
stored = read_approval(store, approval.key)
assert stored is not None
assert (
    recheck_approval(stored, ephemeral, RecheckContext(policy, now))
    is RecheckReason.VALID
)
changed = replace(ephemeral, attached=True)
print(recheck_approval(stored, changed, RecheckContext(policy, now)).value)
assert read_approval(store, approval.key) == stored
print("deletions: 0")
```

Each `PlanEntry` retains identity, ownership/class, observed attachment and active
operations, retention deadline, evidence time, policy version, planning time and
protection reason. Only a fresh, independently verified owned ephemeral object
with known detached/inactive state and an expired deadline becomes a candidate.
`ApprovalAuthority` adds approval identity, approving authority, bounded reason,
approval time and exclusive expiry. All times must be timezone-aware.

An application-provided `ApprovalStore` must durably and atomically implement
`create_if_absent(record)` without overwriting an existing exact key, and
`read(ApprovalKey(resource, approval_id))`. The key includes endpoint, kind,
resource identity and incarnation as well as approval identity. `record_approval`
accepts an identical retry; conflicting content raises `StorageError`.
`read_approval` rejects a returned record with a different key. Store errors
propagate; neither function consumes, deletes or silently replaces an approval.
Operators supply store authorization, access, retention, backup and durability.

`recheck_approval` is a read-only observational boundary, not deletion permission
or an atomic fence. It checks the same resource/incarnation, ownership metadata,
retention deadline and policy, current independent verification, attachment,
active operations, protection, freshness and approval expiry. Evidence must be
at or after approval. A future separately designed executor would need new
evidence immediately before mutation and its own race/authorization contract.
No store concurrency model is imposed on future reconciliation interfaces.

## Fresh Headroom Before Deferred Admission

Docker `system.disk_free()` reports approximate category inventory, not filesystem
free bytes or free inodes. Supply `PressureProvider.read()` from an authorized host
or storage source scoped to the selected endpoint and provider. It returns
`Headroom`, or `None` for missing data; `ProviderStatus.FAILED` records collection
failure. Unexpected provider exceptions propagate and never silently admit work.

This additional offline example uses the same `endpoint` and `now` as above:

```python
from pywhales.admission import AdmissionOwner, ClassLimit, PressureClass
from pywhales.storage import (
    Headroom,
    MissingPressurePolicy,
    PressureGate,
    PressurePolicy,
    ProviderStatus,
    StoragePressureRejected,
    Threshold,
)


class ExampleProvider:
    def __init__(self) -> None:
        self.sample: Headroom | None = None

    def read(self) -> Headroom | None:
        return self.sample


provider = ExampleProvider()
pressure_policy = PressurePolicy(
    endpoint=endpoint,
    provider="example-filesystem",
    max_age=timedelta(seconds=10),
    bytes_threshold=Threshold(1024, 2048),
    inodes_threshold=Threshold(10, 20),
    missing=MissingPressurePolicy.REJECT,
)
gate = PressureGate(pressure_policy, provider, lambda: now)
pressure_class = PressureClass("create")
owner = AdmissionOwner(endpoint, {pressure_class: ClassLimit(1, 0)})
try:
    gate.call(lambda: owner.request(pressure_class), lambda: "admitted")
except StoragePressureRejected as error:
    print(error.decision.reason.value)
provider.sample = Headroom(
    endpoint,
    "example-filesystem",
    now,
    ProviderStatus.OK,
    4096,
    40,
)
print(gate.call(lambda: owner.request(pressure_class), lambda: "admitted"))
```

Configure at least one required metric; a `None` threshold means that metric is
not required. Reject at or below each low threshold; once pressure is latched,
all required measurements must reach their higher recovery thresholds. Required
measurements must be present, correctly scoped, nonnegative and no older than
`max_age`; future timestamps are rejected as stale. With
`MissingPressurePolicy.DEGRADED`, missing/failed/stale data can admit only if no
pressure is already latched. Missing data cannot clear a latch; wrong endpoint or
provider never degrades into approval. Carry latch state between observations.

`PressureGate.call` checks before invoking the deferred admission factory, then
checks again after the admission permit is granted and before the operation.
The caller must bind provider, factory and operation to the same approved
endpoint. Rejection has a distinct storage reason, not `BUSY` or `QUEUE_FULL`.
A post-grant pressure rejection releases the permit and counts as failed admitted
work. Use a callable such as `lambda: create_from_plan(client, plan)`, never an
already-started SDK call. Pressure checks do not reserve storage, cancel active
operations, roll back mutations, or guarantee a quota. The application owns gate
lifetime and serializes access to its mutable latch.

## Production Boundary

No production qualification is claimed. Operators still choose and validate
label authority, durable approvals, providers, freshness, thresholds, recovery,
retention and protection rules. Persistent data may require capacity rather than
deletion. Backup/restore drills, effective Docker log/storage configuration and
provider behavior need separate authorization on disposable resources. Persistent
data is never eligible here, even if someone supplies an approval reason.

{{autogenerated}}
