"""Conservative completeness classification, never offline qualification."""

from typing import Literal, assert_never

from .assessment import DistributionReport, assess
from .common import Contract, Pending, Token
from .manifest import Manifest
from .platform import Platform
from .results import RunResult
from .safety import Authorization, Budgets
from .support import SupportMatrix
from .workload import Measurements


class Evidence(Contract):
    """Manifest, maintained support matrix and declared observations for review."""

    manifest: Manifest
    support: SupportMatrix
    result: RunResult


class OfflineReport(Contract):
    """Schema-valid evidence classified separately from independent qualification."""

    schema_version: Literal[1] = 1
    schema_valid: Literal[True] = True
    evidence_complete: bool
    qualification: Literal["unqualified"] = "unqualified"
    review_required: Literal["independent_maintainer_and_operator"] = (
        "independent_maintainer_and_operator"
    )
    criteria: Literal["passed", "failed", "unknown"]
    flags: tuple[Token, ...]
    distributions: tuple[DistributionReport, ...]
    evidence: Evidence


def classify(evidence: Evidence) -> OfflineReport:
    """Classify claimed completeness without authenticating or authorizing a run.

    Missing observations and omitted/excluded outcomes remain visible. Even a
    complete, apparently passing record is unqualified pending independent review.
    """
    manifest, matrix, result = evidence.manifest, evidence.support, evidence.result
    missing: set[str] = set()
    notes: set[str] = {"independent_review_required"}
    distributions: tuple[DistributionReport, ...] = ()
    failed = False
    if manifest.provenance == "synthetic" or result.provenance == "synthetic":
        notes.add("synthetic_non_authorizing")
    if manifest.state != "runnable" or manifest.decisions:
        missing.add("draft_unresolved")
    if result.manifest_revision != manifest.identity.revision:
        missing.add("manifest_revision_mismatch")
    if result.provenance != manifest.provenance:
        missing.add("provenance_mismatch")
    if (
        result.outcomes.accounted != result.populations.all_submissions
        or not result.populations.all_submissions
    ):
        missing.add("outcome_accounting_incomplete")
    if result.missing_evidence:
        missing.add("declared_missing_evidence")
    if result.excluded_evidence:
        missing.add("declared_excluded_evidence")
    if not all(
        (
            result.isolation_evidence_ref,
            result.artifact_receipt_ref,
            result.cleanup_receipt_ref,
        )
    ):
        missing.add("receipts_missing")
    if result.residual_assets:
        missing.add("cleanup_incomplete")
    if result.early_stopped:
        missing.add("early_stopped")
    row = next((row for row in matrix.rows if row.id == result.support_row), None)
    if row is None or result.support_row != manifest.support_row:
        missing.add("support_row_missing")
    else:
        if row.status != "supported":
            notes.add("support_unapproved")
        if result.observed_versions is None or result.observed_versions != row.versions:
            missing.add("environment_missing_or_mismatched")
        if any(
            decision.field.startswith(f"rows.{matrix.rows.index(row)}.")
            for decision in matrix.decisions
        ):
            missing.add("support_versions_unresolved")
        match manifest.platform:
            case Platform() as platform:
                if row.orchestrator != platform.orchestrator or not set(
                    platform.required_features
                ) <= set(row.required_features):
                    missing.add("support_features_mismatch")
            case Pending():
                missing.add("platform_missing")
    match manifest.authorization:
        case Authorization() as auth:
            if (
                result.authorization_record != auth.record_id
                or result.run_namespace != auth.run_namespace
            ):
                missing.add("authorization_record_mismatch")
            if result.populations.faults and (
                "fault" not in auth.allowed_actions or not auth.destructive_scenarios
            ):
                missing.add("fault_declaration_missing")
        case Pending():
            missing.add("authorization_missing")
    match manifest.workload:
        case Pending():
            missing.add("workload_missing")
        case workload:
            if set(result.endpoint_aliases) != set(workload.endpoint_aliases):
                missing.add("endpoint_alias_mismatch")
            if result.populations.faults and not any(
                operation.action == "fault" and operation.fraction > 0
                for operation in workload.operation_mix
            ):
                missing.add("fault_declaration_missing")
            match workload.termination:
                case "duration":
                    if (
                        result.elapsed_seconds is None
                        or result.elapsed_seconds < workload.duration_seconds
                    ):
                        missing.add("termination_incomplete")
                case "operation_count":
                    if (
                        workload.termination_operations is not None
                        and result.populations.all_submissions
                        < workload.termination_operations
                    ):
                        missing.add("termination_incomplete")
                case unreachable:
                    assert_never(unreachable)
    match (manifest.measurements, manifest.budgets):
        case (Measurements() as measurements, Budgets() as budgets):
            assessment = assess(measurements, budgets, result)
            missing.update(assessment.missing)
            failed = assessment.failed
            distributions = assessment.distributions
        case _:
            missing.add("criteria_missing")
    complete = not missing
    return OfflineReport(
        evidence_complete=complete,
        criteria="unknown" if not complete else ("failed" if failed else "passed"),
        flags=tuple(sorted(missing | notes)),
        distributions=distributions,
        evidence=evidence,
    )
