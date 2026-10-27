from enum import StrEnum
from pathlib import Path
from subprocess import CompletedProcess
from typing import Literal, assert_never
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, JsonValue, ValidationError

from pywhales import DockerClient
from pywhales.components.buildx.imagetools.models import Manifest
from pywhales.components.compose.models import (
    ComposeConfig,
    ComposeConfigNetwork,
    ComposeConfigService,
    ComposeConfigVolume,
    ComposeServiceBuild,
    ComposeServiceNetwork,
    ComposeServicePort,
    ComposeServiceVolume,
    DependencyCondition,
)
from pywhales.enums import (
    BuildxCallMethod,
    BuildxProgress,
    BuildxProgressMode,
    BuildxReportCall,
    ComposeDependencyCondition,
    ComposeProgress,
    ComposeProgressInput,
    ComposePullPolicy,
    MountType,
    NetworkProtocol,
    PublishMode,
)


@pytest.fixture
def client() -> DockerClient:
    result = DockerClient()
    result.client_config._client_call_with_path = ["docker-test"]
    return result


@pytest.fixture(autouse=True)
def command(monkeypatch: pytest.MonkeyPatch) -> Mock:
    result = Mock(return_value=CompletedProcess([], 0, b"", b""))
    monkeypatch.setattr("subprocess.run", result)
    monkeypatch.setattr(
        "pywhales.utils.Popen", Mock(side_effect=AssertionError("unexpected Popen"))
    )
    return result


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (ComposeServiceBuild, "args"),
        (ComposeServiceBuild, "labels"),
        (ComposeServiceVolume, "bind"),
        (ComposeServiceNetwork, "driver_opts"),
        (ComposeConfigService, "blkio_config"),
        (ComposeConfigService, "configs"),
        (ComposeConfigService, "devices"),
        (ComposeConfigNetwork, "driver_opts"),
        (ComposeConfigNetwork, "ipam"),
        (ComposeConfigVolume, "driver_opts"),
        (ComposeConfig, "configs"),
        (ComposeConfig, "secrets"),
    ],
)
@pytest.mark.parametrize("valid", [False, True])
def test_compose_json_fields_when_nested_values_are_supplied(
    model: type[BaseModel], field: str, valid: bool
) -> None:
    payload = {"nested": [None, True, 2, 1.5, "value", {"enabled": False}]}

    if valid:
        result = model.model_validate({field: payload})

        assert result.model_dump(exclude_unset=True) == {field: payload}
    else:
        with pytest.raises(ValidationError):
            model.model_validate({field: {"nested": [Path("not-json")]}})


@pytest.mark.parametrize("value", [None, True, 3, 1.5, "source", [None, {"a": 1}]])
def test_config_json_when_value_is_not_an_object(value: JsonValue) -> None:
    result = ComposeConfig.model_validate({"configs": value})

    assert result.configs == value


@pytest.mark.parametrize(
    ("model", "field", "enum_type"),
    [
        (DependencyCondition, "condition", ComposeDependencyCondition),
        (ComposeServicePort, "mode", PublishMode),
        (ComposeServicePort, "protocol", NetworkProtocol),
        (ComposeServiceVolume, "type", MountType),
    ],
)
@pytest.mark.parametrize("kind", ["known", "future", "enum", "none"])
def test_compose_output_values_when_wire_types_vary(
    model: type[BaseModel],
    field: str,
    enum_type: type[StrEnum],
    kind: Literal["known", "future", "enum", "none"],
) -> None:
    member = next(iter(enum_type))
    match kind:
        case "known":
            value = member.value
        case "future":
            value = "future-wire-value"
        case "enum":
            value = member
        case "none":
            value = None
        case unreachable:
            assert_never(unreachable)

    result = model.model_validate({field: value})

    assert type(getattr(result, field)) is type(value)
    assert result.model_dump(mode="json", exclude_unset=True) == {field: value}


def test_compose_defaults_when_instances_are_independent() -> None:
    first = ComposeConfigService.model_validate({})
    second = ComposeConfigService.model_validate({})

    first.depends_on["db"] = DependencyCondition(condition="service_healthy")
    first.image = "updated"

    assert second.depends_on == {}
    assert first.image == "updated"
    assert first.configs is None
    assert not ComposeConfigService.model_fields["configs"].is_required()


