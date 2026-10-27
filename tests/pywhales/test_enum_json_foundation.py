import json
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

import pywhales.enums as enums
from pywhales._types import JsonObject
from pywhales.utils import DockerCamelModel


class ScopeSnapshot(DockerCamelModel):
    scope: enums.ResourceScope | str | None = None


class JsonSnapshot(DockerCamelModel):
    payload: JsonObject


@pytest.mark.parametrize(
    ("member", "wire_value"),
    [
        (enums.ContainerStatus.RUNNING, "running"),
        (enums.HealthStatus.HEALTHY, "healthy"),
        (enums.RestartPolicyName.ON_FAILURE, "on-failure"),
        (enums.MountType.BIND, "bind"),
        (enums.BindPropagation.RPRIVATE, "rprivate"),
        (enums.Isolation.HYPERV, "hyperv"),
        (enums.TaskState.SHUTDOWN, "shutdown"),
        (enums.RestartCondition.ANY, "any"),
        (enums.NodeRole.MANAGER, "manager"),
        (enums.NodeAvailability.DRAIN, "drain"),
        (enums.NodeState.READY, "ready"),
        (enums.NodeReachability.REACHABLE, "reachable"),
        (enums.EndpointMode.DNSRR, "dnsrr"),
        (enums.PublishMode.INGRESS, "ingress"),
        (enums.NetworkProtocol.TCP, "tcp"),
        (enums.UpdateOrder.START_FIRST, "start-first"),
        (enums.FailureAction.ROLLBACK, "rollback"),
        (enums.ServiceUpdateState.ROLLBACK_STARTED, "rollback_started"),
        (enums.ResourceScope.GLOBAL, "global"),
        (enums.SwarmState.LOCKED, "locked"),
        (enums.ComposeDependencyCondition.SERVICE_HEALTHY, "service_healthy"),
        (enums.ComposeProgress.JSON, "json"),
        (enums.ComposePullPolicy.BUILD, "build"),
        (enums.BuildxProgressMode.RAWJSON, "rawjson"),
        (enums.BuildxCallMethod.OUTLINE, "outline"),
        (enums.ClientType.NERDCTL, "nerdctl"),
        (enums.Orchestrator.KUBERNETES, "kubernetes"),
        (enums.ImageResolutionMode.CHANGED, "changed"),
    ],
)
def test_wire_value_is_preserved_when_formatting_enum(
    member: StrEnum, wire_value: str
) -> None:
    encoded = json.dumps({"value": member})

    assert str(member) == wire_value
    assert f"{member}" == wire_value
    assert member == wire_value
    assert json.loads(encoded) == {"value": wire_value}


@pytest.mark.parametrize("scope", ["local", "global", "swarm", "future-scope", ""])
def test_plain_string_is_preserved_when_known_or_future_scope_is_parsed(
    scope: str,
) -> None:
    snapshot = ScopeSnapshot.model_validate({"Scope": scope})

    assert type(snapshot.scope) is str
    assert snapshot.scope == scope
    assert snapshot.model_dump(by_alias=True) == {"Scope": scope}


def test_enum_is_preserved_when_explicit_scope_member_is_parsed() -> None:
    snapshot = ScopeSnapshot.model_validate({"Scope": enums.ResourceScope.LOCAL})

    assert snapshot.scope is enums.ResourceScope.LOCAL
    assert json.loads(snapshot.model_dump_json(by_alias=True)) == {"Scope": "local"}


def test_scope_is_optional_when_omitted_or_null() -> None:
    omitted = ScopeSnapshot.model_validate({})
    explicit = ScopeSnapshot.model_validate({"Scope": None})

    assert omitted.scope is None
    assert explicit.scope is None


@pytest.mark.parametrize("scope", ["swarm", "future-scope"])
def test_model_remains_mutable_when_scope_is_reassigned(scope: str) -> None:
    snapshot = ScopeSnapshot.model_validate({"Scope": "local"})

    snapshot.scope = scope

    assert snapshot.scope == scope


def test_nested_json_shapes_survive_when_metadata_is_parsed() -> None:
    payload: JsonObject = {
        "future": {"values": [None, True, 3, 2.5, "text", {"nested": []}]},
        "empty": {},
    }

    snapshot = JsonSnapshot.model_validate({"Payload": payload})

    assert snapshot.payload == payload
    assert json.loads(snapshot.model_dump_json(by_alias=True)) == {"Payload": payload}


@pytest.mark.parametrize("invalid", [b"bytes", {1, 2}, (1, 2), Path("not-json")])
def test_non_json_value_is_rejected_when_nested_metadata_is_parsed(
    invalid: bytes | set[int] | tuple[int, int] | Path,
) -> None:
    with pytest.raises(ValidationError):
        JsonSnapshot.model_validate({"Payload": {"nested": [invalid]}})


@pytest.mark.parametrize("invalid", [b"bytes", {1, 2}, (1, 2), Path("not-json")])
def test_non_json_value_is_rejected_when_json_value_is_parsed(
    invalid: bytes | set[int] | tuple[int, int] | Path,
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(JsonValue).validate_python(invalid)


@pytest.mark.parametrize("invalid", [[], "text", None, {1: "non-string-key"}])
def test_non_object_is_rejected_when_json_object_is_parsed(
    invalid: list[str] | str | dict[int, str] | None,
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(JsonObject).validate_python(invalid)


@pytest.mark.parametrize("policy", ["always", "missing", "never"])
def test_shared_pull_policy_is_accepted_when_create_or_up_is_parsed(
    policy: str,
) -> None:
    created = TypeAdapter(enums.ComposeCreatePullPolicy).validate_python(policy)
    started = TypeAdapter(enums.ComposeUpPullPolicy).validate_python(policy)

    assert created == policy
    assert started == policy


@pytest.mark.parametrize("policy", ["build", enums.ComposePullPolicy.BUILD])
def test_build_pull_policy_is_create_only_when_pull_input_is_parsed(
    policy: str | enums.ComposePullPolicy,
) -> None:
    assert TypeAdapter(enums.ComposeCreatePullPolicy).validate_python(policy) == policy
    with pytest.raises(ValidationError):
        TypeAdapter(enums.ComposeUpPullPolicy).validate_python(policy)


@pytest.mark.parametrize("call", ["check", "outline", "targets"])
def test_report_call_is_accepted_when_buildx_report_input_is_parsed(call: str) -> None:
    parsed = TypeAdapter(enums.BuildxReportCall).validate_python(call)

    assert parsed == call


@pytest.mark.parametrize("call", ["build", enums.BuildxCallMethod.BUILD])
def test_build_call_is_not_a_report_when_buildx_call_input_is_parsed(
    call: str | enums.BuildxCallMethod,
) -> None:
    assert TypeAdapter(enums.BuildxBuildCall).validate_python(call) == call
    assert TypeAdapter(enums.BuildxCall).validate_python(call) == call
    with pytest.raises(ValidationError):
        TypeAdapter(enums.BuildxReportCall).validate_python(call)


def test_false_is_preserved_when_buildx_progress_is_parsed() -> None:
    parsed = TypeAdapter(enums.BuildxProgress).validate_python(False)

    assert parsed is False
