from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pywhales.client_config import ClientConfig
from pywhales.components.buildx.cli_wrapper import Builder
from pywhales.components.config.cli_wrapper import Config
from pywhales.components.context.cli_wrapper import Context
from pywhales.components.manifest.cli_wrapper import ManifestList
from pywhales.components.plugin.cli_wrapper import Plugin
from pywhales.components.secret.cli_wrapper import Secret
from pywhales.components.service.cli_wrapper import Service

type CleanupResource = (
    Service | Config | Secret | Context | Builder | ManifestList | Plugin
)


@pytest.mark.parametrize(
    "resource_case",
    [
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
    [None, ValueError, KeyboardInterrupt],
    ids=["body-success", "body-error", "body-interrupt"],
)
@pytest.mark.parametrize(
    "cleanup_error_type",
    [None, RuntimeError, SystemExit],
    ids=["cleanup-success", "cleanup-error", "cleanup-interrupt"],
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
    cleanup_error = (
        cleanup_error_type("cleanup") if cleanup_error_type is not None else None
    )

    def use_resource() -> None:
        with resource as entered:
            assert entered is resource
            if body_error is not None:
                raise body_error

    with patch(
        "subprocess.run",
        side_effect=cleanup_error,
        return_value=CompletedProcess([], 0, b"", b""),
    ) as command:
        if body_error is None and cleanup_error is None:
            use_resource()
        else:
            with pytest.raises(BaseException) as raised:
                use_resource()
            if body_error is not None and cleanup_error is not None:
                assert isinstance(raised.value, BaseExceptionGroup)
                assert len(raised.value.exceptions) == 2
                assert raised.value.exceptions[0] is body_error
                assert raised.value.exceptions[1] is cleanup_error
                assert raised.value.__cause__ is None
                assert raised.value.__suppress_context__ is True
            else:
                assert raised.value is (
                    body_error if body_error is not None else cleanup_error
                )
        command.assert_called_once()
        assert command.call_args.args[0] == [
            "docker-origin",
            "--context",
            "origin-context",
            "--host",
            "tcp://origin-host:2376",
            *removal_args,
            "resource",
        ]
