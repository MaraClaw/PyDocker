# 01: Fleet workload and support contract

Status: Implemented as an offline contributor contract. Real support approval, operator authorization and live capacity qualification remain unresolved; no supported fleet envelope is asserted.

Owner: Maintainer and deployment operator. Tier: workload, support and validation contract; no scheduler or runtime change.

## Objective

Give every later proposal a named operating context and measurable success definition. Produce a reusable workload manifest, a supported-version matrix and a validation authorization record before claiming that PyWhales supports a particular fleet size or performance target.

## Current baseline

- [DockerClient](../pywhales/docker_client.py) wires command groups to one originating [ClientConfig](../pywhales/client_config.py). Client construction does not establish daemon availability, network isolation or capacity.
- [Shared runtime contracts](../pywhales/AGENTS.md) define optional selected-launch deadlines, safe/raw diagnostics and separate stream accounting limits. None is an application-readiness SLO or a total process-memory guarantee.
- [The current benchmark](../scripts/benchmark_docker_overhead.py) measures simulated wrapper overhead without Docker execution. [Its tests](../tests/pywhales/test_docker_overhead_benchmark.py) protect that distinction.
- [The existing performance guide](../docs/template/user_guide/build_performance.md) already covers concurrency, resource/log settings and build-cache controls. The proposal must use these controls rather than label them missing.
- [Client tests](../tests/pywhales/test_docker_client.py), [configuration tests](../tests/pywhales/test_client_config.py), [CI](../.github/workflows/python-package.yml) and [test safety guidance](../tests/AGENTS.md) are the baseline for later changes. Their presence is not evidence that a fleet load test has passed.

The outstanding decisions in `.omo/pywhales-fleet-research/RESEARCH.md` remain operator inputs, not defaults the SDK can infer.

## Contract deliverables

