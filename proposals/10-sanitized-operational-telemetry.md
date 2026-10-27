# Sanitized operational telemetry

Status: Implemented (optional sanitized telemetry; offline validation only)

Owner: SDK maintainer and application observability operator
Tier: P1

## Implementation and validation

The optional [telemetry package](../pywhales/telemetry/) supplies allowlisted execution records and a bounded application-owned exporter. It does not sanitize caller output or cover every subprocess path. See [telemetry tests](../tests/pywhales/test_telemetry.py), [observation tests](../tests/pywhales/test_telemetry_observation.py), [stream tests](../tests/pywhales/test_telemetry_streams.py), [exporter tests](../tests/pywhales/test_telemetry_exporter.py), and the [user guide](../docs/template/user_guide/telemetry.md). Production configuration and authorized deployment validation remain open.

## Objective

Define an optional SDK metadata seam and application exporters that report
operation health without exporting command arguments or workload output.

## Original evidence

- [`run`](../pywhales/utils.py) snapshots diagnostic policy and starts selected
  command deadlines at local launch.
- [`ExecutionPolicy`](../pywhales/_execution_policy.py) carries stream and raw
  diagnostic settings, with per-launch snapshots.
- [`ClientConfig`](../pywhales/client_config.py) documents separate general and
  image stream budgets and the raw diagnostic opt-in.
- [`test_logging.py`](../tests/pywhales/test_logging.py) exercises synthetic
  secret values and safe/raw behavior; returned data is not redacted.
- [`test_utils.py`](../tests/pywhales/test_utils.py) covers timeout and stream
  ownership, including byte-budget behavior.

## Proposed contract

- Add an optional SDK observer receiving a versioned, allowlisted metadata
  record for launch and completion. No observer means current behavior.
- Include bounded operation family, outcome class, local duration and measured
  output byte counts when available. Mark unavailable counts explicitly instead
  of inferring payload content or total process memory from a queue budget.
- An application controller may add queue wait, admission outcome and readiness
  duration as separate measurements. These clocks have distinct start and end
  boundaries; readiness doesn't mean successful CLI exit.
- Use a fixed vocabulary for metric dimensions. Never use raw endpoint, image,
  container, operation ID, command arguments or error text as metric labels.
  Any approved endpoint grouping must have operator-bounded cardinality.
- Exclude environment values, credentials, argv, returned output, exception raw
  attributes and arbitrary workload payloads from every metadata record.
- This rule remains in force when `raw_diagnostics=True`; that setting controls
  existing troubleshooting diagnostics, not the telemetry confidentiality seam.
- Observer and exporter exceptions are contained and never change the Docker
  result, stream ownership, timeout or original operation exception.
- The core seam imposes no mandatory monitoring dependency or background queue.
  If an optional exporter owns a queue, configure its finite bound and explicit
  drop policy, expose drop counts, and define shutdown and flush ownership.
- The SDK owns record schema and callback timing; the application owns exporter
  configuration, access, retention and any readiness instrumentation.

## Compatibility and boundaries

Default calls retain existing return values, exception types and diagnostic
behavior. Telemetry doesn't sanitize caller output or raw diagnostic sinks.
Record timing must not require waiting for remote readiness inside an SDK call.

## Dependencies

- [01: workload and support contract](01-fleet-workload-and-support-contract.md)
  defines approved dimensions, clocks, retention and observation objectives.

## Non-goals

No mandatory monitoring stack, arbitrary text redaction, durable audit log,
per-resource metric series or claim of total process-memory accounting.

## Acceptance scenarios

1. Happy path: a local child emits output and the observer receives the declared
   byte count and duration while the caller receives unchanged output.
   Test seam: local child and recording observer. Surface: driver prints only
   allowlisted metadata and separately verifies the caller's result.
2. Confidentiality and failure: with raw diagnostics enabled and synthetic
   secrets in argv, environment and output, telemetry contains none; a throwing
   observer leaves both successful results and original failures unchanged.
   Test seam: recording and failing callbacks. Surface: driver scans serialized
   records and checks operation outcomes.
3. Adjacent timing and pressure: queued and readiness delays appear separately
   from local duration; a full exporter queue applies its declared drop policy
   without blocking the operation. Test seam: fake clocks and bounded exporter.
   Surface: driver reports the separate durations and exact drop count.

## Future validation and decisions

Local-child and synthetic-data tests cover the offline seam. Inspect exported records in a separately authorized isolated deployment.
Operators must select approved aggregation, retention, queue limits and drop
policy. These choices block a production telemetry configuration, not the seam.
