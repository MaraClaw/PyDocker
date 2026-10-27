# Security and resource profiles

Status: Future (not approved or implemented)

Owner: application platform security maintainer and operator
Tier: P1

## Objective

Offer an opt-in application policy that validates deployment requests before
side effects and translates an approved profile into existing container options.

## Current evidence

- [`ContainerCLI.create`](../pywhales/components/container/cli_wrapper.py) exposes
  image, user, capability, mount, network, CPU, memory, PID and logging options.
- The same create path can pull an image before the create CLI runs, so policy
  validation must precede the whole call.
- [`ClientConfig`](../pywhales/client_config.py) contains endpoint and TLS
  settings; setting them alone doesn't define an authorization policy.
- [`test_container.py`](../tests/pywhales/components/test_container.py) includes
  command, lifecycle and integration cases; its fixtures require careful selection.
- [`test_logging.py`](../tests/pywhales/test_logging.py) distinguishes safe and
  explicitly raw diagnostics while preserving raw returned data.

## Proposed contract

- An application explicitly selects a named, versioned profile and validates a
  complete request before pull, inspect preflight or any other side effect.
- Define approved endpoint identities, registries and image digest requirements;
  reject an unknown endpoint, mutable reference or disallowed registry according
  to the selected profile rather than silently substituting an image.
- Define allowed runtime user, capabilities, privilege, read-only filesystem,
  security options, mounts and network attachment choices.
- Define CPU, memory and PID ceilings and required log driver and retention
  settings. Specify whether log backpressure or loss is acceptable; audit logs
  need an explicit loss policy rather than an implied guarantee.
- Validate missing, conflicting and unsupported options with a typed policy error
  before executing any Docker command. Return a field and policy reason without
  returning secret values or the full request.
- Translate only validated options into the current low-level API; inspect the
  effective runtime configuration during separately authorized validation.
- Rootless operation is allowed only where the selected runtime, workloads and
  networking support the profile. Unsupported required controls reject the request.
- Profiles store secret references, never plaintext secret values; request data,
  logs, errors and persisted artifacts follow an explicit confidentiality policy.
- The operator owns profile distribution, version approval and endpoint access.
  Policy may be bypassed by an actor with direct daemon access; daemon authority,
  host isolation and authorization must be enforced outside this application.

## Compatibility and boundaries

Profile enforcement is opt-in and scoped to application calls routed through it.
Direct low-level SDK calls stay permissive and retain their existing defaults.
No default registry, resource limit, logging driver or isolation strength is
asserted without the operator's workload contract.

## Dependencies

- [01: workload and support contract](01-fleet-workload-and-support-contract.md)
  supplies the trust boundary, supported runtime matrix and resource objectives.

## Non-goals

This doesn't constrain direct Docker daemon control, certify tenant isolation,
manage secrets as a vault, configure the daemon, or promise rootless parity.

## Acceptance scenarios

1. Happy path: a request matching a selected profile yields the expected existing
   SDK arguments. Test seam: policy validator and recording fake client. Surface:
   application driver displays the approved, sanitized plan.
2. Rejection: a prohibited mount, digest or endpoint produces a policy error and
   zero preflight, pull and create calls. Test seam: recording fake client.
   Surface: driver reports the rejected field without secret values.
3. Adjacent behavior: direct SDK invocation retains its old argument forwarding;
   unsupported rootless controls fail the opted-in policy before side effects.
   Test seam: mocked CLI and capability matrix. Surface: driver demonstrates
   independent direct and profile-controlled calls without a daemon.

## Future validation and decisions

Use synthetic policy tests first. On an authorized disposable runtime, compare
the effective identity, limits, mounts, network and log configuration with the
approved profile, including rootless only if selected. No such run occurs here.
Operators must choose threat model, allowed images and endpoints, resource and
retention values, audit loss policy and supported platforms. Those decisions
block a deployable profile and any isolation claim.
