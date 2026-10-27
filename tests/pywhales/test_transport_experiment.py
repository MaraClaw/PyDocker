"""Conditional no-go and semantic comparison behavior with no candidate launches."""

import socket
import subprocess
from unittest.mock import patch

import pytest

from scripts.fleet_harness.experiment import (
    Baseline,
    Comparison,
    Experiment,
    Semantics,
    decide,
)
from scripts.fleet_harness.runner import run
from tests.pywhales.test_fleet_harness import manifest, plan


def semantics() -> Semantics:
    return Semantics(
        authentication="declared",
        endpoint_verification="declared",
        api_versions=("v1.0",),
        errors="typed",
        permissions="equivalent",
        result_shape="inventory",
        stream_ordering="ordered",
        backpressure="bounded",
        byte_accounting="bytes",
        ownership="owned",
        early_close="local_only",
        cancellation="local_only",
        timeout="bounded_local",
        ambiguous_mutation="unknown_no_retry_no_rollback",
        confidentiality="no_credentials_argv_or_payloads",
    )


def test_no_go_when_baseline_is_absent_contacts_nothing() -> None:
    with (
        patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
        patch.object(socket, "socket", side_effect=AssertionError("socket")),
    ):
        decision = decide(Experiment())

    assert decision.decision == "no_go"
    assert "real_material_overhead_baseline_missing" in decision.reasons
    assert decision.candidate_launches == decision.connections == 0
    assert decision.selected_route == "cli"


def test_matched_simulation_when_contracts_agree_does_not_authorize_adoption() -> None:
    result = run(manifest(), plan())
    comparison = Comparison(
        cli=result,
        candidate=result,
        cli_contract=semantics(),
        candidate_contract=semantics(),
    )

    decision = decide(Experiment(comparison=comparison))

    assert (
        decision.comparison_status == "matched_simulation" and not decision.mismatches
    )
    assert decision.decision == "no_go" and decision.simulation
    assert decision.evidence.comparison == comparison
    assert decision.candidate_launches == 0


@pytest.mark.parametrize(
    "field",
    [
        "cancellation",
        "stream_ordering",
        "backpressure",
        "byte_accounting",
        "early_close",
        "timeout",
        "permissions",
        "authentication",
        "endpoint_verification",
        "result_shape",
        "ownership",
        "errors",
        "api_versions",
        "ambiguous_mutation",
        "confidentiality",
    ],
)
def test_semantic_mismatch_when_candidate_is_faster_rejects(field: str) -> None:
    result = run(manifest(), plan())
    contract = semantics()
    candidate = contract.model_copy(
        update={field: ("v2.0",) if field == "api_versions" else "incompatible"}
    )
    comparison = Comparison(
        cli=result,
        candidate=result.model_copy(update={"elapsed_seconds": 0.01}),
        cli_contract=contract,
        candidate_contract=candidate,
    )

    decision = decide(Experiment(comparison=comparison))

    assert decision.comparison_status == "rejected"
    assert f"contract.{field}" in decision.mismatches
    assert (
        decision.selected_route == "cli"
        and decision.compose_buildx_route == "cli_unchanged"
    )


def test_scope_mismatch_when_operation_inputs_change_rejects() -> None:
    result = run(manifest(), plan())
    candidate = result.model_copy(update={"operation_digest": "sha256:" + "f" * 64})
    comparison = Comparison(
        cli=result,
        candidate=candidate,
        cli_contract=semantics(),
        candidate_contract=semantics(),
    )

    decision = decide(Experiment(comparison=comparison))

    assert decision.comparison_status == "rejected"
    assert "scope.operation_digest" in decision.mismatches


def test_outcome_mismatch_when_candidate_fails_below_stop_threshold_rejects() -> None:
    recipe = plan()
    candidate_recipe = recipe.model_copy(
        update={
            "steps": (
                *recipe.steps[:2],
                recipe.steps[2].model_copy(update={"outcome": "failed"}),
            )
        }
    )
    cli = run(manifest(), recipe)
    candidate = run(manifest(), candidate_recipe)
    comparison = Comparison(
        cli=cli,
        candidate=candidate,
        cli_contract=semantics(),
        candidate_contract=semantics(),
    )

    decision = decide(Experiment(comparison=comparison))

    assert candidate.status == "completed"
    assert (
        decision.comparison_status == "rejected"
        and "observed_outcomes" in decision.mismatches
    )


@pytest.mark.parametrize(
    "provenance,seconds,reason",
    [
        ("simulation", (2.0,), "real_material_overhead_baseline_unqualified"),
        ("real", (), "material_overhead_entry_unmet"),
        ("real", (0.1,), "material_overhead_entry_unmet"),
        ("real", (2.0,), "real_binding_and_deadline_adapter_unqualified"),
    ],
)
def test_entry_gate_when_baseline_is_declared_never_launches(
    provenance: str, seconds: tuple[float, ...], reason: str
) -> None:
    baseline = Baseline.model_validate(
        {
            "provenance": provenance,
            "representative": True,
            "independent_review_ref": "declaration-only",
            "operator_objective_ref": "declaration-only",
            "workload_digest": "sha256:" + "b" * 64,
            "environment_digest": "sha256:" + "c" * 64,
            "transport_seconds": seconds,
            "material_overhead_seconds": 1.0,
        }
    )

    decision = decide(Experiment(baseline=baseline))

    assert reason in decision.reasons
    assert decision.decision == "no_go" and decision.candidate_launches == 0
