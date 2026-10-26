import json
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

import pytest

from pywhales import DockerClient
from pywhales.components.compose.models import ComposeConfig

pytestmark = pytest.mark.docker
type CLIRun = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@pytest.fixture
def cli_run(docker_client: DockerClient) -> CLIRun:
    def invoke(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*docker_client.client_config.client_call, *arguments],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )

    return invoke


@pytest.fixture
def smoke_image() -> str:
    return f"pywhales-modern-smoke:{uuid4().hex}"


@pytest.fixture
def smoke_project(
    tmp_path: Path, smoke_image: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    (tmp_path / "Dockerfile").write_text(
        "# syntax=docker/dockerfile:1\nFROM scratch AS smoke\n"
        "ARG SMOKE_VALUE=expected\nLABEL smoke.value=$SMOKE_VALUE\n"
    )
    (tmp_path / "compose.yml").write_text(
        f"services:\n  app:\n    image: {smoke_image}\n"
        "    build:\n      context: .\n      dockerfile: Dockerfile\n"
    )
    (tmp_path / "bake.json").write_text(
        json.dumps(
            {
                "group": {"default": {"targets": ["app"]}},
                "target": {
                    "app": {"context": str(tmp_path), "dockerfile": "Dockerfile"}
                },
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def compose_client(docker_client: DockerClient, smoke_project: Path) -> DockerClient:
    return DockerClient(
        client_call=docker_client.client_config.client_call,
        compose_files=[smoke_project / "compose.yml"],
        compose_project_name="pywhales-modern-smoke",
        compose_parallel=1,
        compose_progress="plain",
    )


def test_installed_versions_match_pins(cli_run: CLIRun) -> None:
    compose_version = cli_run(["compose", "version", "--short"]).stdout.strip()
    buildx_version = cli_run(["buildx", "version"]).stdout

    assert compose_version.removeprefix("v") == "5.6.1"
    assert re.search(r"\bv0\.38\.1\b", buildx_version)


@pytest.mark.parametrize(
    "capability",
    [
        (("compose",), "--dry-run"),
        (("compose",), "--parallel"),
        (("compose",), "--progress"),
        (("compose", "build"), "--print"),
        (("compose", "build"), "--check"),
        (("compose", "build"), "--provenance"),
        (("compose", "build"), "--sbom"),
        (("buildx", "build"), "--call"),
        (("buildx", "bake"), "--call"),
        (("buildx", "bake"), "--jobs"),
    ],
)
def test_explicit_cli_capability(
    cli_run: CLIRun, capability: tuple[tuple[str, ...], str]
) -> None:
    command, flag = capability

    help_output = cli_run([*command, "--help"]).stdout

    flags = set(re.findall(r"--[a-z][a-z-]*\b", help_output))
    assert flag in flags, f"Required CLI capability: {' '.join(command)} {flag}"


def test_compose_config(compose_client: DockerClient, smoke_image: str) -> None:
    config = compose_client.compose.config()

    assert isinstance(config, ComposeConfig)
    assert config.services is not None
    assert config.services["app"].image == smoke_image


def test_compose_dry_run_build(
    docker_client: DockerClient, smoke_project: Path, smoke_image: str
) -> None:
    client = DockerClient(
        client_call=docker_client.client_config.client_call,
        compose_files=[smoke_project / "compose.yml"],
        compose_project_name="pywhales-modern-smoke",
        compose_dry_run=True,
        compose_parallel=1,
        compose_progress="plain",
    )
    assert not docker_client.image.list(filters=[("reference", smoke_image)])

    result = client.compose.build("app", builder="default")

    assert result is None
    assert not docker_client.image.list(filters=[("reference", smoke_image)])


def test_compose_print_definition(
    compose_client: DockerClient,
    smoke_project: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    result = compose_client.compose.build("app", builder="default", print=True)

    captured = capfd.readouterr()
    assert result is None
    dockerfile = Path(json.loads(captured.out)["target"]["app"]["dockerfile"])
    assert (smoke_project / dockerfile).resolve() == (
        smoke_project / "Dockerfile"
    ).resolve()


def test_bake_print_definition(
    docker_client: DockerClient, smoke_project: Path
) -> None:
    result = docker_client.buildx.bake(
        files=[smoke_project / "bake.json"], builder="default", print=True
    )

    assert isinstance(result, dict)
    assert result["target"]["app"]["context"] == str(smoke_project)


def test_bake_jobs_print(
    docker_client: DockerClient,
    smoke_project: Path,
) -> None:
    result = docker_client.buildx.bake(
        "app",
        builder="default",
        files=[smoke_project / "bake.json"],
        jobs=1,
        print=True,
    )

    assert isinstance(result, dict)
    assert result["target"]["app"]["context"] == str(smoke_project)


def test_buildx_build_targets_report(
    docker_client: DockerClient,
    smoke_project: Path,
) -> None:
    result = docker_client.buildx.build(
        smoke_project, builder="default", call="targets"
    )

    assert "smoke" in result


def test_buildx_bake_targets_report(
    docker_client: DockerClient,
    smoke_project: Path,
) -> None:
    result = docker_client.buildx.bake(
        "app", builder="default", files=[smoke_project / "bake.json"], call="targets"
    )

    assert "smoke" in result
