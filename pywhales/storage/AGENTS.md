# Storage Planning And Pressure

## Local Navigation

- `_contracts.py`: `LABEL_NAMESPACE`, `StorageError`, `ResourceClass`, `OwnershipLabels`, `ResourceIdentity`,
  `ResourceObservation`, `RetentionPolicy`. `_planning.py`: `classify`, `plan_retention`, `PlanEntry`,
  `ProtectionReason`. `_approvals.py`: approval values, `ApprovalStore`, rechecks. `_pressure.py`: `assess_pressure`
  and its policy types. `_gate.py`: `PressureGate`. `__init__.py` is the export list.
- User prose lives in `docs/template/user_guide/storage.md`; edit the template, not generated output.
- Nothing here deletes, prunes, or changes Docker state. Don't add an executor to these modules.

## Retention Planning

- `classify` is pure and needs positive evidence. `plan_retention` returns one `PlanEntry` per observation in input
  order, protected and duplicate evidence included. `CANDIDATE` only permits a separate approval, never deletion.
- Only verified, owned `EPHEMERAL` objects can be candidates. `_reason` checks in order: endpoint, known
  incarnation, evidence age within `max_age` (future timestamps are stale), verified ownership matching the policy owner
  and endpoint, rollback flag, class, `attached is False`, empty `active_operations`, then a passed retention deadline.
  `None` attachment, operations, or deadline means unknown and stays protected. A new protective reason belongs in
  `ProtectionReason` with a test.
- `incarnation=None` stays protected even when labels match, because Docker volume names are reusable.
- Labels under `io.pywhales.storage.v1.*` are metadata. `from_labels` returns `None` for missing, malformed, or
  unknown-version input. `ownership_verified` comes from the caller's independent check, never from the labels.
  Reconciliation's `ownership_labels` must keep decoding here, within the 64-character token bound.
- `ResourceIdentity` and `ResourceClass` here differ from the reconciliation types of the same names: `kind` is a
  string rather than `InventoryKind`, and the class is a `StrEnum` rather than a `Literal`.
- Flags must be real `bool` values (`"false"` and `1` raise), timestamps timezone-aware, evidence lifetimes positive.
  `StorageError` names a field only, never a value.

## Approvals

- `ApprovalRecord` pairs an exact candidate `PlanEntry` with an `ApprovalAuthority` (id, authority, reason, approval
  and expiry times). Construction re-classifies the candidate at `approved_at` and rejects non-candidates and plans
  made after approval. Persistent data can't be approved.
- `ApprovalStore` is operator-provided, durable, and immutable. `create_if_absent` atomically keeps the first record
  and returns `False` for an existing key; `read` uses the exact resource incarnation plus approval id. There is no
  update, consume, or delete. Unavailability must raise, never look like absence or approval.
- `record_approval` accepts only an exact retry: on `False` it reads the key and raises
  `StorageError("approval.conflict")` unless the stored record is equal. `read_approval` rejects a mismatched key.
  Resolving an ambiguous write by reading never authorizes execution or a mutation retry.
- `recheck_approval` is observational. It checks the approval window, policy equality, unchanged resource,
  ownership and deadline, evidence no older than the approval, and candidate status. It consumes nothing, and state can
  change after it returns. A future executor must define its own authorization and race handling.

## Pressure

- `Headroom` is provider evidence for one endpoint and provider scope, not Docker category totals. A scope mismatch
  returns `SCOPE` and rejects even under `DEGRADED`, keeping the latch as it was.
- A missing sample, `FAILED` status, an absent required measurement, or a stale or future sample is `MISSING` or
  `STALE`. `REJECT` refuses. `DEGRADED` admits only while unlatched, so missing data never clears an existing latch.
- Hysteresis: at or below `low` rejects and latches. Every required measurement must reach `recovery` to clear.
  `Threshold` needs ints with `0 <= low < recovery`.
- `PressureGate` is application-owned. Callers serialize `check` and `call` externally; it's not a quota
  reservation or an atomic fence. `call` checks before creating the admission request and again after the permit
  is granted, so a post-grant rejection counts as failed work in `AdmissionOwner`, not saturation.
- Provider exceptions propagate and never admit. Provider, clock, and request factory are trusted inputs for the
  same endpoint. The gate provides no cancellation, rollback, or deletion.

## Related Tests

- `tests/pywhales/test_storage_planning.py`: protection reasons, label round trip, approval store, rechecks.
- `tests/pywhales/test_storage_validation.py`: malformed evidence and owned versus protected inventory.
- `tests/pywhales/test_storage_pressure.py`: missing or stale data, hysteresis, checks around admission.
- `tests/pywhales/test_reconciliation_ownership.py`: reconciliation labels decode through `OwnershipLabels`.
