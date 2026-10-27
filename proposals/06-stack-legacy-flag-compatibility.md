# Stack legacy flag compatibility

Status: Future (not approved or implemented)
Tier: P1. Owner: SDK maintainer and release maintainer.

## Objective

Deprecate the legacy stack `orchestrator` argument without changing its
forwarding during a documented compatibility window.

## Current evidence

- [`StackCLI.deploy`](../pywhales/components/stack/cli_wrapper.py) accepts
  `orchestrator: str | None`, passes it to `Command.add_simple_arg`, and
  returns a named `Stack` after the CLI exits.
- [`Command.add_simple_arg`](../pywhales/client_config.py) omits only `None`;
  other values are retained. No deprecation warning is emitted in `deploy`.
- The deploy docstring says the returned handle does not guarantee service task
  convergence. `Stack` delegates `ps`, `services`, and `remove` to its client.
- [`test_stack.py`](../tests/pywhales/components/test_stack.py) contains stack
  discovery, interpolation, and manager-error cases using runtime fixtures.
  These tests were read, not run; they do not establish legacy flag support on
  the selected CLI.
- The historical research in `.omo/pywhales-fleet-research/RESEARCH.md`
  recorded a help/reference mismatch for one CLI version, not a tested deploy
  failure or proof about all supported versions.

## Proposed contract

- When `orchestrator` is not `None`, emit one `DeprecationWarning` at the
  caller's location before invoking the CLI, and forward the value unchanged as
  `--orchestrator` during the compatibility window.
- `None`, including explicit `None`, emits no warning and omits the flag.
  An empty string is non-`None`: warn and forward it exactly as today.
- Preserve positional arguments, remaining options, environment interpolation,
  return type, command output behavior, and originating client config.
- If the selected CLI rejects the forwarded flag, propagate its ordinary error.
  Never probe version support, retry without the flag, silently drop it, or
  switch to a different backend.
- The warning must not include the supplied value or other command data.
  Existing safe diagnostic defaults and explicit raw opt-in remain unchanged.
- Release notes and API documentation should name the compatibility window and
  advise callers to omit the argument. Actual removal is a separate future
  change in the release selected by the maintainer.
- Deployment still owns daemon-side mutations; warning, failure, or local
  interruption does not undo those mutations or establish convergence.

## Dependencies and non-goals

Depends on [workload and support contract](01-fleet-workload-and-support-contract.md),
especially its selected CLI versions. This work does not remove the parameter,
add a Kubernetes backend, alter stack discovery, or create a convergence waiter.

## Acceptance scenarios

1. Happy path: a non-`None` legacy value raises one caller-visible deprecation
   warning and is forwarded unchanged in exactly one deploy invocation.
   Seam: warning capture and mocked `run`. Surface: `stack.deploy` with a fake CLI.
2. Failure: a fake CLI rejects the flag; the original error reaches the caller
   and no probe or fallback command occurs. Seam: failing child and launch spy.
   Surface: deploy through that fake executable.
3. Adjacent regression: omitted and explicit `None` produce no warning and no
   legacy flag; empty string warns and is forwarded. Existing interpolation,
   return shape, and stack options are unchanged. Seam: argument and env tests.
   Surface: compare public deploy calls through the configured fake CLI.
4. Selected-version check: with separate authorization, deploy a disposable
   stack using the supported non-legacy path and capture CLI outcome and
   returned handle. Seam: integration test. Surface: disposable Swarm manager,
   followed by stack removal and teardown verification.

## Future validation and decisions

Run mocked warning, argument, and local fake-child tests first. Separately
authorize a disposable Swarm before any deploy; record versions and remove all
test-owned stack resources and any test-created Swarm state.

The release maintainer must choose the warning window and removal release;
this blocks publishing a removal date or removing the parameter. The operator
must identify supported CLI versions; this blocks a compatibility claim.
