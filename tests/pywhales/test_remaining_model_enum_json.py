import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pywhales.components.config.models import ConfigInspectResult, ConfigSpecDriver
from pywhales.components.context.models import ContextInspectResult
from pywhales.components.network.models import NetworkInspectResult, NetworkIPAM
from pywhales.components.secret.models import SecretInspectResult, SecretSpec
from pywhales.components.volume.models import VolumeInspectResult
from pywhales.enums import ResourceScope
from pywhales.utils import DockerCamelModel


def test_network_preserves_nested_json_when_inspect_payload_uses_aliases() -> None:
    payload = {
        "Name": "network",
        "Scope": "future-scope",
        "IPAM": {
            "Config": [{"Subnet": "192.0.2.0/24", "Extension": [1, True, None]}],
            "Options": {"vendor": {"weights": [1, 2.5]}},
        },
        "Options": {"vendor": {"enabled": False}},
        "ConfigFrom": {"Network": "source", "Extension": {"priority": 2}},
    }

    snapshot = NetworkInspectResult.model_validate_json(json.dumps(payload))

    assert snapshot.ipam is not None
    assert snapshot.ipam.config == [
        {"Subnet": "192.0.2.0/24", "Extension": [1, True, None]}
    ]
    assert snapshot.scope == "future-scope"
    assert type(snapshot.scope) is str
    assert (
        snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    )


def test_volume_preserves_status_json_when_driver_adds_extension_data() -> None:
    payload = {
        "Name": "volume",
        "Scope": "future-scope",
        "Driver": "vendor.driver",
        "Status": {"allocations": [{"ready": True, "bytes": 42, "note": None}]},
        "Options": {"vendor.option": "true"},
    }

    snapshot = VolumeInspectResult.model_validate_json(json.dumps(payload))

    assert snapshot.status == payload["Status"]
    assert snapshot.options == {"vendor.option": "true"}
    assert snapshot.scope == "future-scope"
    assert type(snapshot.scope) is str
    assert (
        snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    )


def test_config_preserves_driver_options_when_templating_uses_nested_json() -> None:
    payload = {
        "ID": "config-id",
        "Version": {"Index": 7},
        "Spec": {
            "Name": "config",
            "Data": "aGVsbG8=",
            "Templating": {
                "Name": "vendor-template",
                "Options": {"extension": {"values": [1, 2.5, True, None]}},
            },
        },
    }

    snapshot = ConfigInspectResult.model_validate_json(json.dumps(payload))

    assert snapshot.version is not None
    assert snapshot.version.index == 7
    assert snapshot.spec is not None
    assert snapshot.spec.templating is not None
    assert snapshot.spec.templating.options == {
        "extension": {"values": [1, 2.5, True, None]}
    }
    assert (
        snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    )


def test_secret_preserves_dictionaries_when_version_and_labels_have_json() -> None:
    payload = {
        "ID": "secret-id",
        "Version": {"Index": 7, "Extension": ["future", None]},
        "Spec": {"Name": "secret", "Labels": {"extension": {"enabled": True}}},
    }

    snapshot = SecretInspectResult.model_validate_json(json.dumps(payload))

    assert snapshot.version == {"Index": 7, "Extension": ["future", None]}
    assert snapshot.spec is not None
    assert snapshot.spec.labels == {"extension": {"enabled": True}}
    assert (
        snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    )


def test_context_preserves_json_when_metadata_and_tls_use_docker_aliases() -> None:
    payload = {
        "Name": "context",
        "Metadata": {"otel": {"enabled": True, "ports": [4317, None]}},
        "Endpoints": {
            "docker": {"Host": "unix:///docker.sock", "SkipTLSVerify": False}
        },
        "TLSMaterial": {"docker": ["ca.pem", {"extension": 3.5}]},
        "Storage": {"MetadataPath": "<IN MEMORY>", "TLSPath": "<IN MEMORY>"},
    }

    snapshot = ContextInspectResult.model_validate_json(json.dumps(payload))

    assert snapshot.metadata == payload["Metadata"]
    assert snapshot.tls_material == payload["TLSMaterial"]
    assert snapshot.endpoints is not None
    assert snapshot.endpoints["docker"].skip_tls_verify is False
    assert snapshot.storage is not None
    assert snapshot.storage.tls_path == "<IN MEMORY>"
    assert (
        snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    )


