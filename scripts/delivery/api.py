"""Bounded curl transport and paginated typed REST access."""

import json
import os
import selectors
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Protocol, override

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from . import limits
from .models import Artifact, Job, PullNumber, Release, Repository


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
        return self.receive([*command, url], body)

    def receive(self, command: list[str], body: bytes | None) -> Response:
        """Drain stdout incrementally; discard stderr without buffering it.

        The four-byte curl status suffix has a separate framing allowance.
        Aborted receipt always kills and reaps the owned process, including on
        deadline expiry or exceptions. No command or stderr enters errors.
        """
        deadline = time.monotonic() + 130
        output = bytearray()
        # A file avoids blocking on a child that stops reading request stdin.
        with tempfile.TemporaryFile() as incoming:
            if body is not None:
                incoming.write(body)
                incoming.seek(0)
            try:
                with subprocess.Popen(
                    command,
                    stdin=incoming,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                ) as process:
                    try:
                        assert process.stdout is not None
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ)
                            while selector.get_map():
                                remaining = deadline - time.monotonic()
                                require(remaining > 0, "HTTP transport timed out")
                                require(
                                    bool(selector.select(remaining)),
                                    "HTTP transport timed out",
                                )
                                capacity = limits.RESPONSE_BYTES + 4 - len(output)
                                data = os.read(
                                    process.stdout.fileno(),
                                    min(limits.CHUNK_BYTES, capacity + 1),
                                )
                                require(
                                    len(data) <= capacity,
                                    "HTTP response byte budget exceeded",
                                )
                                if data:
                                    output.extend(data)
                                else:
                                    selector.unregister(process.stdout)
                        require(
                            process.wait(timeout=max(0, deadline - time.monotonic()))
                            == 0,
                            "HTTP transport failed",
                        )
                    finally:
                        if process.returncode is None:
                            process.kill()
                            process.wait()
            except subprocess.TimeoutExpired:
                raise Rejected("HTTP transport timed out") from None
            except OSError:
                raise Rejected("HTTP transport failed") from None
        require(
            len(output) >= 4 and output[-4] == 10 and output[-3:].isdigit(),
            "HTTP transport invalid status",
        )
        return Response(int(output[-3:]), bytes(output[:-4]))


def listed(model: type[BaseModel]) -> tuple[str, int]:
    """Return a listed model's identity field and its current item cap."""
    if model is Job:
        return "id", limits.JOB_ITEMS
    if model is Artifact:
        return "id", limits.ARTIFACT_ITEMS
    if model is Release:
        return "id", limits.RELEASE_ITEMS
    if model is PullNumber:
        return "number", limits.PULL_ITEMS
    raise Rejected("pagination model has no item cap")


def page_items(body: bytes, envelope: str | None) -> tuple[list[JsonValue], int | None]:
    """Split one page into raw items and its `total_count`, without echoing input."""
    try:
        if envelope is None:
            return TypeAdapter(list[JsonValue]).validate_json(body), None
        wrapper = TypeAdapter(dict[str, JsonValue]).validate_json(body)
    except ValidationError:
        raise Rejected("pagination response malformed") from None
    total, raw = wrapper.get("total_count"), wrapper.get(envelope)
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise Rejected("pagination total_count missing")
    if not isinstance(raw, list):
        raise Rejected("pagination response malformed")
    return raw, total


def parse_item[T: BaseModel](model: type[T], item: JsonValue) -> T:
    """Validate one listed item; the typed error would echo the rejected input."""
    try:
        return model.model_validate_json(json.dumps(item))
    except ValidationError:
        raise Rejected("pagination item malformed") from None


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
        suffix = f"/{path}" if path else ""
        return self.transport.request(
            method or ("GET" if body is None else "POST"),
            f"https://api.github.com/repos/{self.repository}{suffix}",
            body,
        )

    def get[T: BaseModel](self, path: str, model: type[T]) -> T:
        """Parse a required successful response at the boundary."""
        response = self.response(path)
        require(response.status == 200, f"github response: {path}: {response.status}")
        return model.model_validate_json(response.body)

    def pages[T: BaseModel](
        self, path: str, model: type[T], envelope: str | None = None
    ) -> tuple[T, ...]:
        """Fetch every page of a bounded listing, failing closed on any anomaly.

        Bare-array endpoints pass no envelope. Otherwise the response is an object
        whose `envelope` array holds the items and whose `total_count` must be
        present and stable, and must equal the final collected count. That count
        can only reject; the end of the listing is always the first short page.
        """
        key, cap = listed(model)
        items: list[T] = []
        seen: set[object] = set()
        received = 0
        total: int | None = None
        for page in range(1, cap // limits.PAGE_ITEMS + 2):
            response = self.response(f"{path}?per_page={limits.PAGE_ITEMS}&page={page}")
            require(response.status == 200, "pagination response")
            received += len(response.body)
            require(
                received <= limits.PAGINATION_BYTES, "pagination byte budget exceeded"
            )
            raw, count = page_items(response.body, envelope)
            if count is not None:
                require(total in {None, count}, "pagination total_count changed")
                total = count
            require(len(items) + len(raw) <= cap, "pagination item budget exceeded")
            batch = [parse_item(model, item) for item in raw]
            for item in batch:
                identity = getattr(item, key)
                require(identity not in seen, "pagination repeated item")
                seen.add(identity)
            items.extend(batch)
            if len(raw) < limits.PAGE_ITEMS:
                require(total in {None, len(items)}, "pagination total_count mismatch")
                return tuple(items)
        raise Rejected("pagination page budget exceeded")

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
