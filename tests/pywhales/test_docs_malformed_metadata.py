"""Provider cleanup contracts against malformed metadata from a fake CLI.

Subprocess responses are patched rather than executed. Inventory and acquisition
state distinguish owned resources from borrowed ones without a real daemon.
"""

import json
from collections.abc import Callable, Sequence
from subprocess import CompletedProcess
from typing import Final, Literal, TextIO, TypedDict, Unpack, assert_never
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import ValidationError

from docs import docs_utils
from pywhales import DockerClient
from pywhales.client_config import ClientConfig, ParsingError
from pywhales.exceptions import DockerException

type Demo = Literal["containers", "builders", "images", "plugins"]

DEMOS: Final[dict[Demo, Callable[[], str]]] = {
    "containers": docs_utils.generate_code_demo_containers,
    "builders": docs_utils.generate_code_demo_builders,
    "images": docs_utils.generate_code_demo_images,
    "plugins": docs_utils.generate_code_demo_plugins,
}
IDENTIFIER: Final = UUID(int=0)
RESOURCE_NAME: Final = f"pywhales-docs-{IDENTIFIER.hex}"
PLUGIN_NAME: Final = "mochoa/s3fs-volume-plugin"


class RunOptions(TypedDict):
    input: bytes | None
    stdout: int | TextIO | None
    stderr: int | TextIO | None
    env: dict[str, str]
    pass_fds: Sequence[int]


class MalformedDocker:
    """Stateful fake CLI with acquisitions that retain malformed inspect metadata.

    Mutable presence and command histories let tests distinguish successful
    acquisitions, failed acquisition commands, borrowed state, and removal.
    """

    def __init__(self, demo: Demo) -> None:
        """Select the resource scenario and initialize empty command histories."""
        self.demo = demo
        self.present = False
        self.acquisition_fails = False
        self.acquisitions: list[list[str]] = []
        self.removals: list[list[str]] = []

    def execute(
        self, args: list[str], **kwargs: Unpack[RunOptions]
    ) -> CompletedProcess[bytes]:
        """Simulate a known CLI command and update acquisition or removal state.

        Args:
            args: Full command including the expected executable/context prefix.
            **kwargs: Subprocess options accepted by the patched boundary.

        Returns:
            Byte-output CLI success or the configured acquisition failure result.

        Raises:
            AssertionError: If the command prefix or operation is unexpected.
        """
        assert args[:3] == ["docker-docs", "--context", "docs-context"]
        command = args[3:]
        output = ""
        match command:
            case ["image", "inspect", _]:
                if self.demo == "images" and not self.present:
                    return CompletedProcess(args, 1, b"", b"No such image: ubuntu")
                snapshot = {"Id": "image-id", "RepoTags": ["ubuntu:latest"]}
                if self.demo == "images":
                    snapshot["Created"] = "malformed"
                output = json.dumps([snapshot])
            case ["container", "inspect", _]:
                output = json.dumps(
                    [
                        {
                            "Id": "container-id",
                            "Name": f"/{RESOURCE_NAME}",
                            "Created": "malformed",
                        }
                    ]
                )
            case ["plugin", "inspect", _]:
                output = json.dumps(
                    [
                        {
                            "Id": "plugin-id",
                            "Name": f"{PLUGIN_NAME}:latest",
                            "Enabled": "malformed",
                        }
                    ]
                )
            case (
                ["container", "run", *_]
                | ["buildx", "create", *_]
                | ["image", "pull", *_]
                | ["plugin", "install", *_]
            ):
                self.acquisitions.append(command)
                if self.acquisition_fails:
                    return CompletedProcess(args, 1, b"", b"acquisition failed")
                self.present = True
                output = RESOURCE_NAME if self.demo == "builders" else "container-id"
            case ["container", "list", *options]:
                if self.present:
                    output = (
                        f"{RESOURCE_NAME}\tcontainer-id"
                        if "--format" in options
                        else "container-id"
                    )
            case ["buildx", "ls", "--format", format_string]:
                if self.present:
                    output = (
                        json.dumps({"Name": RESOURCE_NAME, "Nodes": "malformed"})
                        if "json" in format_string
                        else f"{RESOURCE_NAME}\t{RESOURCE_NAME}"
                    )
            case ["image", "ls", *_]:
                if self.present:
                    output = "ubuntu:latest"
            case ["plugin", "list", *options]:
                if self.present:
                    output = (
                        f"{PLUGIN_NAME}:latest"
                        if "--format" in options
                        else "plugin-id"
                    )
            case (
                ["container", "rm", *_]
                | ["buildx", "rm", *_]
                | ["image", "rm", *_]
                | ["plugin", "remove", *_]
            ):
                self.removals.append(command)
                self.present = False
            case unexpected:
                raise AssertionError(unexpected)
        return CompletedProcess(args, 0, output.encode(), b"")


def expected_removal(demo: Demo) -> list[str]:
    """Return the cleanup argv expected for the selected acquired resource."""
    match demo:
        case "containers":
            return ["container", "rm", "--force", "container-id"]
        case "builders":
            return ["buildx", "rm", RESOURCE_NAME]
        case "images":
            return ["image", "rm", "--force", "--no-prune", "ubuntu"]
        case "plugins":
            return ["plugin", "remove", "--force", PLUGIN_NAME]
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("demo", DEMOS)
def test_live_demo_removes_owned_resource_when_metadata_stays_malformed(
    demo: Demo,
) -> None:
    cli = MalformedDocker(demo)
    client = DockerClient(
        client_config=ClientConfig(
            context="docs-context", _client_call_with_path=["docker-docs"]
        )
    )

    with (
        patch.object(docs_utils, "docker", client),
        patch.object(docs_utils, "uuid4", return_value=IDENTIFIER),
        patch("subprocess.run", side_effect=cli.execute),
        pytest.raises(ValidationError if demo == "builders" else ParsingError),
    ):
        DEMOS[demo]()

    assert len(cli.acquisitions) == 1
    assert cli.removals == [expected_removal(demo)]


@pytest.mark.parametrize("demo", DEMOS)
def test_live_demo_preserves_borrowed_resource_when_metadata_is_malformed(
    demo: Demo,
) -> None:
    cli = MalformedDocker(demo)
    cli.present = True
    client = DockerClient(
        client_config=ClientConfig(
            context="docs-context", _client_call_with_path=["docker-docs"]
        )
    )

    with (
        patch.object(docs_utils, "docker", client),
        patch.object(docs_utils, "uuid4", return_value=IDENTIFIER),
        patch("subprocess.run", side_effect=cli.execute),
        pytest.raises(ValidationError if demo == "builders" else ParsingError),
    ):
        DEMOS[demo]()

    assert cli.acquisitions == []
    assert cli.removals == []


@pytest.mark.parametrize("demo", DEMOS)
def test_live_demo_does_not_remove_when_acquisition_command_fails(demo: Demo) -> None:
    cli = MalformedDocker(demo)
    cli.acquisition_fails = True
    client = DockerClient(
        client_config=ClientConfig(
            context="docs-context", _client_call_with_path=["docker-docs"]
        )
    )

    with (
        patch.object(docs_utils, "docker", client),
        patch.object(docs_utils, "uuid4", return_value=IDENTIFIER),
        patch("subprocess.run", side_effect=cli.execute),
        pytest.raises(DockerException),
    ):
        DEMOS[demo]()

    assert len(cli.acquisitions) == 1
    assert cli.removals == []