1. A versioned, machine-readable workload manifest and documented schema, shared with [12's harness](12-representative-capacity-harness.md). Validate it using existing project tooling; do not add a production SDK dependency solely to parse benchmark configuration.
2. A maintainer-owned support matrix naming Python, OS/kernel, Docker CLI/Engine and applicable Compose/Buildx versions, selected orchestrator and required features. Distinguish supported, experimental and untested combinations. Do not infer support from successful option parsing.
3. An operator authorization record identifying disposable endpoints, ownership namespace, test operator, resource/stop limits and approved destructive scenarios. No credentials belong in the manifest or published report.
4. A decision register with an owner, rationale and blocking effect for every unresolved field. Draft manifests may record unresolved values; a runnable or qualifying profile may not leave a required field unresolved.

Contributor-facing contracts remain outside generated documentation output. If implementation publishes user-guide pages, wire editable templates, page generation and navigation together under the [documentation guide](../docs/AGENTS.md).

## Manifest contract

| Group | Required information |
| --- | --- |
| Identity and provenance | Manifest/schema version, profile name, revision, decision owners, immutable workload/image identifiers and permitted artifact destinations. |
| Workload | Resident resources, arrival/churn distribution, operation mix, concurrent submissions, image/cache states, image sizes, output/log distributions and duration/termination rules. Counts must distinguish containers, service replicas and in-flight operations. |
| Platform | Effective endpoint inventory and alias mapping, host resources, storage drivers/filesystems, registry path, local/SSH/TLS access, node count and orchestrator/mode. Endpoint labels in public results must be safe aliases. |
| Topology and trust | Address pools, bridge/overlay choice, IPv4/IPv6, MTU/DNS/ingress expectations, trusted operators versus tenants, allowed mounts and security enforcement owner. |
| Ownership and retention | Created versus borrowed assets, persistent versus ephemeral data, protected rollback images, log/cache retention, backup/restore obligations and teardown authority. |
| Measurements | Queue wait, selected CLI execution, end-to-end readiness and recovery measured separately; units, sample population, failure/rejection denominators, latency distributions and resource high-water marks. |
| Pass/fail and safety | Operator-supplied latency/error/recovery/resource budgets, polling/stability criteria, sustained-run duration and early-stop limits. Record rejected, cancelled, failed and unknown outcomes rather than dropping them from samples. |
| Authorization | Explicit disposable target allowlist, allowed actions, test identity, run ownership marker and cleanup verification surface. |

Use explicit units and finite values where required. A profile must reject contradictory modes, impossible bounds and missing required inputs before any external action. An unresolved capacity target is valid in a draft, but cannot be presented as a qualified operating envelope.

## Compatibility and ownership

This contract changes neither `DockerClient` construction nor ordinary CLI forwarding. Do not introduce startup probes, choose an orchestrator automatically, mutate daemon settings or invent hardware-independent concurrency defaults.

Existing selected-launch timeout semantics remain unchanged. A later controller's queue or workflow deadline must be named separately. Stream budgets remain optional independent bounds; selecting positive profile budgets does not silently change library defaults.

Support decisions for [06's legacy stack flag](06-stack-legacy-flag-compatibility.md) must specify a compatibility window and a removal decision. No live fallback may silently omit an unsupported option. Effective endpoint aliases need an explicit operator mapping before [03's shared admission](03-bounded-endpoint-admission.md) can promise aggregate limits.

## Dependencies and non-goals

No future proposal is a prerequisite. Reuse the implemented runtime foundations listed in the [index](../proposals/README.md).

Do not implement a scheduler, universal benchmark workload, authentication vault, production deployment, network controller or fleet-size promise in this increment. Do not require every possible support-matrix combination to be qualified before a bounded first profile can be tested.

## Acceptance scenarios

| Scenario | Binary pass condition | Test seam and real validation surface |
| --- | --- | --- |
| Complete profile | A populated profile validates; its serialized form preserves units, workload identity and requested measurements; required fields have owners and values. | New schema/manifest validation tests beside the future harness; run the validator offline on a representative fixture and inspect its normalized output. No Docker command is needed. |
| Unsafe or unresolved runnable input | Missing authorization, unknown endpoint alias, contradictory mode, invalid numeric limit or unresolved required run budget prevents execution before external calls. Draft-only unresolved fields remain visible with their blockers. | Table-driven manifest tests with a subprocess/endpoint spy that fails on contact; invoke offline validation and verify nonzero rejection with safe field-level errors. |
| Adjacent compatibility | Importing or constructing a client and existing simulated benchmark execution retain their current behavior; no availability probe or daemon mutation is introduced. | Preserve client/configuration tests and benchmark contract tests; run the simulated benchmark and verify it still reports no Docker execution. |
| Honest qualification | A result missing required measured evidence is marked unqualified, not passed; excluded failures and unresolved support combinations are visible. | Report-validation fixtures shared with 12; inspect a deliberately incomplete result through the offline reporting surface. |

## Validation and implementation boundary

Read the current tests before adding manifest/report cases. Run offline validation and existing relevant regression tests first. This increment needs no live Docker resource, host provisioning or destructive fixture. Later live qualification belongs to 12, with its own authorization and cleanup receipts.

Completion means the contract, schema, representative draft/runnable fixtures and decision register agree. It does not mean the listed SLOs have been met. The offline implementation was exercised and its focused tests run; no live infrastructure scenario was executed.

## Implemented offline surface

The contributor-only [contract modules and guide](../scripts/fleet_contract/README.md) provide strict finite, extra-forbid Pydantic models, schema export, normalized JSON validation and offline completeness reports. Run `uv run --offline --locked python -m scripts.fleet_contract --help` from the checkout. The guide documents commands, all typed groups and authoritative runtime invariants that JSON Schema does not encode.

The [owned draft decisions](../scripts/fleet_contract/fixtures/draft.json), [synthetic runnable example](../scripts/fleet_contract/fixtures/synthetic-runnable.json), [maintained support matrix](../scripts/fleet_contract/fixtures/support-matrix.json), [synthetic result](../scripts/fleet_contract/fixtures/synthetic-result.json) and [incomplete result](../scripts/fleet_contract/fixtures/incomplete-result.json) are reusable offline fixtures. All matrix rows are untested; configured CI and fictional version combinations are not passing support evidence. Proposal06's compatibility window and removal decision remain explicit maintainer-owned blockers.

Reports distinguish `schema_valid` and `evidence_complete` from qualification. Every offline report emits `qualification="unqualified"` and requires independent maintainer/operator review. Even complete self-declared evidence, passing fictional criteria or an approval-reference string cannot authenticate real authority, establish capacity or grant qualification. Missing/excluded observations and all failed, rejected, cancelled, unknown and timed-out outcome denominators remain visible. A runnable synthetic manifest is a fully specified example, not authorization.

[Manifest tests](../tests/pywhales/test_fleet_contract.py), [report tests](../tests/pywhales/test_fleet_report.py) and [real offline CLI tests](../tests/pywhales/test_fleet_cli.py) cover complete round trips, owned draft blockers, safe rejection before subprocess/socket contact, honest completeness and unchanged adjacent client/configuration/simulated-benchmark behavior. The focused offline pytest run passed 202 tests with five Docker cases deselected; Ruff and ty checks include the contributor scripts explicitly. Full destructive Docker tests and proposal12's live harness remain unexecuted and out of scope.

## Decisions before implementation or qualification

- Maintainer: choose the initial supported version/feature combinations. This blocks version-specific wrapper acceptance, not writing the generic schema.
- Operator: choose workload distributions, topology, trust model, readiness definition and SLO/resource budgets. This blocks a runnable qualification profile and any capacity claim.
- Operator: designate disposable targets, test authority, ownership and protected data. This blocks all live tests, including cleanup scenarios.
- Maintainer and operator: choose which optional controller policies will be deployed. This blocks qualification of those policies, not independent low-level SDK proposals.
