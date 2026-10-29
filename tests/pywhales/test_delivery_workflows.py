"""Executable delivery decisions with inert HTTP, artifact and upload boundaries.

All distribution archives and site files are synthetic. No test publishes,
dispatches, deploys, runs Docker or contacts a production service.
"""

import base64
import io
import json
import subprocess
import sys
import tarfile
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import IO, BinaryIO, override

import pytest
from pydantic import BaseModel, JsonValue

from scripts.delivery import __main__ as cli
from scripts.delivery import api as http
from scripts.delivery import archives, artifacts, budgets, limits
from scripts.delivery.api import Curl, GitHub, Rejected, Response, Transport
from scripts.delivery.artifacts import describe, download, sha256
from scripts.delivery.chain import name, producer, receipt, seal
from scripts.delivery.distributions import inspect, smoke
from scripts.delivery.models import (
    Artifact,
    ArtifactRun,
    Artifacts,
    Base,
    GitObject,
    GitRef,
    Job,
    Jobs,
    Pull,
    Release,
    Repository,
    Run,
    Seal,
    Workflow,
)
from scripts.delivery.pages import verify_pages_tar
from scripts.delivery.publication import confirm, prepare
from scripts.delivery.releases import release
from scripts.delivery.trust import CHECKS, PUBLISH_JOB, SEAL_JOB, identity

ROOT = Path(__file__).resolve().parents[2]
WHEEL = "pywhales-0.6.1-py3-none-any.whl"
SDIST = "pywhales-0.6.1.tar.gz"
BASE_URL = "https://api.github.com/repos/owner/repo/"
PYPI = "https://pypi.org/pypi/pywhales/0.6.1/json"


def zipped(files: dict[str, bytes], *, compression: int = zipfile.ZIP_STORED) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=compression) as archive:
        for filename, data in files.items():
            archive.writestr(filename, data)
    return result.getvalue()


