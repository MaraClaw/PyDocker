"""Plugin-installer shell regressions using local assets and a fake downloader.

Tests run Bash in a temporary sandbox, not Docker or real network downloads.
Installed plugin files are inert fixture bytes rather than executable plugins.
"""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

SCRIPTS: Final = Path(__file__).resolve().parents[2] / "scripts"
FIXTURE_ASSETS: Final = {
    "compose-5.6.1": b"inert compose fixture\n",
    "buildx-0.38.1": b"inert buildx fixture\n",
}


@dataclass(frozen=True, slots=True)
class InstallerSandbox:
    """Temporary installer checkout with isolated homes, assets, and plugin files."""

    root: Path

    @property
    def plugins(self) -> Path:
        return self.root / "docker config" / "cli-plugins"

    @property
    def manifest(self) -> Path:
        return self.root / "scripts" / "docker-plugin-checksums.txt"

    def run(
        self, overrides: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the copied installer with sandboxed paths and capture its result.

        A fake wget copies local fixture assets. The real Bash child has a
        ten-second timeout; nonzero exit status is returned rather than raised.

        Args:
            overrides: Environment values applied after sandbox defaults, or
                None to keep those defaults.

        Returns:
            The shell result with captured text stdout and stderr.
        """
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"COMPOSE_VERSION", "BUILDX_VERSION", "DOCKER_CONFIG"}
        }
        environment.update(
            HOME=str(self.root / "home"),
            DOCKER_CONFIG=str(self.plugins.parent),
            PATH=f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            FIXTURE_ROOT=str(self.root),
        )
        environment.update(overrides or {})
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "download-docker-plugins.sh")],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )


@pytest.fixture
def sandbox(tmp_path: Path) -> InstallerSandbox:
    """Prepare a copied installer, local assets, and inert existing plugin files.

    Rewrite copied checksums for fixture bytes and install a fake wget on the
    sandbox PATH. Pytest owns the temporary tree; preparation starts no process.

    Returns:
        A sandbox ready to launch the copied Bash installer without downloading.
    """
    for directory in ("scripts", "bin", "assets", "home"):
        (tmp_path / directory).mkdir()
    for name in ("download-docker-plugins.sh", "docker-plugin-checksums.txt"):
        shutil.copyfile(SCRIPTS / name, tmp_path / "scripts" / name)
    instance = InstallerSandbox(tmp_path)
    pins = []
    for line in instance.manifest.read_text().splitlines():
        if line.startswith("#"):
            continue
        plugin, version, _digest = line.split()
        content = FIXTURE_ASSETS[f"{plugin}-{version}"]
        (tmp_path / "assets" / f"{plugin}-{version}").write_bytes(content)
        pins.append(f"{plugin} {version} {hashlib.sha256(content).hexdigest()}\n")
    instance.manifest.write_text("".join(pins))
    transport = tmp_path / "bin" / "wget"
    transport.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[[ $# == 4 && $1 == -q && $3 == -O ]]
printf '%s\n' "$2" >> "$FIXTURE_ROOT/downloads"
case "$2" in
    https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-linux-x86_64) asset=compose-5.6.1 ;;
    https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.linux-amd64) asset=buildx-0.38.1 ;;
    *) exit 9 ;;
esac
if [[ "$asset" == "${FAIL_ASSET-}" ]]; then
    printf 'partial download' > "$4"
    exit 8
fi
cp "$FIXTURE_ROOT/assets/$asset" "$4"
"""
    )
    transport.chmod(0o755)
    instance.plugins.mkdir(parents=True)
    (instance.plugins / "docker-compose").write_bytes(b"existing compose")
    (instance.plugins / "docker-buildx").write_bytes(b"existing buildx")
    return instance


