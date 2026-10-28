"""Verify archive digests before safe data-only ZIP inspection and extraction."""

import hashlib
import io
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .api import GitHub, require
from .models import Artifact, Artifacts, Bundle, Job, Run


def sha256(content: bytes) -> str:
    """Hash exact bytes, including the ZIP wrapper when used for downloads."""
    return hashlib.sha256(content).hexdigest()


def inventory(api: GitHub, run: Run) -> tuple[Artifact, ...]:
    """Enumerate the entire run-scoped inventory; names distinguish attempts."""
    artifacts: list[Artifact] = []
    page = 1
    while True:
        batch = api.get(
            f"actions/runs/{run.id}/artifacts?per_page=100&page={page}", Artifacts
        ).artifacts
        artifacts.extend(batch)
        if len(batch) < 100:
            return tuple(artifacts)
        page += 1


def select(artifacts: tuple[Artifact, ...], name: str) -> Artifact:
    """Reject absent, duplicate and expired evidence instead of choosing one."""
    matches = [artifact for artifact in artifacts if artifact.name == name]
    require(len(matches) == 1, "missing or ambiguous artifact")
    require(not matches[0].expired, "expired artifact")
    return matches[0]


def linked(artifact: Artifact, run: Run, job: Job) -> None:
    """Bind run-scoped material to its same-attempt producing job interval."""
    link = artifact.workflow_run
    require(
        link.id == run.id
        and link.repository_id == run.repository.id
        and link.head_repository_id == run.head_repository.id
        and link.head_sha == run.head_sha
        and link.head_branch == "main",
        "artifact run linkage",
    )
    require(
        job.completed_at is not None
        and job.started_at
        <= artifact.created_at
        <= artifact.updated_at
        <= job.completed_at,
        "artifact attempt interval",
    )


def download(api: GitHub, artifact: Artifact) -> dict[str, bytes]:
    """Check the authoritative ZIP digest, then reject unsafe or ambiguous entries."""
    response = api.response(f"actions/artifacts/{artifact.id}/zip")
    require(response.status == 200, "artifact download failed")
    require(
        f"sha256:{sha256(response.body)}" == artifact.digest, "archive digest mismatch"
    )
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            require(
                bool(path.parts)
                and not path.is_absolute()
                and all(part not in {"..", ".git", ".github"} for part in path.parts)
                and "\\" not in entry.filename
                and ":" not in entry.filename
                and str(path) == entry.filename.rstrip("/"),
                "unsafe archive path",
            )
            mode = stat.S_IFMT(entry.external_attr >> 16)
            require(mode in {0, stat.S_IFREG, stat.S_IFDIR}, "archive special file")
            if entry.is_dir():
                continue
            require(entry.filename not in files, "duplicate archive path")
            files[entry.filename] = archive.read(entry)
    require(bool(files), "empty artifact")
    return files


def describe(artifact: Artifact, files: dict[str, bytes]) -> Bundle:
    """Bind the ZIP identity to all extracted file hashes."""
    return Bundle(
        artifact_id=artifact.id,
        name=artifact.name,
        archive_sha256=artifact.digest.removeprefix("sha256:"),
        files={name: sha256(content) for name, content in files.items()},
    )


def verified(api: GitHub, artifact: Artifact, expected: Bundle) -> dict[str, bytes]:
    """Verify every byte and forbid additional or missing files."""
    files = download(api, artifact)
    require(
        describe(artifact, files) == expected, "artifact content or identity mismatch"
    )
    return files


def extract(files: dict[str, bytes], destination: Path) -> None:
    """Write previously validated regular files into a fresh owned directory."""
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
