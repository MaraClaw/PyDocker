# Bounded endpoint admission

Status: Future (not approved or implemented)

Owner: application controller maintainer and operator
Tier: P1

## Objective

Bound application-submitted work for each explicitly identified endpoint and
pressure class, including pending requests and work already in progress.
Preserve the existing PyWhales API for callers that don't opt in.

## Current evidence

- [`ClientConfig`](../pywhales/client_config.py) carries host and context settings,
  but those settings aren't an application admission identity.
- [`MultiImageStream`](../pywhales/components/image/streams.py) starts at most
  four workers per group, yet submits a future for every input at startup.
- [`ContainerCLI.create`](../pywhales/components/container/cli_wrapper.py)
  can acquire an image before launching the create command.
- [`run`](../pywhales/utils.py) measures selected local command deadlines from
  launch; it doesn't apply a deadline to earlier queueing or preflight.
- [`test_image.py`](../tests/pywhales/components/test_image.py) provides
  controlled child streams for lifecycle and cleanup tests.

## Proposed contract

- A controller explicitly constructs an admission owner with an operator-supplied
  endpoint identity, class limits, a pending bound and a saturation policy.
- Identities must distinguish endpoints the operator intends to isolate; aliases
  for the same daemon must share an identity when aggregate limits are required.
- Classes cover short control calls, transfers/builds and long-lived streams.
  Limits and the mapping of operations to classes are explicit configuration.
- Admission accepts a bounded iterable or producer and never creates one queued
  future per item beyond the configured pending allowance.
- At saturation, the configured policy either waits or returns a distinct
  admission rejection. Optional queue deadlines have their own error and clock.
- Acquire the outer operation permit before image pull or other preflight side
  effects. A cancelled pending request starts no CLI, preflight or mutation.
- A stream permit belongs to the returned owned stream until exhaustion, close,
  failure or completed cancellation cleanup. A bare loop break isn't a close.
- Use one permit for an entire composite operation, or explicitly reserve child
  capacity before entering it. Nested group work must never hold the last permit
  while waiting to acquire that same permit from its own pool.
- The selected launch timeout continues to start at local launch; queue time is
  not silently deducted. Local cancellation doesn't promise remote rollback.
- Release permits exactly once on exceptions and cancellation, including errors
  raised during stream setup and cleanup. Keep the original operation error.
- The admission owner exposes bounded counts and rejection outcomes without
  recording endpoint credentials, command arguments or output payloads.

## Compatibility and boundaries

This is an opt-in application layer, not an automatic SDK-wide limit.
Limits apply only to work routed through the same owner in one process.
Multiple controller processes need explicit external coordination or allocated
shares before an aggregate claim is made; this proposal supplies neither.
Underlying return shapes, diagnostic policy and existing stream ownership stay
unchanged for direct SDK callers.

## Dependencies

- [01: workload and support contract](01-fleet-workload-and-support-contract.md)
  supplies endpoint identity, pressure classes and operator budgets.

## Non-goals

No distributed scheduler, implicit global semaphore, daemon transaction,
remote cancellation guarantee or change to the low-level image API is proposed.

## Acceptance scenarios

1. Happy path: for configured limits, a controlled producer reports pending and
   active high-water marks at or below their bounds and all accepted work ends.
   Test seam: fake operation and bounded producer. Surface: application driver
   prints the counts and results without connecting to a daemon.
2. Saturation and cancellation: cancel a pending create with pull preflight;
   it performs zero preflight and launches, and reports cancellation distinctly.
   Test seam: blocked permit and spies. Surface: driver records zero calls.
3. Adjacent lifecycle: with a one-permit pool, composite streamed work completes
   without deadlock; close and injected child failure each release the permit.
   Test seam: controlled child streams. Surface: a local-child driver observes
   process cleanup and successfully admits the next operation.

## Future validation and decisions

Start with controlled producers and local child processes; only later use an
explicitly authorized disposable daemon. No live validation is performed here.
Operators must choose endpoint aliasing, limits, fairness, wait/reject policy,
queue deadlines and cross-process ownership. Until chosen, no deployment-specific
limits or aggregate safety claim can be approved.
