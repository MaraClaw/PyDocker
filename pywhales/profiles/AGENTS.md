# Create Profiles

## Local Navigation

- `__init__.py` is the export list. `_binding.py`: `EndpointBinding`. `_contracts.py`: closed inputs,
  `PolicyError`, `PolicyReason`, `CONTROLS`. `_policy.py`: `SecurityProfile`, `plan_create`,
  `CreatePlan`, `PlanSummary`. `_create.py`: `create_from_plan`.
- User-facing prose lives in `docs/template/user_guide/profiles.md`; edit the template, not generated output.
- `DockerClient.create` stays permissive. Profiles apply only to requests that go through `plan_create`.

## Endpoint Binding

- A binding approves one exact `ClientConfig` object plus a deep copy of its settings taken at construction.
  Match with `is`, never `==`: an equal but distinct config is `NOT_APPROVED`. The client's
  `container.client_config` must be the same object too.
- `validate()` compares the live config to the snapshot and fails with `CONFLICT` on field `endpoint.binding`.
  It sees Python-side edits (including the `environment` overlay), not file, ambient environment, or remote
  endpoint changes. It is an observational check, not a concurrency fence; callers serialize config changes.
- The snapshot copy keeps `telemetry_observer` by reference. Don't make the copy duplicate it.
- `_prepare()` is the only state transition: after full validation it stores the resolved executable prefix
  once, only when the snapshot has none. `plan_create` never resolves the executable. A later prefix change rejects.

## Closed Inputs

- `CreateRequest` is the whole supported surface. Don't add `**kwargs`, environment or env-file values, devices,
  raw mount strings, namespaces, or label files. A new Docker option needs a typed field, validation, a profile
  allowlist or ceiling, a `create_from_plan` mapping, and a rejection test.
- `PolicyError` carries a fixed field name and `PolicyReason`, never a value. Keep `repr=False` on image, command,
  user, mounts, networks, security options, and mount paths. Tests assert planted secrets stay out of `str` and `repr`.
- Validators use exact types (`type(x) is int`, `bool` rejected as a number) and raise `PolicyError`, not
  `AttributeError` or `TypeError`, for `None` or malformed components. Sequences normalize to tuples.
- `Mount` accepts only `bind` or `volume`, an absolute target, and no comma, quote, NUL, or line break, so no raw
  CLI fragment fits. `LogPolicy` allows `json-file` or `local`; non-blocking needs `loss_approved` and
  `buffer_bytes`, blocking forbids `buffer_bytes`.
- `RuntimePolicy.supported_controls` is operator-declared and must contain every name in `CONTROLS`.
  Adding a control means re-qualifying each runtime declaration.

## Planning

- `plan_create` is pure: no `shutil.which`, subprocess, or image call. Order matters: binding first, then
  image (registry, digest, reference syntax), user, capabilities, privilege, read-only, security options, mounts,
  networks, resources, logging, runtime. Rejection tests assert fixed field names such as `image.digest`.
- Profile values are exact allowlists: security options and logging must equal the profile's, mounts must be a
  subset with unique targets, resources stay at or under ceilings. No profile registry or isolation default exists.
- `CreatePlan` hides its profile and request in `repr`; `summary` returns only profile, version, and endpoint alias.

## Creation

- `create_from_plan` re-runs `plan_create`, then `_prepare()`, then calls `client.container.create`. Rejection
  therefore comes before the pull or inspect preflight. Queue the whole function through admission, not a started
  operation. It doesn't start the container, retry, inspect effective isolation, or hold secrets.
- Fixed mappings: `cap_drop=("ALL",)`, `memory_swap` equals `memory`, log options `max-size`, `max-file`, `mode`
  (plus `max-buffer-size` when non-blocking), and mounts rebuilt from typed `Mount` fields.

## Related Tests

- `tests/pywhales/test_profiles.py`: exact create argv, per-field rejection before any preflight, binding, summary.
- `tests/pywhales/test_profiles_validation.py`: cached endpoint override, malformed components, one-time resolution.
- `tests/pywhales/test_profiles_fixtures.py`: shared offline `client`, `create_request`, and `profile` fixtures.
- `tests/pywhales/test_environment.py`: `test_overlay_change_invalidates_endpoint_binding`.