@pytest.mark.parametrize(
    ("model_type", "field_name"),
    [
        (NetworkIPAM, "Options"),
        (NetworkInspectResult, "Options"),
        (NetworkInspectResult, "ConfigFrom"),
        (VolumeInspectResult, "Status"),
        (ConfigSpecDriver, "Options"),
        (SecretSpec, "Labels"),
        (SecretInspectResult, "Version"),
        (ContextInspectResult, "Metadata"),
        (ContextInspectResult, "TLSMaterial"),
    ],
)
@pytest.mark.parametrize("invalid", [Path("not-json"), b"not-json", {1, 2}])
def test_json_dictionary_rejects_non_json_when_nested_in_extension(
    model_type: type[DockerCamelModel],
    field_name: str,
    invalid: Path | bytes | set[int],
) -> None:
    payload = {"ID": "secret-id", field_name: {"extension": [{"invalid": invalid}]}}

    with pytest.raises(ValidationError) as failure:
        model_type.model_validate(payload)

    assert failure.value.errors()[0]["type"] == "invalid-json-value"


def test_ipam_config_rejects_non_json_when_extension_contains_a_path() -> None:
    payload = {"Config": [{"Extension": {"path": Path("not-json")}}]}

    with pytest.raises(ValidationError) as failure:
        NetworkIPAM.model_validate(payload)

    assert failure.value.errors()[0]["type"] == "invalid-json-value"


@pytest.mark.parametrize(
    "model_type",
    [
        NetworkIPAM,
        NetworkInspectResult,
        VolumeInspectResult,
        ConfigInspectResult,
        ConfigSpecDriver,
        SecretSpec,
        ContextInspectResult,
    ],
)
def test_optional_fields_default_to_none_when_inspect_payload_is_empty(
    model_type: type[DockerCamelModel],
) -> None:
    payload = {}

    snapshot = model_type.model_validate(payload)

    assert snapshot.model_dump() == dict.fromkeys(model_type.model_fields, None)


def test_secret_id_is_required_when_inspect_payload_omits_it() -> None:
    payload = {}

    with pytest.raises(ValidationError) as failure:
        SecretInspectResult.model_validate(payload)

    assert [(error["loc"], error["type"]) for error in failure.value.errors()] == [
        (("ID",), "missing")
    ]


def test_secret_id_accepts_none_when_explicitly_supplied() -> None:
    payload = {"ID": None}

    snapshot = SecretInspectResult.model_validate(payload)

    assert snapshot.id is None


@pytest.mark.parametrize("model_type", [NetworkInspectResult, VolumeInspectResult])
@pytest.mark.parametrize("scope", ["local", "global", "swarm", "future-scope", ""])
def test_resource_scope_preserves_plain_strings_when_inspecting(
    model_type: type[NetworkInspectResult] | type[VolumeInspectResult], scope: str
) -> None:
    payload = {"Scope": scope}

    snapshot = model_type.model_validate(payload)

    assert type(snapshot.scope) is str
    assert snapshot.scope == scope
    assert (
        snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    )


@pytest.mark.parametrize("model_type", [NetworkInspectResult, VolumeInspectResult])
def test_resource_scope_preserves_enum_members_when_explicitly_supplied(
    model_type: type[NetworkInspectResult] | type[VolumeInspectResult],
) -> None:
    payload = {"Scope": ResourceScope.SWARM}

    snapshot = model_type.model_validate(payload)

    assert snapshot.scope is ResourceScope.SWARM
    assert snapshot.model_dump(mode="json", by_alias=True, exclude_unset=True) == {
        "Scope": "swarm"
    }
