import json
from enum import StrEnum
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from pywhales import DockerClient
from pywhales.components.container.cli_wrapper import ContainerListFilter
from pywhales.components.container.models import (
    ContainerConfig,
    ContainerDeviceRequest,
    ContainerGraphDriver,
    ContainerHealth,
    ContainerHostConfig,
    ContainerInspectResult,
    ContainerLogConfig,
    ContainerMount,
    ContainerMountBindOption,
    ContainerRestartPolicy,
    ContainerState,
    ContainerVolumeDriverConfig,
    Mount,
    NetworkInspectResult,
    NetworkSettings,
)
from pywhales.components.image.models import ImageGraphDriver, ImageInspectResult
from pywhales.enums import (
    BindPropagation,
    ContainerStatus,
    HealthStatus,
    Isolation,
    MountType,
    RestartPolicyName,
)
from pywhales.utils import DockerCamelModel

ENUM_FIELDS: Final[tuple[tuple[type[DockerCamelModel], str, StrEnum], ...]] = (
    (ContainerHealth, "status", HealthStatus.HEALTHY),
    (ContainerState, "status", ContainerStatus.RUNNING),
    (ContainerRestartPolicy, "name", RestartPolicyName.ON_FAILURE),
    (ContainerMountBindOption, "propagation", BindPropagation.RPRIVATE),
    (ContainerMount, "type", MountType.BIND),
    (ContainerHostConfig, "isolation", Isolation.HYPERV),
    (Mount, "type", MountType.VOLUME),
    (Mount, "propagation", BindPropagation.RSHARED),
)
JSON_FIELDS: Final[tuple[tuple[type[DockerCamelModel], str], ...]] = (
    (ContainerLogConfig, "config"),
    (ContainerVolumeDriverConfig, "options"),
    (ContainerHostConfig, "storage_opt"),
    (ContainerHostConfig, "sysctls"),
    (ContainerConfig, "exposed_ports"),
    (ContainerConfig, "volumes"),
    (NetworkInspectResult, "driver_options"),
    (NetworkSettings, "ports"),
    (ContainerGraphDriver, "data"),
    (ContainerInspectResult, "node"),
    (ImageGraphDriver, "data"),
)


@pytest.mark.parametrize(("model_type", "field", "member"), ENUM_FIELDS)
@pytest.mark.parametrize("future", [False, True])
def test_output_strings_remain_strings_when_known_or_future_values_are_parsed(
    model_type: type[DockerCamelModel], field: str, member: StrEnum, future: bool
) -> None:
    value = "future-docker-value" if future else member.value

    snapshot = model_type.model_validate_json(json.dumps({field: value}))

    assert type(getattr(snapshot, field)) is str
    assert json.loads(snapshot.model_dump_json(exclude_unset=True)) == {field: value}


@pytest.mark.parametrize(("model_type", "field", "member"), ENUM_FIELDS)
def test_explicit_enum_is_retained_when_an_output_member_is_parsed(
    model_type: type[DockerCamelModel], field: str, member: StrEnum
) -> None:
    snapshot = model_type.model_validate({field: member})

    assert getattr(snapshot, field) is member
    assert json.loads(snapshot.model_dump_json(exclude_unset=True)) == {
        field: member.value
    }


@pytest.mark.parametrize(("model_type", "field"), JSON_FIELDS)
def test_nested_json_is_preserved_when_metadata_fields_are_parsed(
    model_type: type[DockerCamelModel], field: str
) -> None:
    payload: dict[str, JsonValue] = {
        field: {"nested": [None, True, 3, 1.25, "text", {"empty": []}]}
    }

    snapshot = model_type.model_validate_json(json.dumps(payload))

    assert snapshot.model_dump(exclude_unset=True) == payload
    assert json.loads(snapshot.model_dump_json(exclude_unset=True)) == payload


@pytest.mark.parametrize(("model_type", "field"), JSON_FIELDS)
@pytest.mark.parametrize("invalid", [b"bytes", {1, 2}, (1, 2), Path("not-json")])
def test_non_json_metadata_is_rejected_when_nested_values_are_parsed(
    model_type: type[DockerCamelModel],
    field: str,
    invalid: bytes | set[int] | tuple[int, int] | Path,
) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({field: {"nested": [invalid]}})


@pytest.mark.parametrize(
    ("model_type", "field"),
    [
        (ContainerLogConfig, "config"),
        (ContainerHostConfig, "storage_opt"),
        (ContainerInspectResult, "node"),
        (ImageGraphDriver, "data"),
    ],
)
@pytest.mark.parametrize("value", [None, True, 7, 3.5, "text", []])
def test_json_value_fields_preserve_non_object_json_values(
    model_type: type[DockerCamelModel], field: str, value: JsonValue
) -> None:
    snapshot = model_type.model_validate_json(json.dumps({field: value}))

    assert json.loads(snapshot.model_dump_json(exclude_unset=True)) == {field: value}


