# Representative capacity and failure harness

Status: Implemented (offline fake-only harness; real execution and qualification blocked)

Owner: performance maintainer and infrastructure operator
Tier: P1, representative baseline remains outstanding

## Implementation and validation

The [fleet harness](../scripts/fleet_harness/) delivers simulated reports using an in-memory fake; real providers are rejected. See [harness tests](../tests/pywhales/test_fleet_harness.py), [CLI tests](../tests/pywhales/test_fleet_harness_cli.py), [report tests](../tests/pywhales/test_fleet_report.py), and the [user guide](../docs/template/user_guide/capacity_harness.md). Real execution is BLOCKED: an independently qualified endpoint-binding and deadline adapter and separately authorized disposable infrastructure are required. Authorized qualification and a real capacity envelope remain blocked, not available through this harness.

## Objective

Design a separately authorized harness that measures a declared workload on
isolated infrastructure and reports failures as well as observed distributions.
It must not manufacture a universal container count or target.

## Original evidence

- [`benchmark_docker_overhead.py`](../scripts/benchmark_docker_overhead.py)
  substitutes subprocess results and denies real process launches. Its timing is
  simulated wrapper overhead, not a Docker throughput or capacity measurement.
- [`test_docker_overhead_benchmark.py`](../tests/pywhales/test_docker_overhead_benchmark.py)
  checks simulated request counts, report schema and rejection of invalid inputs.
- [`ClientConfig`](../pywhales/client_config.py) holds connection and stream
  settings relevant to a measured run; it doesn't specify host capacity.
- [`test_image.py`](../tests/pywhales/components/test_image.py) mixes local
  controlled-child tests with integration cases that change daemon resources.
- [`tests/AGENTS.md`](../tests/AGENTS.md) describes shared and destructive fixture
  behavior; ordinary marker selection isn't a sufficient safety boundary.

## Proposed contract

- Start baseline design before other fleet features; use the same declared
  workload and infrastructure to compare later changes without assuming success.
- An operator provides explicit opt-in, endpoint allowlist, isolated target
  evidence, workload configuration, ownership tag, stop limits and teardown plan.
- Refuse to start before endpoint, isolation, tag and limits are validated.
  Never run host CI provisioning, daemon-wide prune or shared-host cleanup.
- Record ownership of every created resource and clean up only identified owned
  resources. If cleanup fails, stop and report residual identities to the operator.
- Cover inventory inspection, lifecycle churn, image/build transfers, storage
  growth and pressure, network paths, security controls and recovery faults.
  Enable each destructive or fault scenario only with its own authorization.
- For each run record workload mix and repetitions; Python, library, Docker,
  plugin, kernel and host versions; hardware, topology, images and cache state.
- Record full latency distributions for queueing, local command and readiness,
  along with sample counts, failed and timed-out samples, and their reasons.
  Report command counts, CPU, memory/RSS, file descriptors, child high-water,
  disk bytes and inodes, network and relevant daemon resource observations.
- Define collection boundaries and timestamps; unavailable metrics remain
  explicitly unavailable rather than being recorded as zero.
- Enforce operator-defined time, resource and failure stop limits, including
  a path that ends load and attempts bounded owned-resource teardown.
- Artifacts omit credentials, raw commands and application output; use synthetic
  data and apply an explicit access and retention policy to reports.
- A simulated report remains labeled simulation. Real observations describe only
  the tested workload and environment, not a portable capacity guarantee.

## Compatibility and boundaries

This harness is a separate opt-in operational tool. It changes neither existing
SDK defaults nor the simulated benchmark's schema and interpretation.
No SLO, resource threshold or fleet size is assumed by this proposal.

## Dependencies

- [01: workload and support contract](01-fleet-workload-and-support-contract.md)
  supplies target environment, workload, authority and preregistered criteria.
  Baseline measurement can precede all other implementation work.

## Non-goals

No benchmark on a shared daemon, automatic host setup, unscoped deletion, invented
performance target, capacity certification or production load test by default.

## Acceptance scenarios

1. Happy path: a fake authorized target executes the declared scenario set and
   report contains all samples, environment metadata and teardown receipt.
   Test seam: fake workload and metrics providers. Surface: harness report file
   distinguishes measured, unavailable and failed samples.
2. Safety failure: a missing opt-in, unlisted target or missing stop limit causes
   rejection before any target connection or mutation. Test seam: recording fake
   transport. Surface: CLI reports rejection and a zero-call receipt.
3. Adjacent failure: an injected limit breach stops load and attempts cleanup of
   owned resources only; a cleanup failure appears in the report. Test seam: fake
   clock, resource ledger and failing cleanup. Surface: report retains the failed
   sample, residual resource identity and teardown status.
4. Authorized qualification: on an allowlisted disposable target, execute one
   approved representative profile and report all samples against its declared
   criteria, with measured environment metadata and owned-resource teardown.
   A failed workload criterion remains a failed result; missing authorization or
   incomplete cleanup cannot yield a qualified envelope. Seam: dedicated opt-in
   integration scenario. Surface: actual harness invocation, report and independent
   read-back of remaining owned resources. If infrastructure isn't supplied, mark
   this scenario blocked and make no claim of real capacity validation.

## Future validation and decisions

The offline harness validates fake scenarios only. An operator must authorize a
later isolated real run and its individual fault scenarios after a real adapter is qualified. The operator must choose representative workload,
topology, thresholds, sample design, privacy, acceptable results and test hosts;
until then no capacity envelope or transport comparison can be approved.
