"""Bounded curl transport and paginated typed REST access."""

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Protocol, override

from pydantic import BaseModel, JsonValue, TypeAdapter

from .models import Repository


@dataclass(slots=True)
class Rejected(Exception):
    """Fail closed; exception traceback metadata must remain mutable."""

    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def require(condition: bool, reason: str) -> None:
    """Reject an unsatisfied trust invariant."""
    if not condition:
        raise Rejected(reason)


@dataclass(frozen=True, slots=True)
class Response:
    """HTTP status and undecoded body."""

    status: int
    body: bytes


class Transport(Protocol):
    """HTTP boundary replaced by inert fixtures in local tests."""

    def request(self, method: str, url: str, body: bytes | None = None) -> Response: ...


class Curl:
    """Use runner curl without shell interpolation or cross-host credentials."""

    def request(self, method: str, url: str, body: bytes | None = None) -> Response:
        """Return a bounded HTTPS response; curl drops auth on host redirects."""
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--connect-timeout",
            "10",
            "--max-time",
            "120",
            "--request",
            method,
            "--write-out",
            "\n%{http_code}",
        ]
        if url.startswith("https://api.github.com/"):
            command += [
                "--header",
                f"Authorization: Bearer {os.environ['GH_TOKEN']}",
                "--header",
                "X-GitHub-Api-Version: 2022-11-28",
            ]
        if body is not None:
            command += [
                "--header",
                "Content-Type: application/json",
                "--data-binary",
                "@-",
            ]
        try:
            result = subprocess.run(
                [*command, url],
                input=body,
                capture_output=True,
                check=False,
                timeout=130,
            )
        except subprocess.TimeoutExpired:
            raise Rejected("HTTP transport timed out") from None
        require(result.returncode == 0, "HTTP transport failed")
        content, status = result.stdout.rsplit(b"\n", 1)
        return Response(int(status), content)


@dataclass(frozen=True, slots=True)
class GitHub:
    """Repository-scoped API access with an externally anchored repository ID."""

    transport: Transport
    repository: str
    repository_id: int

    def response(
        self, path: str, body: bytes | None = None, *, method: str | None = None
    ) -> Response:
        """Request one repository endpoint."""
        return self.transport.request(
            method or ("GET" if body is None else "POST"),
            f"https://api.github.com/repos/{self.repository}/{path}",
            body,
        )

    def get[T: BaseModel](self, path: str, model: type[T]) -> T:
        """Parse a required successful response at the boundary."""
        response = self.response(path)
        require(response.status == 200, f"github response: {path}: {response.status}")
        return model.model_validate_json(response.body)

    def pages[T: BaseModel](self, path: str, model: type[T]) -> tuple[T, ...]:
        """Fetch every page, including bare-array endpoints."""
        items: list[T] = []
        page = 1
        while True:
            response = self.response(f"{path}?per_page=100&page={page}")
            require(response.status == 200, "pagination response")
            raw = TypeAdapter(list[JsonValue]).validate_json(response.body)
            batch = [model.model_validate(item) for item in raw]
            items.extend(batch)
            if len(batch) < 100:
                return tuple(items)
            page += 1

    def identity(self) -> Repository:
        """Anchor authority in the invoking repository, never artifact metadata."""
        repo = self.get("", Repository)
        require(
            repo.id == self.repository_id
            and repo.full_name == self.repository
            and repo.default_branch == "main",
            "repository identity",
        )
        return repo


def json_body(value: JsonValue) -> bytes:
    """Serialize a REST request without executing supplied content."""
    return json.dumps(value).encode()
