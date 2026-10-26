import pytest

import pywhales.components.buildx.imagetools.cli_wrapper as imagetools
from pywhales import DockerClient
from pywhales.client_config import Command


def test_create_accepts_default_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[Command] = []

    def run(command: Command) -> str:
        commands.append(command)
        return (
            '{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
        )

    monkeypatch.setattr(imagetools, "run", run)

    manifest = DockerClient().buildx.imagetools.create(["example:latest"], dry_run=True)

    assert manifest.schema_version == 2
    assert manifest.media_type == "application/vnd.oci.image.index.v1+json"
    assert len(commands) == 1
    assert commands[0][-1] == "example:latest"
    assert "--dry-run" in commands[0]
    assert "--tag" not in commands[0]
    assert "--annotation" not in commands[0]
    assert "--file" not in commands[0]
