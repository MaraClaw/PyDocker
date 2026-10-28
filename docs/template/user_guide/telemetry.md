# Sanitized execution telemetry

Telemetry is optional. Set `telemetry_observer` on `DockerClient` or
`ClientConfig` to receive immutable `LaunchRecord` and `CompletionRecord` objects
from `pywhales.telemetry`. These names aren't exported from the package root.
There is no monitoring dependency, global observer, background worker, or network
export by default. Without an observer, command results and errors are unchanged.

```python
from pywhales import DockerClient
from pywhales.telemetry import CompletionRecord, TelemetryRecord


def observe(record: TelemetryRecord) -> None:
    if isinstance(record, CompletionRecord):
        print(record.family, record.outcome, record.duration_seconds)


client = DockerClient(telemetry_observer=observe)
```

Construction is inert. The observer is snapshotted by reference at each local
launch, alongside the client's execution policy. Later config mutations affect
new launches only, not a running stream's completion callback. Commands built
from that client retain the policy through ordinary list composition, shallow
copy, and deep copy. A supplied `client_config` takes precedence over constructor
settings, including `telemetry_observer`.

## Version 2 schema and confidentiality

The vocabulary is closed. `schema_version` is `2`; `phase` is `launch` or
`completion`; `family` is `captured` or `stream`. Family describes the shared
execution path, not an operation inferred from argv. Completion `outcome` is
exactly one of `success`, `failure`, `timeout`, or `closed`.
Each launch attempt has an opaque random `attempt_id`, shared with its completion;
it carries no command, endpoint, credential, PID, or remote-operation meaning.

Records never include commands or arguments, endpoint addresses, contexts,
environment keys or values, stdin, stdout/stderr payloads, exception text, or
free-form labels. `raw_diagnostics=True` does not relax this boundary. Explicit
returned output, raw exception attributes, and application-owned logging remain
confidential and outside telemetry's sanitization contract. An application can
leak its own data from its callback or sink; PyWhales doesn't sanitize their code.

Launch is emitted immediately before an attempted local spawn, after shared
preflight validation. It does not prove that spawn succeeded. A spawn failure has
a failure completion with unavailable byte counts; rejection before the launch
seam emits neither record. The shared `run()` and process-stream factories are
covered. Separate direct subprocess paths, including image archive load/save,
are not covered; this is not a complete audit of every Docker invocation.

Completion adds `duration_seconds`, `stdout_bytes`, and `stderr_bytes`. Duration
uses a monotonic clock, starting after the launch callback returns and ending
before completion delivery. It includes local spawn, execution and cleanup; for
streams it also includes consumer pauses until exhaustion, failure delivery, or
close. It is not admission queue wait, Docker daemon task duration, rollout
convergence, or application readiness. Callback time isn't a separate metric.
Completion also includes `return_code`, the native status of the reaped local
process, independent of `outcome`, or `None` when unavailable (including failed
spawn). It does not describe daemon-side work.

Counts measure raw bytes captured or read from each local pipe, before decoding,
newline removal, or application transforms. They include read bytes discarded by
a stream close or response/buffer limit. They can be partial on timeout, failure,
or early closure and do not assert how much the child or remote workload wrote.
A pipe that was read but produced no bytes reports `0`; inherited/uncaptured
output or an unavailable capture reports `None`, never a fabricated zero. Launch
records have no duration or byte-count fields because those measurements aren't
available yet.

## Stream completion and observer behavior

Each launch attempt has at most one completion. Captured execution completes on
return or failure. A stream completes only when its consumer exhausts it,
observes a terminal error, or closes it, after local process/thread cleanup. Child
exit alone does not complete an unconsumed stream. Always use a context manager
or explicitly close it when stopping early; a bare loop `break` leaves ownership
with the caller and doesn't promise a completion record.

Natural exhaustion with zero exit status is `success`. Natural nonzero exit,
spawn failure, reader error, or a recorded output-budget error is `failure`.
A recorded local or observation deadline is `timeout`. Explicit closure before
natural completion is `closed`, even when close wins a race against a pending
natural nonzero exit. A timeout or reader/budget failure already recorded before
close keeps its timeout/failure outcome and original one-time error delivery.
Neither `closed` nor `timeout` claims remote cancellation or rollback. Mapping
or context-body exceptions still close the local stream without being replaced
by observer exceptions; an otherwise healthy stream then reports `closed`.

Observers run synchronously on the launching/consuming/closing thread, outside
stream locks. They must be quick, thread-safe across concurrent clients/streams,
and non-reentrant. Ordinary `Exception` from either callback phase is contained,
without logging its details, and cannot replace a command result or original
exception. Process-control `BaseException`, blocking, reentrant callbacks, and
adversarial side effects such as changing execution state are outside this
contract. Slow callbacks delay their calling thread; telemetry is not a sandbox.

## Bounded, application-owned export

`BoundedExporter` is an optional thread-safe buffer, not a worker or transport.
The application must choose a positive integer `capacity` and the explicit
`drop_policy="drop_newest"`. Admission never calls the sink: when full, the newest
record is discarded and `dropped` increases. Launch and completion records can
be dropped independently, so exported pairs are not guaranteed. `queued` counts
pending records; `export_errors` counts sink failures discarded without retry.

```python
from pywhales import DockerClient
from pywhales.telemetry import BoundedExporter, TelemetryRecord


def sink(record: TelemetryRecord) -> None:
    # Hand off only these sanitized fields to an application-owned backend.
    print(record)


exporter = BoundedExporter(sink, capacity=256, drop_policy="drop_newest")
client = DockerClient(telemetry_observer=exporter)
try:
    # Run application work here; periodically flush on an application-owned thread.
    exporter.flush()
finally:
    exporter.shutdown(flush=True)
```

The capacity above is illustrative, not a recommended production budget.
`flush()` exports one finite snapshot and returns the number of successful sink
calls. New arrivals wait for a later flush. One snapshot of at most `capacity`
records may coexist with at most `capacity` queued records. Concurrent flushes
are serialized. A sink's ordinary exceptions are counted and contained without
rendering them; failed records are not retried. A sink must not recursively call
`flush()` or `shutdown()`.

The application owns flush scheduling, sink I/O timeouts and shutdown. `flush()`
and `shutdown()` can block on the sink: a finite queue is not a sink-latency
guarantee. `shutdown(flush=True)` stops admission and exports the final snapshot;
`shutdown(flush=False)` counts pending records as dropped and discards them.
Later arrivals are dropped. Repeated shutdown is safe and exports nothing.
Close or exhaust owned streams before exporter shutdown if their completion
records should still be admitted. No implicit retry, network I/O, worker thread,
or process-exit hook is installed.
