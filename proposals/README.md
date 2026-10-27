# Fleet-readiness proposals

Status: Proposal01 is implemented as an [offline contributor contract](../scripts/fleet_contract/README.md). Proposal02 is implemented as an opt-in SDK API with offline validation. Proposal03 is implemented as an opt-in application admission layer with offline validation. Proposals04 through 11 have local implementations with offline validation; live qualification remains blocked. Proposal08 requires an application-supplied, qualified durable backend. Proposal12 delivers a fake-only harness, with real execution blocked. Proposal13 delivers an offline `no_go` decision, retaining the CLI path; a real trial is blocked. Neither implementation nor offline validation approves live operation or certifies a supported fleet size.

This series translates the fleet research in `.omo/pywhales-fleet-research/RESEARCH.md` into separate implementation units. The research and each proposal's original evidence remain historical records. The parent review reports 1,727 offline tests and 261 adjacent tests passed, with ty, Ruff, package and documentation builds passing. The full live integration suite was not run, and no live qualification is claimed.

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

01 is an implemented offline contract; 02 through 11 have implemented local behavior with offline validation. The 12 harness is fake-only, and 13 records `no_go` without real candidate launches or connections. Real execution, live qualification, version/support, authority, workload and budget decisions remain open. Dependencies refer to implementation contracts, not document-writing order. Optional layers remain opt-in.

| ID | Proposal | Responsibility | Prerequisites |
| --- | --- | --- | --- |
| 01 | [Fleet workload and support contract](01-fleet-workload-and-support-contract.md) | Maintainer/operator | None |
| 02 | [Stable inventory and bounded inspection](02-stable-inventory-and-bounded-inspection.md) | SDK | 01 |
| 03 | [Bounded endpoint admission](03-bounded-endpoint-admission.md) | Optional application/controller | 01; existing execution contracts |
| 04 | [Network IPAM, internal and IPv6 controls](04-network-ipam-internal-and-ipv6.md) | SDK; operator validates topology | 01 |
| 05 | [Service rollout and rollback](05-service-rollout-and-rollback.md) | SDK | 01 |
| 06 | [Legacy stack flag compatibility](06-stack-legacy-flag-compatibility.md) | SDK compatibility | 01 |
| 07 | [Service and stack convergence](07-service-and-stack-convergence.md) | Opt-in observation; fake CLI tested | 02, 05, 06 |
| 08 | [Durable operation reconciliation](08-durable-operation-reconciliation.md) | Optional journal and fenced reconciliation; memory fake is NONdurable | 02, 03, 07, 11 |
| 09 | [Security and resource profiles](09-security-and-resource-profiles.md) | Opt-in application policy; runtime qualification blocked | 01; existing diagnostic contracts |
| 10 | [Sanitized operational telemetry](10-sanitized-operational-telemetry.md) | Optional SDK metadata and bounded exporter | 01 |
| 11 | [Storage ownership and retention](11-storage-ownership-and-retention.md) | Protective planning, pressure and approval protocols; no deletion executor | 01, 03, 09 |
| 12 | [Representative capacity harness](12-representative-capacity-harness.md) | Offline fake-only harness; real adapter and qualification blocked | 01 |
| 13 | [Conditional async and API experiment](13-conditional-async-and-api-experiment.md) | Offline evidence-gated `no_go`; real trial blocked | 10, 12; bottleneck evidence |

## Suggested implementation order

1. Maintain the delivered offline contracts and resolve 01's operator and maintainer decisions before any real run.
2. Qualify an application-supplied durable journal backend for 08 and the selected runtime, profile and storage providers for 09 and 11 on separately authorized infrastructure.
3. Before a real baseline, independently qualify 12's missing endpoint-binding and deadline adapter and obtain authorization for disposable infrastructure and each scenario.
4. Revisit 13 only after a representative real baseline meets the materiality entry condition and a real trial is authorized. The current `no_go` requires no new backend.

The numbering groups topics; it is not a strict dependency order. In particular, 08 uses the ownership contract in 11, while 11 defines ownership and eligibility without requiring 08's journal. An offline harness report is not a real baseline.

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

These files are contributor proposals, not generated API documentation. Independent maintainer/operator review is required for any qualification assertion.
