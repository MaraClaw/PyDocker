import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ClientConfig
from pywhales.components.buildx.cli_wrapper import Builder
from pywhales.components.config.cli_wrapper import Config
from pywhales.components.container.cli_wrapper import Container
from pywhales.components.context.cli_wrapper import Context
from pywhales.components.image.cli_wrapper import Image
from pywhales.components.manifest.cli_wrapper import ManifestList
from pywhales.components.network.cli_wrapper import Network
from pywhales.components.plugin.cli_wrapper import Plugin
from pywhales.components.secret.cli_wrapper import Secret
from pywhales.components.service.cli_wrapper import Service
from pywhales.components.volume.cli_wrapper import Volume
from pywhales.exceptions import NoSuchService

type CleanupResource = (
    Container
    | Image
    | Network
    | Volume
    | Service
    | Config
    | Secret
    | Context
    | Builder
    | ManifestList
    | Plugin
)


@pytest.mark.parametrize(
    "resource_case",
    [
        pytest.param((Container, ("container", "rm", "--volumes")), id="container"),
        pytest.param((Image, ("image", "rm", "--force")), id="image"),
        pytest.param((Network, ("network", "remove")), id="network"),
        pytest.param((Volume, ("volume", "remove")), id="volume"),
        pytest.param((Service, ("service", "remove")), id="service"),
        pytest.param((Config, ("config", "rm")), id="config"),
        pytest.param((Secret, ("secret", "remove")), id="secret"),
        pytest.param((Context, ("context", "remove", "--force")), id="context"),
        pytest.param((Builder, ("buildx", "rm")), id="builder"),
        pytest.param((ManifestList, ("manifest", "rm")), id="manifest"),
        pytest.param((Plugin, ("plugin", "remove", "--force")), id="plugin"),
    ],
)
@pytest.mark.parametrize(
    "body_error_type",
    [None, ValueError, KeyboardInterrupt, SystemExit],
    ids=["body-success", "body-error", "body-interrupt", "body-exit"],
)
@pytest.mark.parametrize(
    "cleanup_error_type",
    [None, RuntimeError, ValueError, KeyboardInterrupt, SystemExit, NoSuchService],
    ids=[
        "cleanup-success",
        "cleanup-error",
        "cleanup-value-error",
        "cleanup-interrupt",
        "cleanup-exit",
        "cleanup-unrelated-missing",
    ],
)
def test_cleanup_preserves_failures_when_resource_exits(
    resource_case: tuple[type[CleanupResource], tuple[str, ...]],
    body_error_type: type[BaseException] | None,
    cleanup_error_type: type[BaseException] | None,
) -> None:
    resource_type, removal_args = resource_case
    client_config = ClientConfig(
        context="origin-context",
        host="tcp://origin-host:2376",
        _client_call_with_path=["docker-origin"],
    )
    resource = resource_type(client_config, "resource", is_immutable_id=True)
    body_error = body_error_type("body") if body_error_type is not None else None
    cleanup_error: BaseException | None
    if cleanup_error_type is NoSuchService:
        cleanup_error = NoSuchService(["docker-origin", *removal_args, "resource"], 1)
    else:
        cleanup_error = (
            cleanup_error_type("cleanup") if cleanup_error_type is not None else None
        )

    def use_resource() -> None:
        with resource as entered:
            assert entered is resource
            assert entered.client_config is client_config
            if body_error is not None:
                raise body_error

    command_prefix = [
        "docker-origin",
        "--context",
        "origin-context",
        "--host",
        "tcp://origin-host:2376",
    ]
    responses: list[CompletedProcess[bytes] | BaseException] = []
    expected_commands = []
    if resource_type is Container:
        responses.append(
            CompletedProcess(
                [],
                0,
                b'[{"Id":"resource","HostConfig":{"AutoRemove":false},"State":{"Running":false}}]',
                b"",
            )
        )
        expected_commands.append([*command_prefix, "container", "inspect", "resource"])
    responses.append(
        cleanup_error
        if cleanup_error is not None
        else CompletedProcess([], 0, b"", b"")
    )
    expected_commands.append([*command_prefix, *removal_args, "resource"])

    with (
        patch("subprocess.run", side_effect=responses) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        if body_error is None and cleanup_error is None:
            use_resource()
        else:
            with pytest.raises(BaseException) as raised:
                use_resource()
            if body_error is not None and cleanup_error is not None:
                assert isinstance(raised.value, BaseExceptionGroup)
                assert raised.value.message == (
                    f"{resource_type.__name__} body and cleanup both failed"
                )
                assert len(raised.value.exceptions) == 2
                assert raised.value.exceptions[0] is body_error
                assert raised.value.exceptions[1] is cleanup_error
                assert raised.value.__cause__ is None
                assert raised.value.__suppress_context__ is True
            else:
                assert raised.value is (
                    body_error if body_error is not None else cleanup_error
                )
        assert [call.args[0] for call in command.call_args_list] == expected_commands


@pytest.mark.parametrize("component", ["container", "image", "network", "volume"])
@pytest.mark.parametrize("batch", [False, True], ids=["scalar", "batch"])
def test_cleanup_deletes_inspected_handles(component: str, batch: bool) -> None:
    client = DockerClient(context="origin-context", host="tcp://origin-host:2376")
    client.client_config._client_call_with_path = ["docker-origin"]
    api = {
        "container": client.container,
        "image": client.image,
        "network": client.network,
        "volume": client.volume,
    }[component]
    identity = "existing-volume" if component == "volume" else "a" * 64
    reference = identity if component == "volume" else identity[:12]
    snapshot = {
        "Id": identity,
        "Name": identity,
        "HostConfig": {"AutoRemove": False},
        "State": {"Running": False},
    }
    command_prefix = [
        "docker-origin",
        "--context",
        "origin-context",
        "--host",
        "tcp://origin-host:2376",
    ]
    with (
        patch(
            "subprocess.run",
            side_effect=[
                CompletedProcess([], 0, json.dumps([snapshot]).encode(), b""),
                CompletedProcess([], 0, b"", b""),
            ],
        ) as command,
        patch("pywhales.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        resource = api.inspect([reference])[0] if batch else api.inspect(reference)
        with resource as entered:
            assert entered is resource
            assert entered.client_config is client.client_config
            assert str(entered) == identity

    removal_args = {
        "container": ["rm", "--volumes"],
        "image": ["rm", "--force"],
        "network": ["remove"],
        "volume": ["remove"],
    }[component]
    assert [call.args[0] for call in command.call_args_list] == [
        [*command_prefix, component, "inspect", reference],
        [*command_prefix, component, *removal_args, identity],
    ]
