# Service rollout and rollback controls

Status: Implemented (offline validation only; live scenario blocked)
Tier: P1. Owner: SDK maintainer, with rollout policy set by the operator.

## Objective

Expose a bounded set of Swarm rollout policy options and a targeted rollback
command without implying that a submitted change is application-ready.

## Current evidence

- [`ServiceCLI.update`](../pywhales/components/service/cli_wrapper.py) forwards
  the legacy options and validated update and rollback policies. `Service.update`
  delegates to the same CLI method. `ServiceCLI.rollback` targets one service;
  there is no `Service.rollback` handle method.
- [`ServiceSpec` and `ChangeConfig`](../pywhales/components/service/models.py)
  expose update and rollback configuration in inspection results, and
  `ServiceUpdateStatus` exposes status fields.
- [`test_service.py`](../tests/pywhales/components/test_service.py) exercises
  batch inspect, service scaling, handle update, and manager errors. Its Swarm
  cases use runtime fixtures. Offline service and rollout tests passed; the
  separately gated live scenario has not been executed.

## Proposed contract

- Add keyword-only update and rollback policy options to `ServiceCLI.update`
  and the existing `Service.update` handle method: parallelism, delay,
  failure action, monitor duration, maximum failure ratio, and update order
  for each policy where supported by the selected CLI.
- Give new options `None` defaults so current calls and positional arguments
  retain their behavior. Emit values exactly once, in stable order, without
  treating zero as absent; validate types and ranges at the API boundary.
- Implement `ServiceCLI.rollback(service, *, detach=False, quiet=False)` for
  one existing `ValidService`, forwarding the chosen target and supported flags
  to the configured CLI. Do not add a new handle rollback method in this scope.
- Make target identity explicit; missing targets and CLI failures propagate as
  existing exceptions. Never retry a mutation automatically or drop flags after
  a failure. Preserve the client's diagnostics policy and confidential raw data.
- Return `None` as existing update does. A successful command means the CLI
  exited successfully under its selected detach behavior, not that application
  health or a later convergence check has succeeded.
- Service and cluster lifetime remain operator-owned. Rollback or timeout does
  not remove resources, and an interrupted local command leaves remote outcome
  potentially unknown.
- Keep direct service options separate from policies expressed by stack files.
  Document the supported flag set and duration units against the chosen CLI.

## Dependencies and non-goals

Depends on [workload and support contract](01-fleet-workload-and-support-contract.md).
This is not complete Docker CLI flag parity, an automatic rollback controller,
a deployment scheduler, or a readiness guarantee. Observation belongs to the
separate [convergence proposal](07-service-and-stack-convergence.md).

## Acceptance scenarios

1. Happy path: selected update and rollback policies yield exact argv, including
   zero values, and targeted rollback invokes one command for the named service.
   Seam: mocked CLI and argument tests. Surface: service API with a fake CLI.
2. Failure: invalid policy input fails before launch; a rollback CLI failure is
   returned to the caller with no retry or second mutation. Seam: launch spy and
   failing fake CLI. Surface: invoke the public rollback API against that child.
3. Adjacent regression: legacy update calls produce their existing argv and
   return shape, and `Service.update` forwards every newly selected update option
   just as `ServiceCLI.update` does. Seam: wrapper and static type tests. Surface:
   call both APIs with a fake CLI and compare their recorded arguments.
4. Effective policy: with separately authorized disposable Swarm, update a
   disposable service, inspect policy fields, issue targeted rollback, and
   observe the CLI result. Seam: dedicated integration test. Surface: service
   inspect before and after, with explicit cleanup; no health claim follows.

## Future validation and decisions

Run unit, type, and local fake-child tests first. A real Swarm test requires
separate authorization, an isolated manager, and verified removal of the owned
service and any test-created Swarm state.

The operator must choose acceptable rollout and rollback policy values and
qualify CLI and daemon support before a live deployment. Scenario 4 remains
unexecuted and requires separate authorization and a disposable manager.
The CLI syntax references and exact representation and ranges are documented
in the service API documentation; they do not qualify any supported version.
