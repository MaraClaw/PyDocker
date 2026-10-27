# Network IPAM, internal mode, and IPv6

Status: Implemented (offline validation only; live scenario BLOCKED; support not qualified)
Tier: P1. Owner: SDK maintainer and network operator.

## Objective

Expose selected Docker network creation options for internal networks, IPv6,
and multiple IPAM pools while preserving existing calls and their defaults.

## Current evidence

The creation API is exercised by local Python fake CLI children in
[`test_network_create.py`](../tests/pywhales/components/test_network_create.py)
and static contracts in [`test_network_types.py`](../tests/pywhales/test_network_types.py).
No live daemon or topology has been tested. CLI v28.0.0 is a reference for
[containment association and unordered inspect configuration](https://github.com/docker/cli/blob/f9ced58158d5e0b358052432244b483774a1983d/cli/command/network/create.go#L144-L265)
and [first-equals mapping parsing](https://github.com/docker/cli/blob/f9ced58158d5e0b358052432244b483774a1983d/opts/opts.go#L158-L170),
not a support qualification. Only argv order is promised.

- [`NetworkCLI.create`](../pywhales/components/network/cli_wrapper.py) retains
  singular `subnet` and `gateway` and adds keyword-only IPAM pool controls.
  It returns a handle using the ID from the create command.
- The same wrapper exposes `Network.internal`, `enable_ipv6`, and `ipam`
  inspection properties. [`NetworkIPAM`](../pywhales/components/network/models.py)
  contains a driver, configuration list, and options.
- [`Command`](../pywhales/client_config.py) omits `None` for value options,
  retains zero and false values, and emits valueless flags only when true.
- [`test_network.py`](../tests/pywhales/components/test_network.py) includes
  create/remove, labels, a subnet with a fixed IPv4 address, and batch inspect
  tests. The Docker-dependent cases were not run.

## Proposed contract

- Add keyword-only `internal` and `enable_ipv6` options with `None` defaults,
  plus IPAM driver, IPAM options, and ordered pool definitions.
- Represent each pool as a typed value containing a required subnet and optional
  gateway, IP range, and auxiliary address mapping. Emit each supported Docker
  option in pool order using the selected CLI's documented association rules.
- Preserve the existing positional parameters and exact argv when all new
  options are omitted. Existing singular subnet and gateway remain supported.
- Reject combining singular subnet or gateway with the new pool collection
  before launch; avoid silently choosing one source or merging unrelated pools.
- Distinguish `None` from explicit `False` and zero for all new values. Emit
  explicit false only where the selected CLI supports a boolean value, using
  its defined syntax; otherwise reject it rather than silently changing intent.
  Do not drop zero-valued inputs merely because they are falsey.
- Reject malformed pool structure and ambiguous mappings before launch.
  Leave Docker to report unsupported drivers, invalid CIDRs, conflicts, and
  options rejected by the selected CLI or daemon; preserve that error.
- Do not probe CLI versions or retry with flags removed. The originating client
  config and current safe diagnostic policy remain in effect.
- Return the existing network handle type. Creation changes daemon state;
  caller-controlled removal and context-manager behavior remain unchanged.

## Dependencies and non-goals

Depends on [workload and support contract](01-fleet-workload-and-support-contract.md),
including the selected CLI and Engine support matrix. This proposal does not
implement a dataplane, network policy enforcement, host IPv6 configuration,
address allocation across environments, or cross-host reachability guarantees.

## Acceptance scenarios

1. Happy path: two ordered pools, internal mode, and IPv6 produce the exact
   selected CLI argv and the normal network handle. Seam: mocked `run` and
   typed pool tests. Surface: call `network.create` against a fake CLI.
2. Failure: mixing singular and multi-pool settings fails before launch, and a
   CLI rejection propagates without a fallback invocation. Seam: launch spy and
   failing fake response. Surface: call the public API with both invalid forms.
3. Adjacent regression: legacy positional calls retain argv and return type;
   `None`, explicit `False`, and zero follow the documented selected-CLI
   representation or rejection. Seam: argv and static type tests. Surface:
   compare calls through the configured fake CLI.
4. **BLOCKED** Effective configuration: on separately authorized infrastructure, inspect a
   created network and compare internal, IPv6, driver, and pool fields with the
   request. Seam: disposable integration test. Surface: Docker inspect on the
   created network, followed by verified removal.

## Future validation and decisions

Start with mocked argument, validation, and type tests. Use only local fake
children for process checks. Separately authorize a disposable daemon before
creating networks; record selected versions and remove every owned network.
Multi-host packet-path testing requires separate infrastructure authorization.

The network operator must select address pools, desired driver, IPv6 and
internal behavior; this blocks an effective deployment profile. The maintainer
must settle supported CLI versions before qualification.
