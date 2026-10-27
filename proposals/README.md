# Fleet-readiness proposals

Status: Proposal01 is implemented as an [offline contributor contract](../scripts/fleet_contract/README.md). Proposal02 is implemented as an opt-in SDK API with offline validation. Proposal03 is implemented as an opt-in application admission layer with offline validation. Proposals04, 05 and 06 are implemented locally with offline validation only; their live scenarios are blocked and support is not qualified. Proposals07-13 remain Future. Neither implementation nor offline validation approves live operation or certifies a supported fleet size.

This series translates the fleet research in `.omo/pywhales-fleet-research/RESEARCH.md` into separate implementation units, checked against the updated source on 2026-10-27. The research remains a historical record; its earlier negative findings are not automatically true of this checkout. Source symbols and named regression tests are the baseline, not a claim that tests were executed while drafting these documents.

## Foundations already implemented

| Foundation | Current contract and evidence |
| --- | --- |
| Safe automatic diagnostics | Per-client raw opt-in; automatic diagnostics withhold arbitrary command, environment and captured-output values by default. Raw exception attributes and returned output remain confidential. See [execution](../pywhales/utils.py), [policy](../pywhales/_execution_policy.py), [automatic-diagnostic tests](../tests/pywhales/test_automatic_diagnostics.py) and [inspect-diagnostic tests](../tests/pywhales/test_inspect_diagnostics.py). |
| Selected-launch deadlines | Shared execution and selected public operations support monotonic deadlines and local-child cleanup. Queue wait, preflights and daemon-side completion are not implicitly covered. See [runner](../pywhales/utils.py), [owned streams](../pywhales/_process_stream.py) and [forwarding tests](../tests/pywhales/components/test_deadline_forwarding.py). |
| Configurable stream budgets | General and image-transfer budgets, blocking queue pressure, oversized-record errors and wake-on-close behavior exist. Defaults can remain unbounded; separate accounting limits are not a total-memory ceiling. See [buffer](../pywhales/_stream_buffer.py), [image streams](../pywhales/components/image/streams.py) and [runtime contracts](../pywhales/AGENTS.md). |
| Validated plural inspection | Task, context and plugin plural inspection now validate complete responses before hydration. Existing ordering, duplicate and alias rules must be preserved. See [validation](../pywhales/_inspect_validation.py) and [component contracts](../pywhales/components/AGENTS.md). |
| Staged grouped refresh | `bulk_reload()` groups by exact configuration identity and resource kind, then stages all groups before publishing. The historical unhashable-config failure is superseded. Publication is sequential, not a daemon transaction or atomic visibility to concurrent readers. See [implementation](../pywhales/_bulk_reload.py) and [tests](../tests/pywhales/test_bulk_reload.py). |
| Cleanup ownership | Owned streams and resource cleanup already preserve lifecycle and exception contracts. Local CLI cancellation does not roll back remote mutations; resource context-manager teardown is not ownership discovery. See [streams](../pywhales/_process_stream.py) and [resource cleanup](../pywhales/components/_cleanup.py). |

These foundations are dependencies to preserve, not features to implement again. Proposal02 adds explicit captured-response limits, stable inventory and scoped synchronous observation deadlines. Proposal03 adds explicit application admission bounds; convergence workflows remain separate contracts.

## Proposal index

01 has status **Implemented (offline contract only)**, 02 has status **Implemented (opt-in SDK API; offline validation only)**, 03 has status **Implemented (opt-in application layer; offline validation only)**, and 04, 05 and 06 have status **Implemented (offline validation only; live blocked)**; 07 through 13 remain **Future**. Real version/support, authority, workload and budget decisions remain open. Dependencies refer to implementation contracts, not document-writing order. An optional layer stays opt-in even after its proposal is implemented.

