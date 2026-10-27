"""Strict simulation inputs and resolved contributor declarations."""

from typing import Final, Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from scripts.fleet_contract.common import (
    Amount,
    Contract,
    Decision,
    Nonempty,
    Positive,
    PositiveCount,
    SchemaVersion,
    Token,
)
from scripts.fleet_contract.manifest import Manifest
from scripts.fleet_contract.platform import Ownership, Platform, Topology
from scripts.fleet_contract.results import Outcome
from scripts.fleet_contract.safety import Authorization, Budgets
from scripts.fleet_contract.workload import Action, Identity, Measurements, Workload


class RunnableManifest(Contract):
    """Fully resolved declarations; never proof of permission or isolation."""

    schema_version: SchemaVersion
    state: Literal["runnable"]
    provenance: Literal["synthetic", "operator_declared"]
    identity: Identity
    support_row: Token
    workload: Workload
    platform: Platform
    topology: Topology
    ownership: Ownership
    measurements: Measurements
    budgets: Budgets
    authorization: Authorization
    decisions: tuple[Decision, ...]

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Run the authoritative cross-group validators without narrowing unions."""
        Manifest.model_validate_json(self.model_dump_json())
        return self


class Step(Contract):
    """One scripted fake operation; timings are seconds on the fake clock."""

    scenario: Token
    endpoint: Token
    action: Action
    asset: Token | None = None
    fault: Token | None = None
    outcome: Outcome = "succeeded"
    queue_seconds: Amount = 0.0
    local_seconds: Amount = 0.1
    readiness_seconds: Amount | None = 0.1
    recovery_seconds: Amount | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Require explicit fault identity and allocation slots for mutations."""
        if (self.action == "fault") != (self.fault is not None):
            raise PydanticCustomError("fault_identity", "fault_identity")
        if (self.action in {"create", "pull", "build", "start", "stop", "remove"}) != (
            self.asset is not None
        ):
            raise PydanticCustomError("asset_identity", "asset_identity")
        if self.action == "cleanup":
            raise PydanticCustomError("cleanup_reserved", "cleanup_reserved")
        if self.action != "fault" and self.recovery_seconds is not None:
            raise PydanticCustomError("recovery_population", "recovery_population")
        return self


type MetricName = Literal[
    "cpu_cores",
    "rss_bytes",
    "file_descriptors",
    "children",
    "disk_bytes",
    "disk_inodes",
    "network_bytes",
    "daemon_cpu_cores",
    "daemon_memory_bytes",
]

METRIC_NAMES: Final[tuple[MetricName, ...]] = (
    "cpu_cores",
    "rss_bytes",
    "file_descriptors",
    "children",
    "disk_bytes",
    "disk_inodes",
    "network_bytes",
    "daemon_cpu_cores",
    "daemon_memory_bytes",
)


class Metric(Contract):
    """A scripted resource high-water; null explicitly means unavailable."""

    name: MetricName
    value: Amount | None


class Plan(Contract):
    """Bounded simulation recipe, not a real adapter configuration."""

    provider: Literal["fake", "real"] = "fake"
    opt_in: bool = False
    repetitions: PositiveCount
    maximum_samples: PositiveCount
    operation_timeout_seconds: Positive
    steps: Nonempty[Step]
    metrics: tuple[Metric, ...] = ()
    cleanup_seconds_per_asset: Amount = 0.1
    cleanup_fail_assets: tuple[Token, ...] = ()

    @model_validator(mode="after")
    def bounded(self) -> Self:
        """Bound memory by an explicit sample cap and reject duplicate metrics."""
        if (
            sum(3 + (step.action == "fault") for step in self.steps) * self.repetitions
            > self.maximum_samples
        ):
            raise PydanticCustomError("sample_cap", "sample_cap")
        if len({metric.name for metric in self.metrics}) != len(self.metrics):
            raise PydanticCustomError("duplicate_metric", "duplicate_metric")
        return self


def authorize(manifest: RunnableManifest, plan: Plan) -> tuple[str, ...]:
    """Check every declaration before constructing or calling the fake provider."""
    reasons: list[str] = []
    if not plan.opt_in:
        reasons.append("opt_in_required")
    if plan.provider != "fake":
        reasons.append("real_adapter_unqualified")
    auth = manifest.authorization
    aliases = {
        alias.name: alias.effective_endpoint for alias in manifest.platform.aliases
    }
    removable = {
        asset.id
        for asset in manifest.ownership.assets
        if asset.ownership == "created"
        and asset.data == "ephemeral"
        and asset.teardown == "remove_owned"
    }
    declared = {
        operation.action
        for operation in manifest.workload.operation_mix
        if operation.fraction > 0
    }
    for step in plan.steps:
        if (
            step.endpoint not in manifest.workload.endpoint_aliases
            or aliases.get(step.endpoint) not in auth.effective_endpoint_allowlist
        ):
            reasons.append("endpoint_not_allowed")
        if step.action not in auth.allowed_actions or step.action not in declared:
            reasons.append("action_not_allowed")
        if step.fault is not None and step.fault not in auth.destructive_scenarios:
            reasons.append("fault_not_allowed")
        if step.asset is not None and step.asset not in removable:
            reasons.append("asset_not_owned_ephemeral")
    if not set(plan.cleanup_fail_assets) <= removable:
        reasons.append("cleanup_asset_unknown")
    return tuple(sorted(set(reasons)))