def distributions() -> dict[str, bytes]:
    metadata = b"Name: pywhales\nVersion: 0.6.1\n"
    wheel = zipped(
        {
            "pywhales/py.typed": b"",
            "pywhales/__init__.py": b"",
            "pywhales-0.6.1.dist-info/METADATA": metadata,
        }
    )
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for filename, data in {"pywhales/py.typed": b"", "PKG-INFO": metadata}.items():
            member = tarfile.TarInfo(f"pywhales-0.6.1/{filename}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return {WHEEL: wheel, SDIST: result.getvalue()}


@dataclass
class Service(Transport):
    """Mutable in-memory external service; unknown requests fail closed."""

    routes: dict[tuple[str, str], Response] = field(default_factory=dict)
    calls: list[tuple[str, str, bytes | None]] = field(default_factory=list)
    api: GitHub = field(init=False)
    source: Run = field(init=False)
    publisher: Run = field(init=False)
    jobs: tuple[Job, ...] = field(init=False)
    tested: Seal = field(init=False)
    material: dict[str, bytes] = field(default_factory=distributions)

    def __post_init__(self) -> None:
        self.api = GitHub(self, "owner/repo", 1)
        repo = Repository(id=1, full_name="owner/repo", default_branch="main")
        self.put("", repo)
        self.source = Run(
            id=100,
            run_attempt=1,
            workflow_id=10,
            path=".github/workflows/python-package.yml",
            event="push",
            head_branch="main",
            head_sha="a" * 40,
            repository=repo,
            head_repository=repo,
            status="completed",
            conclusion="success",
        )
        self.publisher = self.source.model_copy(
            update={
                "id": 200,
                "workflow_id": 20,
                "path": ".github/workflows/python-publish.yml",
                "event": "workflow_run",
                "head_sha": "b" * 40,
            }
        )
        for run in (self.source, self.publisher):
            self.put(f"actions/runs/{run.id}", run)
            self.put(f"actions/runs/{run.id}/attempts/1", run)
            self.put(
                f"actions/workflows/{Path(run.path).name}",
                Workflow(id=run.workflow_id, path=run.path, state="active"),
            )
        self.jobs = tuple(
            self.job(index + 1, title, self.source)
            for index, title in enumerate((*CHECKS, SEAL_JOB))
        )
        self.set_jobs(self.jobs)
        self.put(
            "actions/runs/200/attempts/1/jobs?per_page=100&page=1",
            Jobs(jobs=(self.job(99, PUBLISH_JOB, self.publisher),)),
        )
        self.put(
            f"commits/{self.source.head_sha}/pulls?per_page=100&page=1", [{"number": 7}]
        )
        self.put(
            "pulls/7",
            Pull(
                merged=True,
                merge_commit_sha=self.source.head_sha,
                base=Base(ref="main", repo=repo),
            ),
        )
        dist = self.artifact(1, "dist", self.source, self.material)
        site = self.artifact(
            2,
            "site",
            self.source,
            {"index.html": b"exact tested site", ".nojekyll": b""},
        )
        self.tested = Seal(
            producer=identity(self.source),
            jobs={job.name: job.id for job in self.jobs},
            version="0.6.1",
            distributions=describe(dist, self.material),
            site=describe(site, {"index.html": b"exact tested site", ".nojekyll": b""}),
        )
        seal_artifact = self.artifact(
            3,
            "seal",
            self.source,
            {"seal.json": self.tested.model_dump_json().encode()},
        )
        self.set_artifacts((dist, site, seal_artifact))
        self.routes[("GET", PYPI)] = Response(404, b"{}")
        self.put("releases?per_page=100&page=1", [])

    def put(self, path: str, value: BaseModel | JsonValue, status: int = 200) -> None:
        body = (
            value.model_dump_json().encode()
            if isinstance(value, BaseModel)
            else json.dumps(value).encode()
        )
        url = BASE_URL + path if path else BASE_URL.removesuffix("/")
        self.routes[("GET", url)] = Response(status, body)

    @override
    def request(self, method: str, url: str, body: bytes | None = None) -> Response:
        self.calls.append((method, url, body))
        return self.routes.get((method, url), Response(404, b"{}"))

    def job(self, number: int, title: str, run: Run) -> Job:
        return Job(
            id=number,
            name=title,
            run_id=run.id,
            run_attempt=1,
            head_sha=run.head_sha,
            status="completed",
            conclusion="success",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

    def artifact(
        self, number: int, kind: str, run: Run, files: dict[str, bytes]
    ) -> Artifact:
        data = zipped(files)
        self.routes[("GET", BASE_URL + f"actions/artifacts/{number}/zip")] = Response(
            200, data
        )
        return Artifact(
            id=number,
            name=name(kind, run),
            expired=False,
            digest=f"sha256:{sha256(data)}",
            created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            workflow_run=ArtifactRun(
                id=run.id,
                repository_id=1,
                head_repository_id=1,
                head_sha=run.head_sha,
                head_branch="main",
            ),
        )

    def set_jobs(self, jobs: tuple[Job, ...]) -> None:
        self.put(
            "actions/runs/100/attempts/1/jobs?per_page=100&page=1", Jobs(jobs=jobs)
        )

    def set_artifacts(self, artifacts: tuple[Artifact, ...], run_id: int = 100) -> None:
        self.put(
            f"actions/runs/{run_id}/artifacts?per_page=100&page=1",
            Artifacts(artifacts=artifacts),
        )

    def artifacts(self) -> tuple[Artifact, ...]:
        return self.api.get(
            "actions/runs/100/artifacts?per_page=100&page=1", Artifacts
        ).artifacts

    def published(self, files: dict[str, bytes] | None = None) -> None:
        material = self.material if files is None else files
        self.routes[("GET", PYPI)] = Response(
            200,
            json.dumps(
                {
                    "urls": [
                        {
                            "filename": filename,
                            "packagetype": "bdist_wheel"
                            if filename.endswith(".whl")
                            else "sdist",
                            "digests": {"sha256": sha256(data)},
                        }
                        for filename, data in material.items()
                    ]
                }
            ).encode(),
        )

    def receipted(self) -> None:
        self.published()
        document = confirm(self.api, 100)
        artifact = self.artifact(
            4,
            "receipt",
            self.publisher,
            {"receipt.json": document.model_dump_json().encode()},
        )
        self.set_artifacts((artifact,), 200)

    def released(self, sha: str | None = None) -> None:
        self.put(
            "git/ref/tags/v0.6.1",
            GitRef(object=GitObject(type="commit", sha=sha or self.source.head_sha)),
        )
        self.put(
            "releases/tags/v0.6.1",
            Release(id=1, tag_name="v0.6.1", draft=False, prerelease=False),
        )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Service:
    instance = Service()
    for key, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REPOSITORY_ID": "1",
        "GITHUB_RUN_ID": "200",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "b" * 40,
        "PRODUCER_ATTEMPT": "1",
        "PUBLISHER_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "Curl", lambda: instance)
    return instance


def invoke(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["delivery", *arguments])
    return cli.main()


def test_repository_identity_uses_canonical_root(service: Service) -> None:
    assert service.api.identity() == service.source.repository
    assert service.calls == [("GET", BASE_URL.removesuffix("/"), None)]


@pytest.mark.parametrize(
    "repository",
    [
        Repository(id=2, full_name="owner/repo", default_branch="main"),
        Repository(id=1, full_name="other/repo", default_branch="main"),
        Repository(id=1, full_name="owner/repo", default_branch="other"),
    ],
)
def test_repository_identity_rejects_mismatch(
    service: Service, repository: Repository
) -> None:
    service.put("", repository)
    with pytest.raises(Rejected, match="repository identity"):
        service.api.identity()


def test_cli_lifecycle_preserves_exact_packages_and_manual_site(
    service: Service,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    upload = tmp_path / "upload"
    assert (
        invoke(monkeypatch, "prepare", "--run-id", "100", "--output", str(upload)) == 0
    )
    assert {
        path.name: path.read_bytes() for path in upload.iterdir()
    } == service.material
    service.published()
    assert (
        invoke(
            monkeypatch,
            "confirm",
            "--run-id",
            "100",
            "--output",
            str(tmp_path / "confirmed"),
        )
        == 0
    )
    service.receipted()
    service.released()
    assert invoke(monkeypatch, "release", "--run-id", "200") == 0
    site = tmp_path / "manual-site"
    assert invoke(monkeypatch, "pages", "--run-id", "200", "--output", str(site)) == 0
    assert {path.name: path.read_bytes() for path in site.iterdir()} == {
        "index.html": b"exact tested site",
        ".nojekyll": b"",
    }
    assert all(method == "GET" for method, _url, _body in service.calls)


@pytest.mark.parametrize(
    "fault",
    ["missing", "expired", "duplicate", "digest", "content", "run", "time", "attempt"],
)
def test_rejects_unproven_artifacts(service: Service, fault: str) -> None:
    artifacts = list(service.artifacts())
    match fault:
        case "missing":
            artifacts.pop(0)
        case "expired":
            artifacts[0] = artifacts[0].model_copy(update={"expired": True})
        case "duplicate":
            artifacts.append(artifacts[0])
        case "digest":
            service.routes[("GET", BASE_URL + "actions/artifacts/1/zip")] = Response(
                200, b"tampered"
            )
        case "content":
            artifacts[0] = artifacts[0].model_copy(
                update={"digest": f"sha256:{'0' * 64}"}
            )
        case "run":
            artifacts[0] = artifacts[0].model_copy(
                update={
                    "workflow_run": artifacts[0].workflow_run.model_copy(
                        update={"id": 101}
                    )
                }
            )
        case "time":
            artifacts[0] = artifacts[0].model_copy(
                update={"created_at": datetime(2025, 1, 1, tzinfo=UTC)}
            )
        case "attempt":
            artifacts[0] = artifacts[0].model_copy(
                update={"name": "delivery-dist-100-2"}
            )
    service.set_artifacts(tuple(artifacts))
    with pytest.raises(Rejected):
        producer(service.api, 100)


@pytest.mark.parametrize(
    "fault", ["workflow", "path", "fork", "event", "branch", "failure", "pr", "base"]
)
def test_rejects_untrusted_runs_and_inexact_merges(
    service: Service, fault: str
) -> None:
    changes = {
        "workflow": {"workflow_id": 11},
        "path": {"path": ".github/workflows/evil.yml"},
        "fork": {
            "head_repository": service.source.repository.model_copy(update={"id": 2})
        },
        "event": {"event": "pull_request"},
        "branch": {"head_branch": "topic"},
        "failure": {"conclusion": "failure"},
    }
    if fault in changes:
        run = service.source.model_copy(update=changes[fault])
        service.put("actions/runs/100", run)
        service.put("actions/runs/100/attempts/1", run)
    else:
        service.put(
            "pulls/7",
            Pull(
                merged=True,
                merge_commit_sha="c" * 40 if fault == "pr" else service.source.head_sha,
                base=Base(
                    ref="topic" if fault == "base" else "main",
                    repo=service.source.repository,
                ),
            ),
        )
    with pytest.raises(Rejected):
        producer(service.api, 100)


@pytest.mark.parametrize("fault", ["partial", "skipped", "duplicate", "attempt", "sha"])
def test_rejects_incomplete_same_attempt_checks(service: Service, fault: str) -> None:
    jobs = list(service.jobs)
    match fault:
        case "partial":
            jobs.pop(0)
        case "skipped":
            jobs[0] = jobs[0].model_copy(update={"conclusion": "skipped"})
        case "duplicate":
            jobs.append(jobs[0])
        case "attempt":
            jobs[0] = jobs[0].model_copy(update={"run_attempt": 2})
        case "sha":
            jobs[0] = jobs[0].model_copy(update={"head_sha": "c" * 40})
    service.set_jobs(tuple(jobs))
    with pytest.raises(Rejected):
        producer(service.api, 100)


@pytest.mark.parametrize("existing", [(), (WHEEL,), (WHEEL, SDIST)])
def test_partial_retries_upload_only_missing_exact_bytes(
    service: Service, tmp_path: Path, existing: tuple[str, ...]
) -> None:
    service.published({filename: service.material[filename] for filename in existing})
    prepare(service.api, 100, tmp_path / "upload")
    assert {
        path.name: path.read_bytes() for path in (tmp_path / "upload").iterdir()
    } == {
        filename: data
        for filename, data in service.material.items()
        if filename not in existing
    }


@pytest.mark.parametrize("files", [{WHEEL: b"conflict"}, {"other.whl": b"unexpected"}])
def test_destination_conflicts_stop_before_upload(
    service: Service, tmp_path: Path, files: dict[str, bytes]
) -> None:
    service.published(files)
    with pytest.raises(Rejected, match="destination"):
        prepare(service.api, 100, tmp_path / "upload")
    assert not (tmp_path / "upload").exists()


def test_skipped_publication_cannot_issue_receipt(service: Service) -> None:
    with pytest.raises(Rejected, match="incomplete"):
        confirm(service.api, 100)


def test_skipped_publisher_job_cannot_substitute_receipt(service: Service) -> None:
    service.receipted()
    job = service.job(99, PUBLISH_JOB, service.publisher).model_copy(
        update={"conclusion": "skipped"}
    )
    service.put(
        "actions/runs/200/attempts/1/jobs?per_page=100&page=1", Jobs(jobs=(job,))
    )
    with pytest.raises(Rejected, match="unsuccessful job"):
        receipt(service.api, 200)


@pytest.mark.parametrize("operation", ["release", "pages"])
def test_automatic_and_manual_paths_reject_wrong_tag(
    service: Service, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    service.receipted()
    service.released("c" * 40)
    assert invoke(monkeypatch, operation, "--run-id", "200") == 1


@pytest.mark.parametrize("race", [False, True])
def test_create_and_concurrent_release_verify_exact_final_tag(
    service: Service, race: bool
) -> None:
    service.receipted()
    document = receipt(service.api, 200)
    original = service.request

    def request(method: str, url: str, body: bytes | None = None) -> Response:
        if method == "POST" and url == BASE_URL + "git/refs":
            service.put(
                "git/ref/tags/v0.6.1",
                GitRef(object=GitObject(type="commit", sha=service.source.head_sha)),
            )
            return Response(422 if race else 201, b"{}")
        if method == "POST" and url == BASE_URL + "releases":
            assert body is not None and json.loads(body)["draft"] is True
            service.released()
            service.put(
                "releases/tags/v0.6.1",
                Release(id=1, tag_name="v0.6.1", draft=True, prerelease=False),
            )
            return Response(
                422 if race else 201,
                Release(id=1, tag_name="v0.6.1", draft=True, prerelease=False)
                .model_dump_json()
                .encode(),
            )
        if method == "PATCH" and url == BASE_URL + "releases/1":
            service.released()
            return Response(200, b"{}")
        return original(method, url, body)

    class Race(Transport):
        @override
        def request(self, method: str, url: str, body: bytes | None = None) -> Response:
            return request(method, url, body)

    service.api = GitHub(Race(), "owner/repo", 1)
    release(service.api, document)


def test_annotated_tag_is_resolved_not_trusted_by_name(service: Service) -> None:
    service.receipted()
    service.released()
    service.put(
        "git/ref/tags/v0.6.1", GitRef(object=GitObject(type="tag", sha="c" * 40))
    )
    service.put(
        f"git/tags/{'c' * 40}",
        GitRef(object=GitObject(type="commit", sha=service.source.head_sha)),
    )
    release(service.api, receipt(service.api, 200))


def test_trigger_attempt_must_match_even_with_valid_material(
    service: Service, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRODUCER_ATTEMPT", "2")
    assert (
        invoke(
            monkeypatch,
            "prepare",
            "--run-id",
            "100",
            "--output",
            str(tmp_path / "upload"),
        )
        == 1
    )
    assert not (tmp_path / "upload").exists()


def test_sealer_checks_actual_uploader_ids_and_digests(
    service: Service, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_SHA", service.source.head_sha)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.1"\n')
    for kind, artifact in zip(("DIST", "SITE"), service.artifacts()[:2], strict=True):
        monkeypatch.setenv(f"{kind}_ARTIFACT_ID", str(artifact.id))
        monkeypatch.setenv(
            f"{kind}_ARTIFACT_DIGEST", artifact.digest.removeprefix("sha256:")
        )
    assert seal(service.api, 100, tmp_path) == service.tested
    monkeypatch.setenv("DIST_ARTIFACT_ID", "987")
    with pytest.raises(Rejected, match="uploader output"):
        seal(service.api, 100, tmp_path)


@pytest.mark.parametrize(
    "path",
    ["../escape", "/absolute", "foo/../../escape", "a\\b", ".github/workflows/x"],
)
def test_archive_paths_never_escape_or_supply_executable_checkout(
    service: Service, path: str
) -> None:
    artifact = service.artifact(55, "site", service.source, {path: b"untrusted"})
    with pytest.raises(Rejected, match="unsafe archive"):
        download(service.api, artifact)


def test_real_module_cli_help_and_invalid_input() -> None:
    for arguments, expected in (
        (["--help"], 0),
        (["pages", "--run-id", "not-a-number"], 2),
        (["pages", "--run-id", "0"], 1),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.delivery", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == expected, result.stderr


def test_file_hashes_reject_repacked_tampering_with_valid_archive_digest(
    service: Service,
) -> None:
    artifacts = list(service.artifacts())
    artifacts[1] = service.artifact(
        2, "site", service.source, {"index.html": b"changed site"}
    )
    service.set_artifacts(tuple(artifacts))
    with pytest.raises(Rejected, match="content or identity"):
        producer(service.api, 100)


@pytest.mark.parametrize(
    "fault", ["missing", "expired", "duplicate", "tamper", "identity"]
)
def test_receipt_material_is_not_a_successful_run_substitute(
    service: Service, fault: str
) -> None:
    service.receipted()
    artifacts = list(
        service.api.get(
            "actions/runs/200/artifacts?per_page=100&page=1", Artifacts
        ).artifacts
    )
    match fault:
        case "missing":
            artifacts.clear()
        case "expired":
            artifacts[0] = artifacts[0].model_copy(update={"expired": True})
        case "duplicate":
            artifacts.append(artifacts[0])
        case "tamper":
            service.routes[("GET", BASE_URL + "actions/artifacts/4/zip")] = Response(
                200, b"tampered receipt"
            )
        case "identity":
            document = confirm(service.api, 100)
            document = document.model_copy(
                update={
                    "publisher": document.publisher.model_copy(update={"attempt": 2})
                }
            )
            artifacts[0] = service.artifact(
                4,
                "receipt",
                service.publisher,
                {"receipt.json": document.model_dump_json().encode()},
            )
    service.set_artifacts(tuple(artifacts), 200)
    with pytest.raises(Rejected):
        receipt(service.api, 200)


@pytest.mark.parametrize("status", [403, 404, 410, 500])
def test_download_boundary_failures_cannot_publish(
    service: Service, status: int
) -> None:
    service.routes[("GET", BASE_URL + "actions/artifacts/1/zip")] = Response(
        status, b"unavailable"
    )
    with pytest.raises(Rejected, match="download failed"):
        producer(service.api, 100)


@pytest.mark.parametrize("fault", ["kind", "duplicate", "incomplete"])
def test_destination_confirmation_requires_both_unique_correct_kinds(
    service: Service, fault: str
) -> None:
    service.published()
    if fault == "incomplete":
        service.published({WHEEL: service.material[WHEEL]})
    else:
        release_data = {
            "urls": [
                {
                    "filename": WHEEL,
                    "packagetype": "sdist" if fault == "kind" else "bdist_wheel",
                    "digests": {"sha256": sha256(service.material[WHEEL])},
                },
                {
                    "filename": SDIST if fault == "kind" else WHEEL,
                    "packagetype": "sdist" if fault == "kind" else "bdist_wheel",
                    "digests": {
                        "sha256": sha256(
                            service.material[SDIST if fault == "kind" else WHEEL]
                        )
                    },
                },
            ]
        }
        service.routes[("GET", PYPI)] = Response(200, json.dumps(release_data).encode())
    with pytest.raises(Rejected):
        confirm(service.api, 100)


def test_late_page_duplicate_is_rejected_instead_of_selecting_first(
    service: Service,
) -> None:
    artifacts = service.artifacts()
    first = (artifacts[0],) * 100
    service.put(
        "actions/runs/100/artifacts?per_page=100&page=1", Artifacts(artifacts=first)
    )
    service.put(
        "actions/runs/100/artifacts?per_page=100&page=2", Artifacts(artifacts=artifacts)
    )
    with pytest.raises(Rejected, match="ambiguous artifact"):
        producer(service.api, 100)
    assert any(
        url.endswith("artifacts?per_page=100&page=2")
        for _method, url, _body in service.calls
    )


@pytest.mark.parametrize("operation", ["release", "pages"])
def test_manual_and_automatic_paths_cannot_use_producer_run_id(
    service: Service, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    assert invoke(monkeypatch, operation, "--run-id", "100") == 1


@pytest.mark.parametrize("fault", ["metadata", "typed", "legacy"])
@pytest.mark.parametrize("artifact", [WHEEL, SDIST])
def test_distribution_checks_read_contents_not_filenames(
    service: Service, fault: str, artifact: str
) -> None:
    files = dict(service.material)
    contents = {
        "pywhales/py.typed": b"",
        "pywhales-0.6.1.dist-info/METADATA": b"Name: pywhales\nVersion: 0.6.1\n",
    }
    match fault:
        case "metadata":
            contents["pywhales-0.6.1.dist-info/METADATA"] = (
                b"Name: pywhales\nVersion: 0.6.2\n"
            )
        case "typed":
            del contents["pywhales/py.typed"]
        case "legacy":
            contents["python_on_whales/__init__.py"] = b""
    if artifact == WHEEL:
        files[artifact] = zipped(contents)
    else:
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode="w:gz") as archive:
            for filename, data in contents.items():
                path = "PKG-INFO" if filename.endswith("/METADATA") else filename
                member = tarfile.TarInfo(f"pywhales-0.6.1/{path}")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        files[artifact] = result.getvalue()
    with pytest.raises(Rejected):
        inspect(files, "0.6.1")


@pytest.mark.parametrize("build_sidecar", [False, True])
def test_smoke_uses_separate_isolated_installs_outside_source(
    service: Service,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_sidecar: bool,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.1"\n')
    (tmp_path / "dist").mkdir()
    for filename, data in service.material.items():
        (tmp_path / "dist" / filename).write_bytes(data)
    if build_sidecar:
        (tmp_path / "dist" / ".gitignore").write_text("*\n")
    calls: list[tuple[list[str], str]] = []

    def install(
        command: list[str], *, cwd: str, check: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert check and timeout > 0
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", install)
    smoke(tmp_path)
    assert {
        Path(command[command.index("--with") + 1]).name for command, _cwd in calls
    } == {WHEEL, SDIST}
    assert all(
        "-I" in command
        and "--isolated" in command
        and "--no-project" in command
        and not Path(cwd).is_relative_to(tmp_path)
        for command, cwd in calls
    )


@pytest.mark.parametrize(
    "extra", ["unexpected.txt", ".unexpected", "pywhales-0.6.0.tar.gz"]
)
def test_smoke_rejects_unexpected_build_files(tmp_path: Path, extra: str) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.1"\n')
    (tmp_path / "dist").mkdir()
    for filename, data in distributions().items():
        (tmp_path / "dist" / filename).write_bytes(data)
    (tmp_path / "dist" / extra).write_bytes(b"unexpected")

    with pytest.raises(Rejected, match="distribution filenames"):
        smoke(tmp_path)


def test_distribution_inspection_rejects_build_sidecar() -> None:
    files = distributions() | {".gitignore": b"*\n"}

    with pytest.raises(Rejected, match="distribution filenames"):
        inspect(files, "0.6.1")


@pytest.mark.parametrize("checkout_import", [False, True])
def test_smoke_checks_actual_uv_installs_with_cache_backed_module_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checkout_import: bool
) -> None:
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    record = tmp_path / "imports.jsonl"
    monkeypatch.setenv("DELIVERY_SMOKE_RECORD", str(record))
    package = (
        b"import json,os,pathlib,sys\n"
        b"with open(os.environ['DELIVERY_SMOKE_RECORD'], 'a') as record:\n"
        b" record.write(json.dumps({'prefix':sys.prefix,'module':str(pathlib.Path(__file__).resolve())})+'\\n')\n"
        b"class DockerClient: pass\n"
    )
    contents = {
        "pywhales/__init__.py": package,
        "pywhales/py.typed": b"",
        "pywhales-0.6.1.dist-info/METADATA": b"Metadata-Version: 2.1\nName: pywhales\nVersion: 0.6.1\n",
        "pywhales-0.6.1.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if checkout_import:
        (tmp_path / "pywhales").mkdir()
        (tmp_path / "pywhales" / "__init__.py").write_bytes(package)
        contents["checkout.pth"] = (
            f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n"
        ).encode()
    contents["pywhales-0.6.1.dist-info/RECORD"] = "".join(
        f"{filename},,\n" for filename in contents
    ).encode()
    wheel = zipped(contents)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / WHEEL).write_bytes(wheel)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.1"\n')
    backend = (
        "import base64,pathlib\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        f" pathlib.Path(wheel_directory, {WHEEL!r}).write_bytes(base64.b64decode({base64.b64encode(wheel)!r}))\n"
        f" return {WHEEL!r}\n"
    ).encode()
    with tarfile.open(tmp_path / "dist" / SDIST, mode="w:gz") as archive:
        for filename, data in {
            "pywhales/__init__.py": package,
            "pywhales/py.typed": b"",
            "PKG-INFO": contents["pywhales-0.6.1.dist-info/METADATA"],
            "pyproject.toml": b'[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
            "backend.py": backend,
        }.items():
            member = tarfile.TarInfo(f"pywhales-0.6.1/{filename}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

    if checkout_import:
        with pytest.raises(subprocess.CalledProcessError):
            smoke(tmp_path)
        imported = [json.loads(line) for line in record.read_text().splitlines()]
        assert len(imported) == 1
        assert Path(imported[0]["module"]).is_relative_to(tmp_path.resolve())
    else:
        smoke(tmp_path)
        imported = [json.loads(line) for line in record.read_text().splitlines()]
        assert len(imported) == 2
        assert all(
            not Path(item["module"]).is_relative_to(Path(item["prefix"]).resolve())
            and not Path(item["module"]).is_relative_to(tmp_path.resolve())
            for item in imported
        )


@pytest.mark.parametrize("suffix", ["@main", "@refs/heads/main"])
def test_documented_run_path_ref_forms_remain_authoritatively_bound(
    service: Service, suffix: str
) -> None:
    for run in (service.source, service.publisher):
        scoped = run.model_copy(update={"path": run.path + suffix})
        service.put(f"actions/runs/{run.id}", scoped)
        service.put(f"actions/runs/{run.id}/attempts/1", scoped)
    service.receipted()
    assert receipt(service.api, 200).seal == service.tested


def test_actual_pages_tar_preserves_hidden_files_and_all_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPYFILE_DISABLE", "1")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_bytes(b"exact tested site")
    (site / ".well-known").mkdir()
    (site / ".well-known" / "record").write_bytes(b"hidden but tested")
    archive = tmp_path / "artifact.tar"
    subprocess.run(
        ["tar", "-cf", str(archive), "-C", str(site), "."], check=True, timeout=10
    )
    verify_pages_tar(site, archive)
    (site / ".well-known" / "record").write_bytes(b"changed")
    with pytest.raises(Rejected, match="differs from tested site"):
        verify_pages_tar(site, archive)


def test_tag_mutation_during_creation_stays_draft_and_rejects(service: Service) -> None:
    service.receipted()
    document = receipt(service.api, 200)
    service.put(
        "git/ref/tags/v0.6.1",
        GitRef(object=GitObject(type="commit", sha=service.source.head_sha)),
    )
    original = service.request
    writes: list[str] = []

    class TagMutation(Transport):
        @override
        def request(self, method: str, url: str, body: bytes | None = None) -> Response:
            if method == "POST" and url == BASE_URL + "releases":
                assert body is not None and json.loads(body)["draft"] is True
                writes.append(method)
                service.released("c" * 40)
                service.put(
                    "releases/tags/v0.6.1",
                    Release(id=1, tag_name="v0.6.1", draft=True, prerelease=False),
                )
                return Response(
                    201,
                    Release(id=1, tag_name="v0.6.1", draft=True, prerelease=False)
                    .model_dump_json()
                    .encode(),
                )
            if method == "PATCH":
                writes.append(method)
            return original(method, url, body)

    with pytest.raises(Rejected, match="tag commit mismatch"):
        release(GitHub(TagMutation(), "owner/repo", 1), document)
    assert writes == ["POST"]


@pytest.mark.parametrize("timeout", [False, True])
def test_http_transport_failures_never_expose_authorization(
    monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    monkeypatch.setenv("GH_TOKEN", "fixture-secret-token")

    script = "import sys; sys.stderr.write('fixture-secret-token'); sys.exit(7)"
    processes, _commands = controlled_curl(monkeypatch, script)
    if timeout:
        clock = iter((0.0, 131.0))
        monkeypatch.setattr(http.time, "monotonic", lambda: next(clock))
    message = "HTTP transport timed out" if timeout else "HTTP transport failed"
    with pytest.raises(Rejected, match=message) as rejected:
        Curl().request("GET", BASE_URL + "actions/runs/100")
    assert "fixture-secret-token" not in str(rejected.value)
    assert processes[0].returncode is not None
    assert processes[0].stdout is not None and processes[0].stdout.closed


def controlled_curl(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> tuple[list[subprocess.Popen[bytes]], list[list[str]]]:
    processes: list[subprocess.Popen[bytes]] = []
    commands: list[list[str]] = []
    launch = subprocess.Popen

    def start(
        command: list[str], *, stdin: BinaryIO, stdout: int, stderr: int
    ) -> subprocess.Popen[bytes]:
        assert stdout == subprocess.PIPE and stderr == subprocess.DEVNULL
        commands.append(command)
        process = launch(
            [sys.executable, "-c", script], stdin=stdin, stdout=stdout, stderr=stderr
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    return processes, commands


@pytest.mark.parametrize("framing", ["unknown", "dishonest"])
@pytest.mark.parametrize("size", [0, 32, 33])
def test_http_counts_received_body_without_trusting_length(
    monkeypatch: pytest.MonkeyPatch, framing: str, size: int
) -> None:
    payload = b"x" * size

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(206)
            if framing == "dishonest":
                self.send_header("Content-Length", "1")
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if framing == "dishonest":
                if payload:
                    self.wfile.write(
                        f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
                    )
                self.wfile.write(b"0\r\n\r\n")
            else:
                self.wfile.write(payload)

        @override
        def log_message(self, format: str, *args: str | int) -> None:
            return

    launch = subprocess.Popen
    processes: list[subprocess.Popen[bytes]] = []
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()

        def start(
            command: list[str], *, stdin: BinaryIO, stdout: int, stderr: int
        ) -> subprocess.Popen[bytes]:
            assert command[command.index("--proto") + 1] == "=https"
            assert command[command.index("--proto-redir") + 1] == "=https"
            assert command[command.index("--max-time") + 1] == "120"
            assert command[command.index("--connect-timeout") + 1] == "10"
            local_command = list(command)
            local_command[local_command.index("--proto") + 1] = "=http"
            local_command[-1] = f"http://127.0.0.1:{server.server_port}/"
            process = launch(local_command, stdin=stdin, stdout=stdout, stderr=stderr)
            processes.append(process)
            return process

        monkeypatch.setattr(subprocess, "Popen", start)
        monkeypatch.setattr(limits, "RESPONSE_BYTES", 32)
        try:
            if size > 32:
                with pytest.raises(Rejected, match="byte budget"):
                    Curl().request("GET", "https://delivery.invalid/")
            else:
                assert Curl().request("GET", "https://delivery.invalid/") == Response(
                    206, payload
                )
        finally:
            server.shutdown()
            worker.join(timeout=5)
        assert not worker.is_alive()
    assert processes[0].returncode is not None
    assert processes[0].stdout is not None and processes[0].stdout.closed


def test_curl_discards_stderr_and_preserves_binary_body_status_and_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"\x00\n404\n"
    monkeypatch.setenv("GH_TOKEN", "fixture-secret-token")
    monkeypatch.setattr(limits, "RESPONSE_BYTES", len(payload))
    processes, commands = controlled_curl(
        monkeypatch,
        "import sys; sys.stderr.buffer.write(b'e'*2097152); "
        "sys.stdout.buffer.write(sys.stdin.buffer.read()+b'\\n201')",
    )
    for url in (
        BASE_URL,
        "https://pypi.org/fixture",
        "https://api.github.com.evil/fixture",
    ):
        assert Curl().request("POST", url, payload) == Response(201, payload)
    assert "Authorization: Bearer fixture-secret-token" in commands[0]
    assert all(
        not any("Authorization:" in arg for arg in command) for command in commands[1:]
    )
    assert all(process.returncode == 0 for process in processes)


def test_curl_budget_abort_kills_and_reaps_live_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "RESPONSE_BYTES", 8)
    processes, _commands = controlled_curl(
        monkeypatch, "import os\nwhile True: os.write(1,b'x'*4096)"
    )

    with pytest.raises(Rejected, match="byte budget"):
        Curl().request("GET", "https://delivery.invalid/")

    assert processes[0].returncode is not None and processes[0].returncode < 0
    assert processes[0].stdout is not None and processes[0].stdout.closed


def test_curl_read_error_kills_and_reaps_live_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes, _commands = controlled_curl(
        monkeypatch, "import os\nwhile True: os.write(1,b'x'*4096)"
    )
    original_read = http.os.read

    def fail(fd: int, size: int) -> bytes:
        if processes:
            raise OSError("fixture read failure")
        return original_read(fd, size)

    monkeypatch.setattr(http.os, "read", fail)

    with pytest.raises(Rejected, match="HTTP transport failed"):
        Curl().request("GET", "https://delivery.invalid/")

    assert processes[0].returncode is not None and processes[0].returncode < 0
    assert processes[0].stdout is not None and processes[0].stdout.closed


@pytest.mark.parametrize("suffix", [b"", b"\n20", b"\nwat", b"x200"])
def test_curl_rejects_malformed_status_without_partial_response(
    monkeypatch: pytest.MonkeyPatch, suffix: bytes
) -> None:
    processes, _commands = controlled_curl(
        monkeypatch, f"import sys; sys.stdout.buffer.write({suffix!r})"
    )
    with pytest.raises(Rejected, match="invalid status"):
        Curl().request("GET", "https://delivery.invalid/")
    assert processes[0].returncode == 0


def tarred(files: dict[str, bytes], *, compressed: bool) -> bytes:
    result = io.BytesIO()
    with (
        tarfile.open(fileobj=result, mode="w:gz")
        if compressed
        else tarfile.open(fileobj=result, mode="w:")
    ) as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return result.getvalue()


def sized_distribution(kind: str) -> tuple[dict[str, bytes], dict[str, bytes]]:
    contents = {"pywhales/py.typed": b"", "payload": b"x" * 128, "second": b"y" * 64}
    metadata = b"Name: pywhales\nVersion: 0.6.1\n"
    files = distributions()
    if kind == "wheel":
        contents["pywhales-0.6.1.dist-info/METADATA"] = metadata
        files[WHEEL] = zipped(contents, compression=zipfile.ZIP_DEFLATED)
    else:
        contents["PKG-INFO"] = metadata
        contents = {f"pywhales-0.6.1/{name}": data for name, data in contents.items()}
        files[SDIST] = tarred(contents, compressed=True)
    return files, contents


@pytest.mark.parametrize("kind", ["artifact", "wheel", "sdist", "pages"])
@pytest.mark.parametrize("axis", ["entries", "member_bytes", "expanded_bytes"])
@pytest.mark.parametrize("excess", [False, True])
def test_all_archive_entries_accept_exact_budgets_and_reject_first_excess(
    service: Service,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    axis: str,
    excess: bool,
) -> None:
    contents = {"index.html": b"x" * 128, "second": b"y" * 64}
    files = distributions()
    if kind in {"wheel", "sdist"}:
        files, contents = sized_distribution(kind)
    policy = {
        "entries": len(contents),
        "member_bytes": max(map(len, contents.values())),
        "expanded_bytes": sum(map(len, contents.values())),
    }
    policy[axis] -= int(excess)
    bound = limits.ArchiveLimits(**policy)
    monkeypatch.setattr(
        limits,
        {
            "artifact": "ARTIFACT",
            "wheel": "DISTRIBUTION",
            "sdist": "DISTRIBUTION",
            "pages": "SITE",
        }[kind],
        bound,
    )
    artifact = service.artifact(55, "site", service.source, contents)
    wrapped = zipped(contents, compression=zipfile.ZIP_DEFLATED)
    artifact = artifact.model_copy(update={"digest": f"sha256:{sha256(wrapped)}"})
    service.routes[("GET", BASE_URL + "actions/artifacts/55/zip")] = Response(
        200, wrapped
    )
    site = tmp_path / "site"
    site.mkdir()
    for filename, data in contents.items():
        target = site / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive = tmp_path / "artifact.tar"
    archive.write_bytes(tarred(contents, compressed=False))

    def verify() -> None:
        if kind == "artifact":
            assert download(service.api, artifact) == contents
        elif kind in {"wheel", "sdist"}:
            inspect(files, "0.6.1")
        else:
            verify_pages_tar(site, archive)

    if excess:
        with pytest.raises(Rejected, match="budget exceeded"):
            verify()
    else:
        verify()


@pytest.mark.parametrize("kind", ["artifact", "wheel", "sdist", "pages"])
def test_dishonest_member_metadata_cannot_bypass_actual_expansion(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    contents = {"payload": b"x"}
    files = distributions()
    if kind in {"wheel", "sdist"}:
        files, contents = sized_distribution(kind)
        contents = {
            name: b"x" if name.endswith("payload") else data
            for name, data in contents.items()
        }
        files[WHEEL if kind == "wheel" else SDIST] = (
            zipped(contents) if kind == "wheel" else tarred(contents, compressed=True)
        )
    artifact = service.artifact(55, "site", service.source, contents)
    archive_path = tmp_path / "artifact.tar"
    archive_path.write_bytes(tarred(contents, compressed=False))
    site = tmp_path / "site"
    site.mkdir()
    (site / "payload").write_bytes(b"x")
    monkeypatch.setattr(limits, "ARTIFACT", limits.ArchiveLimits(100, 127, 1024))
    monkeypatch.setattr(limits, "DISTRIBUTION", limits.ArchiveLimits(100, 127, 1024))
    monkeypatch.setattr(limits, "SITE", limits.ArchiveLimits(100, 127, 1024))
    zip_open = zipfile.ZipFile.open
    tar_open = tarfile.TarFile.extractfile

    def zip_stream(package: zipfile.ZipFile, entry: zipfile.ZipInfo) -> IO[bytes]:
        if entry.filename.endswith("payload"):
            return io.BytesIO(b"x" * 128)
        return zip_open(package, entry)

    def tar_stream(
        package: tarfile.TarFile, member: tarfile.TarInfo
    ) -> IO[bytes] | None:
        if member.name.endswith("payload"):
            return io.BytesIO(b"x" * 128)
        return tar_open(package, member)

    monkeypatch.setattr(zipfile.ZipFile, "open", zip_stream)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", tar_stream)

    with pytest.raises(Rejected, match="input byte budget exceeded"):
        if kind == "artifact":
            download(service.api, artifact)
        elif kind in {"wheel", "sdist"}:
            inspect(files, "0.6.1")
        else:
            verify_pages_tar(site, archive_path)


@pytest.mark.parametrize("kind", ["project", "wheel", "sdist"])
@pytest.mark.parametrize("excess", [False, True])
def test_local_smoke_bounds_each_input_before_isolated_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, excess: bool
) -> None:
    files = distributions()
    project = ('[project]\nversion = "0.6.1"\n# ' + "é" * 64 + "\n").encode()
    selected = {"project": "pyproject.toml", "wheel": WHEEL, "sdist": SDIST}[kind]
    if kind != "project":
        files[selected] += b"\0" * 8192
    (tmp_path / "pyproject.toml").write_bytes(project)
    (tmp_path / "dist").mkdir()
    for filename, data in files.items():
        (tmp_path / "dist" / filename).write_bytes(data)
    monkeypatch.setattr(
        limits,
        "PROJECT_BYTES" if kind == "project" else "DISTRIBUTION_BYTES",
        len(project if kind == "project" else files[selected]) - int(excess),
    )
    installed: list[list[str]] = []

    def install(
        command: list[str], *, cwd: str, check: bool, timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        installed.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", install)

    if excess:
        with pytest.raises(Rejected, match="input byte budget exceeded"):
            smoke(tmp_path)
        assert installed == []
    else:
        smoke(tmp_path)
        assert len(installed) == 2


@pytest.mark.parametrize("excess", [False, True])
def test_sealer_independently_bounds_project_configuration(
    service: Service,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    excess: bool,
) -> None:
    project = ('[project]\nversion = "0.6.1"\n# ' + "é" * 64 + "\n").encode()
    (tmp_path / "pyproject.toml").write_bytes(project)
    monkeypatch.setattr(limits, "PROJECT_BYTES", len(project) - int(excess))
    monkeypatch.setenv("GITHUB_SHA", service.source.head_sha)
    for kind, artifact in zip(("DIST", "SITE"), service.artifacts()[:2], strict=True):
        monkeypatch.setenv(f"{kind}_ARTIFACT_ID", str(artifact.id))
        monkeypatch.setenv(
            f"{kind}_ARTIFACT_DIGEST", artifact.digest.removeprefix("sha256:")
        )

    if excess:
        with pytest.raises(Rejected, match="input byte budget exceeded"):
            seal(service.api, 100, tmp_path)
    else:
        assert seal(service.api, 100, tmp_path) == service.tested


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("excess", [False, True])
def test_distribution_inspection_independently_bounds_compressed_inputs(
    monkeypatch: pytest.MonkeyPatch, kind: str, excess: bool
) -> None:
    files = distributions()
    selected = WHEEL if kind == "wheel" else SDIST
    files[selected] += b"\0" * 8192
    monkeypatch.setattr(
        limits, "DISTRIBUTION_BYTES", len(files[selected]) - int(excess)
    )

    if excess:
        with pytest.raises(Rejected, match="distribution input byte budget"):
            inspect(files, "0.6.1")
    else:
        inspect(files, "0.6.1")


@pytest.mark.parametrize("excess", [False, True])
def test_pages_independently_bounds_actual_local_tar_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excess: bool
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_bytes(b"site")
    data = tarred({"index.html": b"site"}, compressed=False)
    archive = tmp_path / "artifact.tar"
    archive.write_bytes(data)
    monkeypatch.setattr(limits, "PAGES_TAR_BYTES", len(data) - int(excess))

    if excess:
        with pytest.raises(Rejected, match="input byte budget exceeded"):
            verify_pages_tar(site, archive)
    else:
        verify_pages_tar(site, archive)


@pytest.mark.parametrize("axis", ["entries", "member_bytes", "expanded_bytes"])
def test_pages_reference_site_has_its_own_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_bytes(b"x" * 8)
    (site / "second").write_bytes(b"y" * 8)
    data = tarred({"index.html": b"", "second": b""}, compressed=False)
    archive = tmp_path / "artifact.tar"
    archive.write_bytes(data)
    policy = {"entries": 2, "member_bytes": 8, "expanded_bytes": 16}
    policy[axis] -= 1
    monkeypatch.setattr(limits, "SITE", limits.ArchiveLimits(**policy))

    if axis == "entries":
        archive.write_bytes(tarred({"index.html": b""}, compressed=False))
    with pytest.raises(Rejected, match="budget exceeded"):
        verify_pages_tar(site, archive)


def test_bounded_read_stops_on_first_excess_without_unbounded_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Source(io.BytesIO):
        @override
        def read(self, size: int | None = -1, /) -> bytes:
            assert size is not None and 0 < size <= 5 - self.tell()
            return super().read(min(size, 2))

    source = Source(b"abcdefgh" * 1000)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: source)

    with pytest.raises(Rejected, match="input byte budget exceeded"):
        budgets.local(tmp_path / "growing-file", 4)
    assert source.closed


def test_digest_rejection_precedes_any_zip_member_trust(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    def decode(data: bytes, policy: limits.ArchiveLimits) -> dict[str, bytes]:
        pytest.fail("member parser used before digest verification")

    artifact = service.artifacts()[0].model_copy(
        update={"digest": f"sha256:{'0' * 64}"}
    )
    monkeypatch.setattr(artifacts, "zip_files", decode)

    with pytest.raises(Rejected, match="archive digest mismatch"):
        download(service.api, artifact)


def test_rejected_extraction_does_not_publish_partial_files(tmp_path: Path) -> None:
    destination = tmp_path / "output"

    with pytest.raises(OSError):
        artifacts.extract(
            {"first": b"accepted", "first/nested": b"conflict"}, destination
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["artifact", "wheel", "sdist", "pages"])
@pytest.mark.parametrize("fault", ["malformed", "path", "duplicate", "special"])
def test_all_archive_entry_points_reject_unsafe_or_ambiguous_members(
    service: Service, tmp_path: Path, kind: str, fault: str
) -> None:
    result = io.BytesIO()
    name = "../escape" if fault == "path" else "payload"
    if kind in {"artifact", "wheel"}:
        with zipfile.ZipFile(result, "w") as package:
            entry = zipfile.ZipInfo(name)
            if fault == "special":
                entry.create_system = 3
                entry.external_attr = 0o120777 << 16
            package.writestr(entry, b"data")
            if fault == "duplicate":
                with pytest.warns(UserWarning):
                    package.writestr(name, b"second")
    else:
        with (
            tarfile.open(fileobj=result, mode="w:gz")
            if kind == "sdist"
            else tarfile.open(fileobj=result, mode="w:")
        ) as tar:
            entry = tarfile.TarInfo(name)
            if fault == "special":
                entry.type = tarfile.SYMTYPE
                entry.linkname = "target"
            tar.addfile(entry)
            if fault == "duplicate":
                tar.addfile(tarfile.TarInfo(name))
    data = b"broken" if fault == "malformed" else result.getvalue()
    artifact = service.artifact(55, "site", service.source, {"placeholder": b""})
    artifact = artifact.model_copy(update={"digest": f"sha256:{sha256(data)}"})
    service.routes[("GET", BASE_URL + "actions/artifacts/55/zip")] = Response(200, data)
    files = distributions() | {WHEEL if kind == "wheel" else SDIST: data}
    site = tmp_path / "site"
    site.mkdir()
    (site / "payload").write_bytes(b"")
    archive = tmp_path / "artifact.tar"
    archive.write_bytes(data)
    output = tmp_path / "output"

    reason = {
        "malformed": "invalid .* archive",
        "path": "unsafe archive path",
        "duplicate": "duplicate archive path",
        "special": "archive special file",
    }[fault]
    with pytest.raises(Rejected, match=reason):
        if kind == "artifact":
            artifacts.extract(download(service.api, artifact), output)
        elif kind in {"wheel", "sdist"}:
            inspect(files, "0.6.1")
        else:
            verify_pages_tar(site, archive)
    assert not output.exists()


@pytest.mark.parametrize("tar", [False, True])
@pytest.mark.parametrize("excess", [False, True])
def test_directory_entries_count_even_without_payload(tar: bool, excess: bool) -> None:
    result = io.BytesIO()
    if tar:
        with tarfile.open(fileobj=result, mode="w:") as package:
            for name in ("first", "second"):
                entry = tarfile.TarInfo(name)
                entry.type = tarfile.DIRTYPE
                package.addfile(entry)
    else:
        with zipfile.ZipFile(result, "w") as package:
            for name in ("first/", "second/"):
                package.writestr(name, b"")
    policy = limits.ArchiveLimits(2 - int(excess), 0, 0)

    def decode() -> dict[str, bytes]:
        if tar:
            return archives.tar_files(result.getvalue(), policy, pages=True)
        return archives.zip_files(result.getvalue(), policy)

    if excess:
        with pytest.raises(Rejected, match="entry budget exceeded"):
            decode()
    else:
        assert decode() == {}


def test_artifact_response_budget_is_independent_of_transport(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = service.artifact(55, "site", service.source, {"payload": b""})
    data = service.routes[("GET", BASE_URL + "actions/artifacts/55/zip")].body
    monkeypatch.setattr(limits, "RESPONSE_BYTES", len(data))
    assert download(service.api, artifact) == {"payload": b""}
    monkeypatch.setattr(limits, "RESPONSE_BYTES", len(data) - 1)

    with pytest.raises(Rejected, match="artifact input byte budget"):
        download(service.api, artifact)


def test_zip_nul_name_cannot_be_truncated_into_a_trusted_path() -> None:
    data = zipped({"aabb": b"data"}).replace(b"aabb", b"aa\0b")

    with pytest.raises(Rejected, match="unsafe archive path"):
        archives.zip_files(data, limits.ARTIFACT)


@pytest.mark.parametrize("tar", [False, True])
def test_high_ratio_compressed_members_exceed_small_expansion_budget(tar: bool) -> None:
    contents = {"payload": b"x" * (2 * 1024 * 1024)}
    data = (
        tarred(contents, compressed=True)
        if tar
        else zipped(contents, compression=zipfile.ZIP_DEFLATED)
    )
    assert len(data) < 4096
    policy = limits.ArchiveLimits(10, 65536, 65536)

    with pytest.raises(Rejected, match="byte budget exceeded"):
        if tar:
            archives.tar_files(data, policy)
        else:
            archives.zip_files(data, policy)


def test_pages_rejects_reference_symlink(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_bytes(b"data")
    archive = tmp_path / "artifact.tar"
    archive.write_bytes(tarred({"index.html": b"data"}, compressed=False))

    with pytest.raises(Rejected, match="Pages site special file"):
        verify_pages_tar(site, archive)
