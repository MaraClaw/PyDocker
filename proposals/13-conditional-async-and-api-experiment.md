# Conditional async and Engine API experiment

Status: Future (not approved or implemented)

Owner: SDK maintainer and performance operator
Tier: P2, conditional on measurement

## Objective

Decide whether a narrowly scoped asynchronous CLI facade or persistent Engine
API prototype merits further work, using comparable measured operations.
A documented no-go is a valid final outcome.

## Current evidence

- [`run`](../pywhales/utils.py) executes local CLI commands synchronously and
  supports selected launch-based deadlines and local process cleanup.
- [`ProcessStream`](../pywhales/_process_stream.py) owns streaming process
  lifecycle; closing the local process doesn't guarantee remote cancellation.
- [`SystemCLI.events`](../pywhales/components/system/cli_wrapper.py) returns an
  owned mapped stream, so a comparison must include subscription behavior.
- [`test_utils.py`](../tests/pywhales/test_utils.py) covers local process,
  timeout and stream cleanup semantics.
- [`benchmark_docker_overhead.py`](../scripts/benchmark_docker_overhead.py)
  explicitly measures simulated wrapper overhead, not Docker capacity.

## Proposed decision and experiment contract

- Enter the experiment only when 12 provides representative baseline evidence
  that transport overhead is material against operator-approved objectives.
  Use the sanitized measurements and clock definitions from 10.
- First compare the existing CLI path with batching and connection reuse where
  applicable. Specify the selected small operation set before implementation.
- A prototype may evaluate an asynchronous CLI facade, a narrow persistent
  Engine API path, or both. It must remain opt-in and isolated from production.
- Compare equivalent operations, inputs, result shapes and workloads on the
  same approved infrastructure, including cache state and failed samples.
- Document authentication, endpoint verification, supported API versions,
  errors and permission behavior for each candidate before comparison.
- Compare stream ordering, backpressure, byte accounting, ownership, early
  close, cancellation and timeout semantics with their CLI counterparts.
- A lost mutation response remains an ambiguous remote outcome on either path;
  cancellation of local work doesn't imply daemon rollback or safe retry.
- Report queue, local and readiness durations separately, with distributions,
  resource usage and confidence limits appropriate to the sampled workload.
- Reject candidates that fail required correctness, confidentiality, supported
  version or failure semantics, even if their timings improve.
- Publish a decision record containing scope, raw-safe artifacts, comparisons,
  observed limitations and go/no-go rationale. A no-go requires no new backend.
- Preserve the existing CLI path and its Compose and Buildx behavior. An Engine
  API experiment doesn't imply that those CLI-native surfaces are replaced.

## Compatibility and boundaries

No default changes, unconditional rewrite or production backend promise follows
from this experiment. Operators own test authorization and resource teardown;
the maintainer owns equivalence criteria and records unavailable measurements.
Never export credentials, endpoint secrets, argv or workload payloads as metrics.

## Dependencies

- [10: sanitized telemetry](10-sanitized-operational-telemetry.md) defines
  measurement boundaries and confidentiality.
- [12: representative capacity harness](12-representative-capacity-harness.md)
  supplies the authorized baseline, workload and comparison surface.

## Non-goals

No replacement for Compose, Buildx or the CLI, no scheduler, no remote rollback
guarantee and no supported persistent API implementation are promised.

## Acceptance scenarios

1. Happy path: a representative baseline meets the operator's entry criterion;
   equivalent candidate runs produce a decision record with distributions and
   failures. Test seam: fake benchmark result adapter. Surface: report viewer
   shows both contracts, measurements and an explicit go or no-go.
2. No-go: absent evidence or unmet entry criteria produce a documented no-go
   without starting the prototype or contacting an endpoint. Test seam: fake
   decision inputs and recording transport. Surface: decision report states why
   and records zero launches or connections.
3. Adjacent regression: injected stream cancellation or ambiguous response is
   compared against the CLI contract, and any semantic mismatch fails the trial.
   Test seam: controlled child and fake API transport. Surface: comparison report
   preserves the mismatch and confirms the existing CLI route remains selected.
4. Conditional measured trial: if entry criteria hold and infrastructure is
   authorized, compare the chosen candidate and CLI on that same real workload.
   The report binds every result to the environment, includes failed samples and
   contract checks, and records teardown. Seam: 12's opt-in comparison scenario.
   Surface: actual candidate/CLI invocations and measured decision artifacts.
   Without this evidence, only a no-go or blocked trial is valid; simulated
   equivalence cannot justify production adoption.

## Future validation and decisions

Use simulated contract comparisons before any authorized isolated endpoint run.
This proposal performs no live experiment. Operators must define the workload,
materiality threshold, acceptable regression budget, versions and endpoint access;
those choices block the go/no-go decision and any production adoption request.
