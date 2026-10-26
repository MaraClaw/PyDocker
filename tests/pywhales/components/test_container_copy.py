from collections.abc import Sequence
from pathlib import Path
from typing import get_type_hints

import pytest

import pywhales.components.container.cli_wrapper as container_cli
from pywhales import DockerClient
from pywhales.client_config import ClientConfig
from pywhales.components.container.cli_wrapper import Container, ContainerPath
from pywhales.utils import ValidPath


@pytest.mark.parametrize("parameter", ["source", "destination"])
def test_copy_parameters_accept_only_paths(parameter: str) -> None:
    hints = get_type_hints(container_cli.ContainerCLI.copy)

    assert hints[parameter] == ValidPath | ContainerPath


@pytest.mark.parametrize(
    ("source", "destination", "expected"),
    [
        (
            "local file.txt",
            ("example", "/tmp/in file.txt"),
            ("local file.txt", "example:/tmp/in file.txt"),
        ),
        (
            Path("local file.txt"),
            ("example", Path("/tmp/in file.txt")),
            ("local file.txt", "example:/tmp/in file.txt"),
        ),
        (
            ("example", "/tmp/out file.txt"),
            "local file.txt",
            ("example:/tmp/out file.txt", "local file.txt"),
        ),
        (
            ("example", Path("/tmp/out file.txt")),
            Path("local file.txt"),
            ("example:/tmp/out file.txt", "local file.txt"),
        ),
        (
            "example:/tmp/raw file.txt",
            Path("local file.txt"),
            ("example:/tmp/raw file.txt", "local file.txt"),
        ),
        (
            Path("local file.txt"),
            "example:/tmp/raw file.txt",
            ("local file.txt", "example:/tmp/raw file.txt"),
        ),
        (
            "local file.txt",
            (
                Container(ClientConfig(), "immutable-id", is_immutable_id=True),
                Path("/tmp/in file.txt"),
            ),
            ("local file.txt", "immutable-id:/tmp/in file.txt"),
        ),
        (
            (
                Container(ClientConfig(), "immutable-id", is_immutable_id=True),
                Path("/tmp/out file.txt"),
            ),
            Path("local file.txt"),
            ("immutable-id:/tmp/out file.txt", "local file.txt"),
        ),
    ],
)
def test_copy_preserves_path_arguments(
    monkeypatch: pytest.MonkeyPatch,
    source: ValidPath | ContainerPath,
    destination: ValidPath | ContainerPath,
    expected: tuple[str, str],
) -> None:
    commands: list[Sequence[str | Path]] = []

    def run(command: Sequence[str | Path]) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(container_cli, "run", run)
    client = DockerClient(
        client_config=ClientConfig(_client_call_with_path=["container-cli"])
    )

    client.copy(source, destination)

    assert commands == [["container-cli", "container", "cp", *expected]]