def test_manifest_annotations_when_json_is_nested() -> None:
    payload = {"nested": [None, True, {"count": 2}]}

    result = Manifest.model_validate(
        {"mediaType": "future-media", "schemaVersion": 2, "annotations": payload}
    )

    assert result.annotations == payload
    assert result.model_dump(by_alias=True)["mediaType"] == "future-media"
    assert Manifest.model_fields["media_type"].is_required()
    assert Manifest.model_fields["schema_version"].is_required()


def test_manifest_annotations_when_value_is_not_json() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            {"mediaType": None, "schemaVersion": None, "annotations": {"x": Path("x")}}
        )


@pytest.mark.parametrize("progress", ["json", ComposeProgress.JSON])
def test_compose_build_argv_when_progress_is_enum_or_literal(
    client: DockerClient, command: Mock, progress: ComposeProgressInput
) -> None:
    client.compose.build("web", progress=progress)

    assert command.call_args.args[0] == [
        "docker-test",
        "compose",
        "--progress",
        "json",
        "build",
        "web",
    ]


def test_compose_build_argv_when_progress_is_vendor_specific(
    client: DockerClient, command: Mock
) -> None:
    client.compose.build("web", progress="vendor-mode")

    assert command.call_args.args[0] == [
        "docker-test",
        "compose",
        "--progress",
        "vendor-mode",
        "build",
        "web",
    ]


def test_compose_create_argv_when_pull_is_build(
    client: DockerClient, command: Mock
) -> None:
    client.compose.create("web", pull=ComposePullPolicy.BUILD)

    assert command.call_args.args[0] == [
        "docker-test",
        "compose",
        "create",
        "--pull",
        "build",
        "web",
    ]


def test_compose_up_argv_when_pull_is_enum(client: DockerClient, command: Mock) -> None:
    client.compose.up("web", pull=ComposePullPolicy.MISSING)

    assert command.call_args.args[0] == [
        "docker-test",
        "compose",
        "up",
        "--pull",
        "missing",
        "web",
    ]


@pytest.mark.parametrize("call", ["check", BuildxCallMethod.CHECK])
@pytest.mark.parametrize("progress", ["plain", BuildxProgressMode.PLAIN, False])
@pytest.mark.parametrize("operation", ["build", "bake"])
def test_buildx_report_argv_when_enum_or_literal_is_used(
    client: DockerClient,
    command: Mock,
    call: BuildxReportCall,
    progress: BuildxProgress,
    operation: Literal["build", "bake"],
) -> None:
    command.return_value = CompletedProcess([], 0, b"report\n", b"")
    progress_args = [] if progress is False else ["--progress", "plain"]

    match operation:
        case "build":
            result = client.buildx.build(".", call=call, progress=progress)
            report_args = [*progress_args, "--call", "check"]
        case "bake":
            result = client.buildx.bake(".", call=call, progress=progress)
            report_args = ["--call", "check", *progress_args]
        case unreachable:
            assert_never(unreachable)

    assert result == "report"
    command.assert_called_once()
    assert command.call_args.args[0] == [
        "docker-test",
        "buildx",
        operation,
        *report_args,
        ".",
    ]
    assert (command.call_args.kwargs["stderr"] is not None) is (progress is False)


def test_compose_raw_config_when_extensions_are_nested(
    client: DockerClient, command: Mock
) -> None:
    command.return_value = CompletedProcess(
        [], 0, b'{"services":{},"extension":[null,true,{"n":3}]}', b""
    )

    result = client.compose.config(return_json=True)

    assert result == {"services": {}, "extension": [None, True, {"n": 3}]}


def test_bake_raw_config_when_nested_values_are_present(
    client: DockerClient, command: Mock
) -> None:
    command.return_value = CompletedProcess(
        [], 0, b'{"target":{"app":{"args":{"A":null},"tags":["app:1"]}}}', b""
    )

    result = client.buildx.bake(print=True, call=BuildxCallMethod.BUILD)

    assert result == {"target": {"app": {"args": {"A": None}, "tags": ["app:1"]}}}
