# Service and stack convergence

Status: Implemented (opt-in observation; offline validation only; live qualification blocked)
Tier: P1. Owner: SDK maintainer and service operator.

## Implementation and validation

The opt-in [convergence package](../pywhales/convergence/) observes fixed service or stack targets without changing deploy, update or rollback behavior. [Offline tests](../tests/pywhales/test_convergence.py) include local fake CLI observations; the [user guide](../docs/template/user_guide/convergence.md) describes outcomes, deadlines and limitations. Running tasks do not prove application readiness. Authorized disposable Swarm qualification remains blocked and has not been performed.

## Objective

Offer an opt-in observation workflow that reports Swarm service or stack
convergence separately from the existing mutation commands.

## Original evidence

- [`StackCLI.deploy`](../pywhales/components/stack/cli_wrapper.py) returns a
  stack handle after the deploy CLI exits and explicitly does not guarantee task
  convergence. Stack `services` resolves listed service IDs individually.
- [`ServiceCLI.ps`](../pywhales/components/service/cli_wrapper.py) and
  `StackCLI.ps` return task handles, including task history. Service inspection
  and `Service.update` are separate operations.
- [`ServiceInspectResult`](../pywhales/components/service/models.py) contains
  service version, spec, and update status. [`TaskInspectResult`](../pywhales/components/task/models.py)
  contains task spec, service ID, slot, desired state, and observed status.
- [`run`](../pywhales/utils.py) supports an optional local launch timeout, but
  existing service and stack listing/inspection APIs do not expose a total
  workflow deadline. [`test_service.py`](../tests/pywhales/components/test_service.py)
  and [`test_stack.py`](../tests/pywhales/components/test_stack.py) cover
  inspection and task discovery; tests were read, not run.

## Proposed contract

- Add a separate waiter accepting a service target or an explicit fixed stack
  membership target, a positive finite total timeout, polling interval, and a
  cancellation signal. No existing deploy, update, or rollback call waits via
  this API unless the caller invokes it explicitly.
- Require the caller to capture and supply target service IDs and target
  generation evidence after submission. For a stack, capture the expected
  service IDs and generation evidence explicitly; do not infer completeness
  from an empty or still-changing discovery response.
- Define generation evidence as the inspected service version plus a canonical
  fingerprint of the relevant desired task template and mode. Extend inspected
  wire fields as needed to compare task templates without discarding material
  fields. A version alone is not proof that a task belongs to that generation.
- Match tasks to the target service, current desired slots, and target template;
  exclude shutdown/history tasks and count at most one current running task per
  slot. Where evidence is missing, ambiguous, or insufficient to distinguish a
  stale task, do not report success. Concurrent target changes return a typed
  superseded/failure result rather than silently following a newer generation.
- Initially support replicated long-running services with an explicit desired
  replica count, including zero replicas. Define zero success as no matching
  desired-running tasks plus a stable target spec and non-failed update state.
  Global, job, unknown, and malformed modes return an explicit unsupported or
  observation-failure result, never inferred success.
- Success requires every fixed target to retain its captured generation, have
  the requested number of matching running slots, and have no paused, failed,
  or rollback-in-progress update state. Failed or paused updates produce a
  typed failure with the affected target and safe diagnostic metadata.
- Return a typed outcome distinguishing success, terminal failure, deadline,
  and cancellation. Transport and malformed observation failures retain their
  cause in a typed failure without treating them as readiness or retrying a
  mutation. Document whether caller-visible raw error fields are confidential.
- Use one monotonic deadline from waiter entry. Bound every list, inspect, poll
  sleep, and chunked observation with remaining time; stop launching calls when
  none remains. Deadline or cancellation stops and reaps owned local children.
  Cleanup may take time after the deadline; no remote cancellation is implied.
- Running replicas describe Swarm task state, not application health or an
  external readiness probe. Never invoke rollback implicitly.

## Dependencies and non-goals

Depends on [bounded inventory and observation](02-stable-inventory-and-bounded-inspection.md),
[service rollout controls](05-service-rollout-and-rollback.md), and
[stack flag compatibility](06-stack-legacy-flag-compatibility.md).
This is not durable reconciliation, an application health checker, a scheduler,
or a replacement for explicit rollback decisions.

## Acceptance scenarios

1. Happy path: a fixed target with matching generation and running replicas
   yields typed success; a stack succeeds only when every target qualifies.
   Seam: fake clock and recorded service/task snapshots. Surface: call the
   waiter through a local fake CLI returning a sequence of observations.
2. Edge and failure: historical running tasks, an ambiguous template, failed
   update, unsupported mode, or changed target generation never yield success;
   each produces its documented failure or remains pending until deadline.
   Seam: model fixtures and fake clock. Surface: feed each sequence to the waiter.
3. Deadline and cancellation: a hung observation child is stopped and reaped,
   the matching typed outcome is returned, and no later poll or mutation starts.
   Seam: local blocking child and cancellation signal. Surface: invoke waiter
   and verify child exit and recorded command sequence.
4. Adjacent regression: deploy and update return as before and never start the
   waiter or rollback. Seam: wrapper mocks. Surface: invoke both against a fake
   CLI and assert no observation or rollback commands were issued.

## Future validation and decisions

Offline tests cover deadlines, stale tasks, cancellation and cleanup. Separately authorize a disposable Swarm for
rolling-update, failed-update, and stack membership tests; remove owned stacks
and services and verify teardown of any test-created Swarm state.

The operator must choose target membership, poll interval, deadline, and any
separate application readiness check; these block operational use. The maintainer
must define complete comparable task-template fields and the handling of each
supported update state; insufficient generation evidence blocks success.
