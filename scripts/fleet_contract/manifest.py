"""Version-one manifest and cross-group runtime invariants."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from .common import Contract, Decision, Pending, SchemaVersion, Token, check_decisions
from .platform import Ownership, Platform, Topology
from .safety import Authorization, Budgets
from .workload import Identity, Measurements, Workload


class Manifest(Contract):
    """Typed workload contract whose runnable state confers no authorization."""

    schema_version: SchemaVersion
    state: Literal["draft", "runnable"]
    provenance: Literal["synthetic", "operator_declared"]
    identity: Identity
    support_row: Token | Pending
    workload: Workload | Pending
    platform: Platform | Pending
    topology: Topology | Pending
    ownership: Ownership | Pending
    measurements: Measurements | Pending
    budgets: Budgets | Pending
    authorization: Authorization | Pending
    decisions: tuple[Decision, ...]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Reject unresolved runnable profiles and unsafe cross-group references."""
        check_decisions(self, self.decisions)
        if self.state == "runnable" and self.decisions:
            raise PydanticCustomError("runnable_unresolved", "runnable_unresolved")
        match (self.workload, self.platform):
            case (Workload() as workload, Platform() as platform):
                aliases = {alias.name for alias in platform.aliases}
                if not set(workload.endpoint_aliases) <= aliases:
                    raise PydanticCustomError("unknown_alias", "unknown_alias")
                if workload.service_replicas and platform.orchestrator == "standalone":
                    raise PydanticCustomError("replica_mode", "replica_mode")
            case _:
                pass
        match (self.platform, self.topology):
            case (Platform() as platform, Topology() as topology):
                if (
                    topology.network == "overlay" or topology.ingress == "routing_mesh"
                ) and platform.orchestrator != "swarm":
                    raise PydanticCustomError("topology_mode", "topology_mode")
            case _:
                pass
        match (self.platform, self.workload, self.authorization):
            case (
                Platform() as platform,
                Workload() as workload,
                Authorization() as auth,
            ):
                selected = {
                    alias.effective_endpoint
                    for alias in platform.aliases
                    if alias.name in workload.endpoint_aliases
                }
                inventory = {endpoint.id for endpoint in platform.endpoints}
                allowed = set(auth.effective_endpoint_allowlist)
                if not selected <= allowed <= inventory:
                    raise PydanticCustomError(
                        "authorization_target", "authorization_target"
                    )
                actions = {operation.action for operation in workload.operation_mix}
                if (
                    not actions <= set(auth.allowed_actions)
                    or "cleanup" not in auth.allowed_actions
                ):
                    raise PydanticCustomError(
                        "authorization_action", "authorization_action"
                    )
                if "fault" in actions and not auth.destructive_scenarios:
                    raise PydanticCustomError(
                        "authorization_fault", "authorization_fault"
                    )
                if (self.provenance == "synthetic") != (
                    auth.purpose == "synthetic_example"
                ):
                    raise PydanticCustomError("provenance_mode", "provenance_mode")
            case _:
                pass
        match (
            self.workload,
            self.measurements,
            self.budgets,
            self.authorization,
            self.ownership,
        ):
            case (
                Workload() as workload,
                Measurements() as measurements,
                Budgets() as budgets,
                Authorization() as auth,
                Ownership() as ownership,
            ):
                if (
                    {budget.boundary for budget in budgets.latency}
                    != {item.boundary for item in measurements.latency}
                    or {budget.resource for budget in budgets.resources}
                    != set(measurements.resource_high_water)
                    or any(
                        budget.percentile not in item.percentiles
                        for budget in budgets.latency
                        for item in measurements.latency
                        if budget.boundary == item.boundary
                    )
                    or not budgets.sustained_duration_seconds
                    <= workload.duration_seconds
                    <= budgets.early_stop_duration_seconds
                    <= auth.maximum_run_seconds
                    or budgets.cleanup_timeout_seconds
                    != ownership.teardown_timeout_seconds
                    or ownership.maximum_created_assets > auth.maximum_created_assets
                    or auth.expires_after_seconds < auth.maximum_run_seconds
                ):
                    raise PydanticCustomError("criteria_mismatch", "criteria_mismatch")
            case _:
                pass
        return self