| ID | Proposal | Responsibility | Prerequisites |
| --- | --- | --- | --- |
| 01 | [Fleet workload and support contract](01-fleet-workload-and-support-contract.md) | Maintainer/operator | None |
| 02 | [Stable inventory and bounded inspection](02-stable-inventory-and-bounded-inspection.md) | SDK | 01 |
| 03 | [Bounded endpoint admission](03-bounded-endpoint-admission.md) | Optional application/controller | 01; existing execution contracts |
| 04 | [Network IPAM, internal and IPv6 controls](04-network-ipam-internal-and-ipv6.md) | SDK; operator validates topology | 01 |
| 05 | [Service rollout and rollback](05-service-rollout-and-rollback.md) | SDK | 01 |
| 06 | [Legacy stack flag compatibility](06-stack-legacy-flag-compatibility.md) | SDK compatibility | 01 |
| 07 | [Service and stack convergence](07-service-and-stack-convergence.md) | Opt-in SDK observation workflow | 02, 05, 06 |
| 08 | [Durable operation reconciliation](08-durable-operation-reconciliation.md) | Optional application/controller | 02, 03, 07, 11 |
| 09 | [Security and resource profiles](09-security-and-resource-profiles.md) | Operator policy; opt-in application validation | 01; existing diagnostic contracts |
| 10 | [Sanitized operational telemetry](10-sanitized-operational-telemetry.md) | SDK metadata; optional exporters/controller | 01 |
| 11 | [Storage ownership and retention](11-storage-ownership-and-retention.md) | Operator; optional application/controller | 01, 03, 09 |
| 12 | [Representative capacity harness](12-representative-capacity-harness.md) | Maintainer/operator test infrastructure | 01 |
| 13 | [Conditional async and API experiment](13-conditional-async-and-api-experiment.md) | Measured experiment | 10, 12; bottleneck evidence |

## Suggested implementation order

1. Maintain and review 01's implemented offline version, workload, ownership and validation contracts. Resolve its owned operator/maintainer decisions before a real run rather than choosing arbitrary SLOs.
2. Start 12 early to collect an authorized baseline. It does not require every other proposal; qualification scenarios are enabled as their feature contracts become available.
3. Maintain 02, 03, 04, 05 and 06 and implement 09 and 10 as independent increments once their shared interfaces are agreed.
4. Implement 07 after 02/05/06, and 11 after 03/09. Then implement optional 08 using those established contracts.
5. Execute 13 only if the measured entry condition holds. A documented no-go completes the experiment; a persistent API backend or async rewrite is not an unconditional deliverable.

The numbering groups topics; it is not a strict dependency order. In particular, 08 requires the ownership contract in 11, and 12 should begin before final feature qualification. There is no dependency cycle: 11 defines ownership and eligibility without requiring 08's durable journal.

## Scope boundaries

PyWhales remains a typed Docker CLI wrapper. Preserve direct low-level calls, originating configuration, resource identity, return types, safe diagnostics and existing cleanup semantics unless a proposal explicitly identifies a reviewed compatibility change.

Optional application/controller proposals coordinate admission, policy and observed outcomes. They do not replace Swarm or Kubernetes placement, promise exactly-once Docker mutations, implement a firewall dataplane, or turn labels into an authorization boundary. The selected host/runtime owns effective isolation, routing and quotas.

No proposal permits automatic mutation retries after an ambiguous timeout, silent option dropping, runtime feature-probe fallbacks, broad pruning of shared resources, or production load testing without explicit authorization. No universal container count or throughput improvement has been established.

## Acceptance for each increment

- Re-read the proposal's source and tests before implementation; the checkout may have changed again.
- Resolve the decisions that block that increment, while leaving unrelated operator decisions open.
- Preserve named regressions and add behavior tests at the repository's existing seam, not tests that pin proposal text.
- Prove the happy path, boundary/failure path and adjacent regression on the named surface. Mocks do not prove host isolation, cross-host networking or real capacity.
- Run relevant diagnostics, type contracts and tests. Run Docker integration scenarios only on authorized disposable infrastructure, with positive resource ownership and teardown receipts.
- Update the proposal status and this index only after its own criteria pass. Report unexecuted infrastructure scenarios explicitly.

These files are contributor proposals, not generated API documentation. Proposal01's offline contract adds contributor tooling only; proposal02 adds opt-in SDK behavior without a new dependency or live Docker workload. Independent maintainer/operator review is required for any qualification assertion.
