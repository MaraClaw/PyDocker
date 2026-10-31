"""Versioned canonical intent containing references, never arbitrary payloads."""

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Final, Literal

from pywhales._json import dumps_compat
from pywhales.admission import EndpointIdentity
from pywhales.reconciliation._errors import InvalidDescription

HASH_VERSION: Final = "pywhales.desired.v1"
type ResourceClass = Literal[
    "borrowed", "persistent", "ephemeral", "rollback-protected"
]


def token(value: str, *, max_length: int = 128) -> None:
    """Reject values outside the bounded non-confidential metadata vocabulary."""
    if re.fullmatch(rf"[A-Za-z0-9][A-Za-z0-9_.-]{{0,{max_length - 1}}}", value) is None:
        raise InvalidDescription()


@dataclass(frozen=True, slots=True)
class InputReference:
    """Immutable SHA-256 reference to externally approved input, not its payload.

    The trusted resolver verifies this digest before use. Do not hash a naked
    low-entropy secret as approved input; secret values live in a secret provider.
    """

    sha256: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise InvalidDescription()


@dataclass(frozen=True, slots=True)
class SecretReference:
    """Public secret-provider alias and immutable version; never a secret value."""

    name: str
    version: str

    def __post_init__(self) -> None:
        token(self.name)
        token(self.version)


@dataclass(frozen=True, slots=True, kw_only=True)
class DesiredDescription:
    """Typed description of externally approved desired input.

    No command, environment, free-form JSON, task template or plaintext secret
    belongs here. The approved input defines all detailed mutation parameters.
    Secret references form a set; their order is not material to the desired hash.
    """

    approved_input: InputReference
    owner: str
    resource_class: ResourceClass
    target: str
    secrets: tuple[SecretReference, ...] = ()

    def __post_init__(self) -> None:
        token(self.owner, max_length=64)
        token(self.target)
        if self.resource_class not in {
            "borrowed",
            "persistent",
            "ephemeral",
            "rollback-protected",
        } or len({reference.name for reference in self.secrets}) != len(self.secrets):
            raise InvalidDescription()
        object.__setattr__(
            self, "secrets", tuple(sorted(self.secrets, key=lambda ref: ref.name))
        )

    @property
    def desired_hash(self) -> str:
        """Return the versioned SHA-256 of canonical, secret-free metadata."""
        canonical = dumps_compat(
            {"schema": HASH_VERSION, "desired": asdict(self)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return f"{HASH_VERSION}:sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True, slots=True)
class OperationKey:
    """Stable endpoint alias and operation token; aliases must map consistently."""

    endpoint: EndpointIdentity
    operation: str

    def __post_init__(self) -> None:
        token(self.operation, max_length=64)


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """Immutable operation identity plus canonical desired description."""

    key: OperationKey
    desired: DesiredDescription