def test_device_capabilities_keep_list_nesting_and_device_ids_alias() -> None:
    snapshot = ContainerDeviceRequest.model_validate_json(
        '{"DeviceIDs":["gpu-0"],"Capabilities":[["gpu","compute"],["utility"]]}'
    )

    assert snapshot.device_ids == ["gpu-0"]
    assert snapshot.capabilities == [["gpu", "compute"], ["utility"]]
    assert json.loads(snapshot.model_dump_json(by_alias=True, exclude_unset=True)) == {
        "DeviceIDs": ["gpu-0"],
        "Capabilities": [["gpu", "compute"], ["utility"]],
    }


def test_non_json_capability_is_rejected_inside_nested_list() -> None:
    with pytest.raises(ValidationError):
        ContainerDeviceRequest.model_validate({"Capabilities": [[Path("not-json")]]})


@pytest.mark.parametrize(("model_type", "field"), JSON_FIELDS)
def test_optional_metadata_remains_none_when_omitted(
    model_type: type[DockerCamelModel], field: str
) -> None:
    snapshot = model_type.model_validate({})

    assert getattr(snapshot, field) is None


def test_nested_container_models_remain_mutable_with_wire_aliases() -> None:
    snapshot = ContainerInspectResult.model_validate_json(
        '{"State":{"Status":"running","Health":{"Status":"healthy"}},'
        '"HostConfig":{"RestartPolicy":{"Name":"on-failure"},'
        '"Mounts":[{"Type":"bind","BindOptions":{"Propagation":"rprivate"}}]},'
        '"Node":{"future":[null,true]}}'
    )
    assert snapshot.state is not None

    snapshot.state.status = ContainerStatus.EXITED

    assert snapshot.state.status is ContainerStatus.EXITED
    assert snapshot.state.health is not None
    assert snapshot.state.health.status == "healthy"
    assert snapshot.host_config is not None
    assert snapshot.host_config.restart_policy is not None
    assert snapshot.host_config.restart_policy.name == "on-failure"
    assert snapshot.host_config.mounts is not None
    assert isinstance(snapshot.host_config.mounts[0], ContainerMount)
    assert snapshot.node == {"future": [None, True]}


def test_image_inspect_keeps_nested_container_config_and_json_graph_data() -> None:
    payload = {
        "GraphDriver": {"Data": {"future": [None, True, {"layers": []}]}},
        "Config": {"ExposedPorts": {"80/tcp": {}}, "Volumes": {"/data": {}}},
    }

    snapshot = ImageInspectResult.model_validate_json(json.dumps(payload))

    assert isinstance(snapshot.config, ContainerConfig)
    assert snapshot.config.exposed_ports == {"80/tcp": {}}
    assert snapshot.config.volumes == {"/data": {}}
    assert (
        json.loads(snapshot.model_dump_json(by_alias=True, exclude_unset=True))
        == payload
    )


@pytest.mark.parametrize(
    ("key", "member"),
    [
        *(("status", member) for member in ContainerStatus),
        *(("health", member) for member in HealthStatus),
        *(("isolation", member) for member in Isolation),
    ],
)
def test_constrained_filters_accept_existing_strings_and_matching_enum_members(
    key: str, member: StrEnum
) -> None:
    adapter = TypeAdapter(ContainerListFilter)

    assert adapter.validate_python((key, member)) == (key, member.value)
    assert adapter.validate_json(json.dumps([key, member.value])) == (key, member.value)


@pytest.mark.parametrize("key", ["status", "health", "isolation"])
def test_constrained_filters_reject_future_values_instead_of_broadening_inputs(
    key: str,
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ContainerListFilter).validate_python((key, "future-docker-value"))


def test_enum_filters_render_original_wire_values_in_container_list_argv() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    with patch("pywhales.components.container.cli_wrapper.run", return_value="") as run:
        containers = client.container.list(
            filters=[
                ("status", ContainerStatus.RUNNING),
                ("health", HealthStatus.HEALTHY),
                ("isolation", Isolation.HYPERV),
            ]
        )

    assert containers == []
    run.assert_called_once()
    assert list(run.call_args.args[0]) == [
        "docker-test",
        "container",
        "list",
        "-q",
        "--no-trunc",
        "--filter",
        "status=running",
        "--filter",
        "health=healthy",
        "--filter",
        "isolation=hyperv",
    ]
