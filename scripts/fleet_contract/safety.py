"""Operator-supplied criteria and authorization records without authority checks."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from .common import (
    Amount,
    Contract,
    Fraction,
    Nonempty,
    Positive,
    PositiveCount,
    Text,
    Token,
)
from .workload import Action, Boundary, Resource


class LatencyBudget(Contract):
    """Population-specific percentile threshold, in seconds."""

    boundary: Boundary
    percentile: Fraction
    maximum_seconds: Positive


class ResourceBudget(Contract):
    """Resource high-water maximum and an earlier-or-equal stop threshold."""

    resource: Resource
    maximum: Positive
    early_stop: Positive

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Require early stop not to exceed the resource maximum."""
        if self.early_stop > self.maximum:
            raise PydanticCustomError("budget_bounds", "budget_bounds")
        return self


class Budgets(Contract):
    """Preregistered operator criteria, never SDK defaults or achieved SLOs."""

    latency: Nonempty[LatencyBudget]
    resources: Nonempty[ResourceBudget]
    maximum_error_fraction: Fraction
    maximum_recovery_seconds: Positive
    polling_interval_seconds: Positive
    stability_window_seconds: Positive
    stable_observations: PositiveCount
    readiness_definition: Text
    sustained_duration_seconds: Positive
    early_stop_duration_seconds: Positive
    early_stop_error_fraction: Fraction
    early_stop_unknown_count: PositiveCount
    cleanup_timeout_seconds: Positive

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Reject contradictory polling, stop durations and duplicate criteria."""
        if (
            len({budget.boundary for budget in self.latency}) != len(self.latency)
            or len({budget.resource for budget in self.resources})
            != len(self.resources)
            or self.polling_interval_seconds * self.stable_observations
            > self.stability_window_seconds
            or self.sustained_duration_seconds > self.early_stop_duration_seconds
            or self.stability_window_seconds > self.sustained_duration_seconds
            or self.early_stop_error_fraction < self.maximum_error_fraction
        ):
            raise PydanticCustomError("budget_bounds", "budget_bounds")
        return self


class Authorization(Contract):
    """Explicit operator declaration, not proof of real authority or isolation."""

    record_id: Token
    operator: Text
    approved_by: Text
    purpose: Literal["synthetic_example", "operator_declared"]
    disposable: bool
    effective_endpoint_allowlist: Nonempty[Token]
    allowed_actions: Nonempty[Action]
    destructive_scenarios: tuple[Token, ...]
    run_namespace: Token
    ownership_marker: Token
    isolation_evidence_ref: Token
    cleanup_verification: Literal["owned_inventory_readback"]
    cleanup_surface_ref: Token
    stop_authority: Text
    maximum_run_seconds: Positive
    maximum_created_assets: PositiveCount
    expires_after_seconds: Amount

    @model_validator(mode="after")
    def disposable_only(self) -> Self:
        """Require an explicit strict-boolean disposable-target declaration."""
        if not self.disposable:
            raise PydanticCustomError(
                "authorization_disposable", "authorization_disposable"
            )
        return self
