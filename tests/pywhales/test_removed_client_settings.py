import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pywhales import DockerClient
from pywhales.client_config import ClientConfig
from pywhales.utils import run


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("client_binary", "docker"),
        ("client_binary", "other"),
        ("client_binary", None),
        ("compose_env_file", None),
        ("compose_env_file", "example.env"),
    ],
)
def test_client_rejects_removed_settings(keyword: str, value: str | None) -> None:
    construct = Mock(wraps=DockerClient)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        construct(**{keyword: value})


@pytest.mark.parametrize("value", [None, "example.env"])
def test_config_rejects_removed_env_file(value: str | None) -> None:
    construct = Mock(wraps=ClientConfig)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        construct(compose_env_file=value)


@pytest.mark.parametrize("debug", [None, "", "0", "true", "1"])
@pytest.mark.parametrize("raw", [False, True])
def test_debug_requires_exact_one_and_respects_diagnostic_policy(
    debug: str | None,
    raw: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if debug is None:
        monkeypatch.delenv("PYWHALES_DEBUG", raising=False)
    else:
        monkeypatch.setenv("PYWHALES_DEBUG", debug)
    sentinel = "private-argument-sentinel"
    config = ClientConfig(client_call=[sys.executable], raw_diagnostics=raw)

    output = run([*config.docker_cmd, "-c", "import sys; print(sys.argv[1])", sentinel])

    printed = capsys.readouterr().out
    assert output == sentinel
    assert bool(printed) is (debug == "1")
    assert (sentinel in printed) is (debug == "1" and raw)


def test_supported_client_call_is_lazy_and_retains_supplied_config() -> None:
    config = ClientConfig(
        client_call=["custom-cli", "wrapper"], client_binary_path="still-supported"
    )
    with patch("shutil.which", return_value="/custom-cli") as lookup:
        client = DockerClient(client_config=config, client_call=["ignored"])
        lookup.assert_not_called()

        command = client.docker_cmd

    assert client.client_config is config
    assert config.client_binary_path == "still-supported"
    assert command == ["/custom-cli", "wrapper"]
    assert config.get_client_call_with_path() == [Path("/custom-cli"), "wrapper"]
    lookup.assert_called_once_with("custom-cli")
