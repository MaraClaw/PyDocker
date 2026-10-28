# Service and stack convergence

`pywhales.convergence` is an opt-in, read-only observer. Deploy, service update,
and rollback retain their existing behavior and do not invoke it. Capture each
exact immutable service ID after submission; the caller must know the complete,
fixed membership of a stack. Never infer it from an empty or changing discovery.

```python
from threading import Event

from pywhales import DockerClient
from pywhales.convergence import ServiceTarget, StackTarget, capture_service, wait
from pywhales.inventory import InspectionPolicy

client = DockerClient()
policy = InspectionPolicy(
    max_references=32, max_argv_bytes=65536, max_response_bytes=1048576
)
cancel = Event()
first = capture_service(
    client, "full-service-id-1", policy=policy, timeout=10, cancel=cancel
)
second = capture_service(
    client, "full-service-id-2", policy=policy, timeout=10, cancel=cancel
)
if isinstance(first, ServiceTarget) and isinstance(second, ServiceTarget):
    target = StackTarget(services=(first, second))
    outcome = wait(target, policy=policy, timeout=120, poll_interval=1, cancel=cancel)
```

Capture returns `ServiceTarget`, `Failure`, `Deadline`, or `Cancelled`. The waiter
returns `Success`, `Failure`, `Deadline`, or `Cancelled`. A stack target must be
nonempty and contain distinct service IDs. Targets retain the originating mutable
client configuration; do not change its endpoint while using them.

Each target records the service version and a fingerprint of its complete desired
task template and mode. Task specs are compared exactly, including unknown wire
fields, except that omitted `Runtime` and `Runtime: "container"` are equivalent.
The task object's own version is not a service-template generation. Only replicated
long-running container services with an explicit nonnegative replica count are
supported. Global, job, unknown and malformed modes fail closed. A zero-replica
service succeeds only with no desired-running tasks and a stable generation.

Only current desired-running tasks count; historical shutdown tasks do not. Each
required slot must contain exactly one matching running task. Missing, stale,
duplicate or ambiguous evidence remains pending. `updating` remains pending;
absent update status or `completed` may succeed. `paused`, `failed`, and all
rollback states are terminal `update_failed` failures, including a completed
rollback. Unknown update states fail as malformed. Changed service versions or
template fingerprints return `superseded`. Inspection and transport errors return
`observation` failures with their original cause. Snapshot collection is not an
atomic Swarm transaction: the final generation recheck reduces, but cannot remove,
concurrent-change races.

The policy limits each CLI invocation; task inspections use one reference at a time.
Capture and wait each start their own positive finite monotonic timeout. Cancellation
or deadline terminates and reaps an owned local CLI child; cleanup can outlast the
deadline and does not cancel remote daemon work. No mutation is retried or rolled
back. Running Swarm tasks are not evidence of application health or readiness.

The target's `template` and `Failure.cause`, including exception raw attributes,
may contain confidential service settings, command arguments, and output. Do not
log or expose them without an appropriate confidentiality policy. The template is
excluded from target representations and the cause from failure representations.
