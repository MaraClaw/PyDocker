"""Immutable convergence targets and caller-visible outcomes."""

from dataclasses import dataclass, field
from typing import Literal

from pywhales.client_config import ClientConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceTarget:
    """A captured service generation and its originating client configuration."""

    client_config: ClientConfig = field(repr=False, compare=False)
    service_id: str
    version: int
    fingerprint: str
    template: str = field(repr=False)
    replicas: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StackTarget:
    """An explicit, nonempty and fixed set of service generations."""

    services: tuple[ServiceTarget, ...]

    def __post_init__(self) -> None:
        """Reject empty or duplicate membership."""
        if not self.services or len(
            {target.service_id for target in self.services}
        ) != len(self.services):
            raise ValueError("Stack target must contain distinct services")


@dataclass(frozen=True, slots=True)
class Success:
    """All fixed services met the captured convergence conditions."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Failure:
    """Terminal outcome; the optional cause may contain confidential details."""

    reason: Literal[
        "unsupported", "malformed", "superseded", "update_failed", "observation"
    ]
    service_id: str | None = None
    state: str | None = None
    cause: Exception | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class Deadline:
    """The total observation deadline expired."""


@dataclass(frozen=True, slots=True)
class Cancelled:
    """An active cancellation token was set."""


type Outcome = Success | Failure | Deadline | Cancelled
