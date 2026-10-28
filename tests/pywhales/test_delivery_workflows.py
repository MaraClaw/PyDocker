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
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest
from pydantic import BaseModel, JsonValue

from scripts.delivery import __main__ as cli
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


def zipped(files: dict[str, bytes]) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
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
        self.routes[("GET", BASE_URL + path)] = Response(status, body)

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

    def fail(
        command: list[str],
        *,
        input: bytes | None,
        capture_output: bool,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output and not check and timeout > 0
        if timeout_failure:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 7, b"", b"failed")

    timeout_failure = timeout
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(Rejected, match="HTTP transport") as rejected:
        Curl().request("GET", BASE_URL + "actions/runs/100")
    assert "fixture-secret-token" not in str(rejected.value)
