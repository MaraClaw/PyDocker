"""Maintainer-owned version combinations and unresolved legacy policy."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from .common import (
    Contract,
    Decision,
    Nonempty,
    Pending,
    SchemaVersion,
    Text,
    Token,
    check_decisions,
)
from .platform import Orchestrator

type ExactVersion = Annotated[
    str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
]
type ExactKernel = Annotated[
    str, Field(pattern=r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
]


class Versions(Contract):
    """Exact observed versions; unknown values require owned decisions."""

    python: ExactVersion | Pending
    os: Text | Pending
    kernel: ExactKernel | Pending
    docker_cli: ExactVersion | Pending
    docker_engine: ExactVersion | Pending
    compose: ExactVersion | Pending | None
    buildx: ExactVersion | Pending | None

    @property
    def resolved(self) -> bool:
        """Return whether every applicable version is an observation, not a decision."""
        return not any(
            isinstance(value, Pending)
            for value in (
                self.python,
                self.os,
                self.kernel,
                self.docker_cli,
                self.docker_engine,
                self.compose,
                self.buildx,
            )
        )

    def plugins_present(
        self, orchestrator: Orchestrator, required_features: tuple[str, ...]
    ) -> bool:
        """Require explicit versions or decisions for explicitly applicable plugins."""
        return (
            not (orchestrator == "compose" or "compose" in required_features)
            or self.compose is not None
        ) and ("buildx" not in required_features or self.buildx is not None)


class SupportRow(Contract):
    """One maintained combination; CI configuration is not a passing receipt."""

    id: Token
    status: Literal["supported", "experimental", "untested"]
    provenance: Literal["configured_ci", "synthetic", "operator_declared"]
    versions: Versions
    orchestrator: Orchestrator
    required_features: Nonempty[Token]
    maintainer: Text
    approval_ref: Token | None
    evidence_refs: tuple[Token, ...]
    rationale: Text

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Reserve null plugin versions for plugins not applicable to this row."""
        if not self.versions.plugins_present(self.orchestrator, self.required_features):
            raise PydanticCustomError(
                "plugin_version_missing", "plugin_version_missing"
            )
        return self


class LegacyPolicy(Contract):
    """Proposal06 compatibility window and explicit removal decision."""

    compatibility_window: Text | Pending
    removal_decision: Text | Pending


class SupportMatrix(Contract):
    """Versioned contributor matrix, with no inferred support defaults."""

    schema_version: SchemaVersion
    maintainer: Text
    rows: Nonempty[SupportRow]
    legacy06: LegacyPolicy
    decisions: tuple[Decision, ...]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Require owned unknowns and explicit evidence for reviewed support rows."""
        check_decisions(self, self.decisions)
        if len({row.id for row in self.rows}) != len(self.rows):
            raise PydanticCustomError("support_duplicate", "support_duplicate")
        for row in self.rows:
            if row.status != "untested" and (
                row.provenance != "operator_declared"
                or row.approval_ref is None
                or not row.evidence_refs
                or not row.versions.resolved
            ):
                raise PydanticCustomError("support_unreviewed", "support_unreviewed")
        return self
