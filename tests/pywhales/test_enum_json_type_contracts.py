from collections.abc import Iterable
from typing import assert_type

from pydantic import JsonValue

from pywhales import DockerClient, Image, Network, Volume, enums
from pywhales.client_config import ClientConfig, ReloadableObjectFromJson
from pywhales.components.buildx.cli_wrapper import BakeResult
from pywhales.components.config.models import (
    ConfigInspectResult,
    ConfigSpecDriver,
    DockerObjectVersion,
)
from pywhales.components.context.models import ContextInspectResult
from pywhales.components.network.models import NetworkInspectResult, NetworkIPAM
from pywhales.components.secret.models import SecretInspectResult, SecretSpec
from pywhales.components.volume.models import VolumeInspectResult
from pywhales.utils import MappedProcessStream


def _check_client_enum_inputs() -> None:
    config = ClientConfig(
        client_type=enums.ClientType.NERDCTL,
        compose_progress=enums.ComposeProgress.PLAIN,
    )
    assert_type(config.client_type, enums.ClientTypeInput)
    assert_type(config.compose_progress, enums.ComposeProgressInput | None)
    assert_type(DockerClient(client_config=config), DockerClient)
    assert_type(
        DockerClient(
            client_type=enums.ClientType.DOCKER,
            compose_progress=enums.ComposeProgress.JSON,
        ),
        DockerClient,
    )
    assert_type(
        DockerClient(client_type="docker", compose_progress="json"), DockerClient
    )


def _check_buildx_enum_overloads(client: DockerClient) -> None:
    assert_type(client.buildx.build(".", call="build"), Image | None)
    assert_type(
        client.buildx.build(".", call=enums.BuildxCallMethod.BUILD), Image | None
    )
    assert_type(client.buildx.build(".", call="check"), str)
    assert_type(client.buildx.build(".", call="outline"), str)
    assert_type(client.buildx.build(".", call="targets"), str)
    assert_type(client.buildx.build(".", call=enums.BuildxCallMethod.CHECK), str)
    assert_type(client.buildx.build(".", call=enums.BuildxCallMethod.OUTLINE), str)
    assert_type(client.buildx.build(".", call=enums.BuildxCallMethod.TARGETS), str)
    assert_type(
        client.buildx.build(".", call=enums.BuildxCallMethod.CHECK, stream_logs=True),
        MappedProcessStream[str],
    )
    assert_type(
        client.buildx.build(".", progress=enums.BuildxProgressMode.RAWJSON),
        Image | None,
    )
    assert_type(client.buildx.build(".", progress=False), Image | None)
    assert_type(client.buildx.bake(call="build"), BakeResult)
    assert_type(client.buildx.bake(call="build", return_config=False), None)
    assert_type(client.buildx.bake(call="check"), str)
    assert_type(client.buildx.bake(call="outline"), str)
    assert_type(client.buildx.bake(call="targets"), str)
    assert_type(client.buildx.bake(call=enums.BuildxCallMethod.BUILD), BakeResult)
    assert_type(
        client.buildx.bake(call=enums.BuildxCallMethod.BUILD, return_config=False),
        None,
    )
    assert_type(client.buildx.bake(call=enums.BuildxCallMethod.OUTLINE), str)
    assert_type(client.buildx.bake(call=enums.BuildxCallMethod.TARGETS), str)
    assert_type(client.buildx.bake(call=enums.BuildxCallMethod.CHECK), str)
    assert_type(
        client.buildx.bake(call=enums.BuildxCallMethod.CHECK, stream_logs=True),
        MappedProcessStream[str],
    )


def _check_dynamic_buildx_call(
    client: DockerClient, call: enums.BuildxCallMethod
) -> None:
    assert_type(client.buildx.build(".", call=call), Image | str | None)
    assert_type(client.buildx.bake(call=call), BakeResult | str)
    assert_type(client.buildx.bake(call=call, return_config=False), str | None)


def _check_compose_enum_overloads(client: DockerClient) -> None:
    assert_type(client.compose.create(pull=enums.ComposePullPolicy.BUILD), None)
    assert_type(client.compose.create(pull="build"), None)
    assert_type(client.compose.up(pull=enums.ComposePullPolicy.MISSING), None)
    assert_type(client.compose.up(pull="never"), None)
    assert_type(
        client.compose.create(pull=enums.ComposePullPolicy.BUILD, stream_logs=True),
        Iterable[tuple[str, bytes]] | None,
    )
    assert_type(
        client.compose.up(pull=enums.ComposePullPolicy.ALWAYS, stream_logs=True),
        Iterable[tuple[str, bytes]] | None,
    )


def _check_network_models(snapshot: NetworkInspectResult, ipam: NetworkIPAM) -> None:
    assert_type(snapshot.scope, enums.ResourceScope | str | None)
    assert_type(snapshot.options, dict[str, JsonValue] | None)
    assert_type(snapshot.config_from, dict[str, JsonValue] | None)
    assert_type(ipam.config, list[dict[str, JsonValue]] | None)
    assert_type(ipam.options, dict[str, JsonValue] | None)


def _check_volume_context_models(
    volume: VolumeInspectResult, context: ContextInspectResult
) -> None:
    assert_type(volume.scope, enums.ResourceScope | str | None)
    assert_type(volume.status, dict[str, JsonValue] | None)
    assert_type(volume.options, dict[str, str] | None)
    assert_type(context.metadata, dict[str, JsonValue] | None)
    assert_type(context.tls_material, dict[str, JsonValue] | None)


def _check_config_models(
    snapshot: ConfigInspectResult, driver: ConfigSpecDriver
) -> None:
    assert_type(snapshot.version, DockerObjectVersion | None)
    assert_type(driver.options, dict[str, JsonValue] | None)


def _check_secret_models(snapshot: SecretInspectResult, spec: SecretSpec) -> None:
    assert_type(snapshot.version, dict[str, JsonValue] | None)
    assert_type(spec.labels, dict[str, JsonValue] | None)


def _check_public_properties(client: DockerClient) -> None:
    network = client.network.inspect("network")
    volume = client.volume.inspect("volume")
    context = client.context.inspect("context")
    assert_type(network.scope, enums.ResourceScope | str | None)
    assert_type(network.options, dict[str, JsonValue] | None)
    assert_type(network.config_from, dict[str, JsonValue] | None)
    assert_type(volume.scope, enums.ResourceScope | str | None)
    assert_type(volume.status, dict[str, JsonValue] | None)
    assert_type(context.metadata, dict[str, JsonValue] | None)
    assert_type(context.tls_material, dict[str, JsonValue] | None)
    assert_type(
        client.network.list(filters=[("scope", enums.ResourceScope.SWARM)]),
        list[Network],
    )
    assert_type(
        client.volume.list(filters=[("scope", enums.ResourceScope.LOCAL)]),
        list[Volume],
    )


def _check_shared_json_boundary(
    resource: ReloadableObjectFromJson[NetworkInspectResult],
    payload: dict[str, JsonValue],
) -> None:
    assert_type(resource._fetch_inspect_result_json("network"), dict[str, JsonValue])
    assert_type(resource._parse_json_object(payload), NetworkInspectResult)
    assert_type(resource._parse_inspect_result(payload), NetworkInspectResult)