def test_release_manifest_contains_only_verified_current_pins() -> None:
    pins = [
        line.split()
        for line in (SCRIPTS / "docker-plugin-checksums.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert sorted(pins) == [
        [
            "buildx",
            "0.38.1",
            "e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4",
        ],
        [
            "compose",
            "5.6.1",
            "47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0",
        ],
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"COMPOSE_VERSION": "5.6.1", "BUILDX_VERSION": "0.38.1"},
    ],
    ids=["defaults", "explicit-current"],
)
def test_activates_both_verified_plugins(
    sandbox: InstallerSandbox, overrides: Mapping[str, str]
) -> None:
    result = sandbox.run(overrides)

    assert result.returncode == 0, result.stderr
    for plugin, version in (("compose", "5.6.1"), ("buildx", "0.38.1")):
        installed = sandbox.plugins / f"docker-{plugin}"
        assert installed.read_bytes() == FIXTURE_ASSETS[f"{plugin}-{version}"]
        assert installed.stat().st_mode & 0o777 == 0o755
    assert sorted(path.name for path in sandbox.plugins.iterdir()) == [
        "docker-buildx",
        "docker-compose",
    ]


def test_uses_home_when_docker_config_is_empty(sandbox: InstallerSandbox) -> None:
    result = sandbox.run({"DOCKER_CONFIG": ""})

    assert result.returncode == 0, result.stderr
    plugins = sandbox.root / "home" / ".docker" / "cli-plugins"
    assert (plugins / "docker-compose").read_bytes() == FIXTURE_ASSETS["compose-5.6.1"]
    assert (sandbox.plugins / "docker-compose").read_bytes() == b"existing compose"


@pytest.mark.parametrize("asset", ["compose-5.6.1", "buildx-0.38.1"])
def test_digest_mismatch_preserves_both_existing_plugins(
    sandbox: InstallerSandbox, asset: str
) -> None:
    (sandbox.root / "assets" / asset).write_bytes(b"corrupt untrusted bytes")

    result = sandbox.run()

    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr
    assert {path.name: path.read_bytes() for path in sandbox.plugins.iterdir()} == {
        "docker-compose": b"existing compose",
        "docker-buildx": b"existing buildx",
    }


def test_second_download_failure_preserves_both_existing_plugins(
    sandbox: InstallerSandbox,
) -> None:
    result = sandbox.run({"FAIL_ASSET": "buildx-0.38.1"})

    assert result.returncode == 8
    assert len((sandbox.root / "downloads").read_text().splitlines()) == 2
    assert {path.name: path.read_bytes() for path in sandbox.plugins.iterdir()} == {
        "docker-compose": b"existing compose",
        "docker-buildx": b"existing buildx",
    }


@pytest.mark.parametrize("variable", ["COMPOSE_VERSION", "BUILDX_VERSION"])
@pytest.mark.parametrize("version", ["99.99.99", ""])
def test_unknown_version_fails_before_download(
    sandbox: InstallerSandbox, variable: str, version: str
) -> None:
    result = sandbox.run({variable: version})

    assert result.returncode != 0
    assert not (sandbox.root / "downloads").exists()
    assert {path.name: path.read_bytes() for path in sandbox.plugins.iterdir()} == {
        "docker-compose": b"existing compose",
        "docker-buildx": b"existing buildx",
    }


@pytest.mark.parametrize(
    "replacement",
    [
        "",
        "buildx 0.38.1 invalid-digest\n",
        f"buildx 0.38.1 {'0' * 64}\nbuildx 0.38.1 {'1' * 64}\n",
    ],
)
def test_missing_or_malformed_pin_fails_before_download(
    sandbox: InstallerSandbox, replacement: str
) -> None:
    lines = sandbox.manifest.read_text().splitlines(keepends=True)
    sandbox.manifest.write_text(
        "".join(
            replacement if line.startswith("buildx 0.38.1 ") else line for line in lines
        )
    )

    result = sandbox.run()

    assert result.returncode != 0
    assert not (sandbox.root / "downloads").exists()
    assert {path.name: path.read_bytes() for path in sandbox.plugins.iterdir()} == {
        "docker-compose": b"existing compose",
        "docker-buildx": b"existing buildx",
    }


def test_well_formed_incorrect_pin_rejects_downloaded_bytes(
    sandbox: InstallerSandbox,
) -> None:
    sandbox.manifest.write_text(
        f"compose 5.6.1 {hashlib.sha256(FIXTURE_ASSETS['compose-5.6.1']).hexdigest()}\n"
        f"buildx 0.38.1 {'0' * 64}\n"
    )

    result = sandbox.run()

    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr
    assert {path.name: path.read_bytes() for path in sandbox.plugins.iterdir()} == {
        "docker-compose": b"existing compose",
        "docker-buildx": b"existing buildx",
    }
