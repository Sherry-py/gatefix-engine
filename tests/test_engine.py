"""Tests for the GateFix engine: gate.py formula unit tests + a full
sydney_move case regression test (asserts the 7 commits route exactly as
described in README.md — this is the guard against silently breaking the
real case data or the precondition scoring functions)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine
from gate import GateConfig


# ---------- quality_score ----------

def test_quality_score_weights_sum_to_one():
    cfg = GateConfig()
    total = cfg.w_relevance + cfg.w_coverage + cfg.w_ordering + cfg.w_robustness
    assert abs(total - 1.0) < 1e-9


def test_quality_score_all_ones_is_one():
    cfg = GateConfig()
    assert cfg.quality_score(1, 1, 1, 1) == 1.0


def test_quality_score_all_zeros_is_zero():
    cfg = GateConfig()
    assert cfg.quality_score(0, 0, 0, 0) == 0.0


# ---------- route (three-state routing) ----------

def test_route_pass_above_tau_pass():
    cfg = GateConfig()
    assert cfg.route(0.9, verifiable_ext=True) == "PASS"


def test_route_auto_repair_in_gap_when_verifiable():
    cfg = GateConfig()
    assert cfg.route(0.6, verifiable_ext=True, dry_rounds=0) == "AUTO_REPAIR"


def test_route_escalate_when_gap_not_externally_verifiable():
    cfg = GateConfig()
    assert cfg.route(0.6, verifiable_ext=False) == "ESCALATE"


def test_route_escalate_once_k_dry_rounds_exhausted():
    cfg = GateConfig()
    assert cfg.route(0.6, verifiable_ext=True, dry_rounds=cfg.k_dry) == "ESCALATE"


def test_route_escalate_below_tau_repair():
    cfg = GateConfig()
    assert cfg.route(0.2, verifiable_ext=True) == "ESCALATE"


# ---------- is_commit / loop_mode ----------

def test_is_commit_infinite_cost_reverse_is_always_commit():
    cfg = GateConfig()
    assert cfg.is_commit(float("inf"), value=0) is True


def test_is_commit_above_lambda_threshold():
    cfg = GateConfig()
    assert cfg.is_commit(cost_reverse=100, value=50) is True   # 100 > 1.0 * 50


def test_is_commit_below_lambda_threshold():
    cfg = GateConfig()
    assert cfg.is_commit(cost_reverse=10, value=50) is False   # 10 <= 1.0 * 50


def test_loop_mode_on_the_loop_when_reversible_and_cheap_to_fix():
    cfg = GateConfig()
    assert cfg.loop_mode(cost_reverse=10, value=50, cost_fix=10) == "ON_THE_LOOP"


def test_loop_mode_in_the_loop_when_irreversible():
    cfg = GateConfig()
    assert cfg.loop_mode(cost_reverse=float("inf"), value=50, cost_fix=0) == "IN_THE_LOOP"


def test_loop_mode_in_the_loop_when_fix_too_expensive():
    cfg = GateConfig()
    assert cfg.loop_mode(cost_reverse=10, value=50, cost_fix=1000) == "IN_THE_LOOP"


# ---------- expectation_gate (soft commit) ----------

def test_expectation_gate_blocks_unsupported_promise():
    assert GateConfig.expectation_gate(contains_promise=True, has_feasibility_evidence=False) is False


def test_expectation_gate_allows_supported_promise():
    assert GateConfig.expectation_gate(contains_promise=True, has_feasibility_evidence=True) is True


def test_expectation_gate_allows_non_promise_message():
    assert GateConfig.expectation_gate(contains_promise=False, has_feasibility_evidence=False) is True


# ---------- expected_external_risk ----------

def test_expected_external_risk_multiplies_probability_and_loss():
    assert GateConfig.expected_external_risk(0.15, 1240) == 0.15 * 1240


# ---------- end-to-end: the real Sydney lease-termination case ----------

EXPECTED_ROUTES = {
    "discard_items": "PASS",
    "physical_handover": "PASS",
    "key_to_building_manager": "PASS",
    "key_to_agent": "PASS",
    "bond_claim_confirm": "ESCALATE",
    "friend_compensation": "BYPASS_TO_HUMAN",
    "air_freight_dispatch": "PASS",
}


def test_sydney_move_case_reproduces_expected_routes():
    engine.run_case("sydney_move")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]

    assert len(records) == len(EXPECTED_ROUTES)
    routes = {r["commit_id"]: r["route"] for r in records}
    assert routes == EXPECTED_ROUTES

    # air_freight_dispatch must still carry residual external risk even though it PASSes —
    # this is the "Commit(a,E)=True != Total_Cost settled" point the README makes.
    risk_record = next(r for r in records if r["commit_id"] == "air_freight_dispatch")
    assert risk_record["risk_ext"] == GateConfig.expected_external_risk(0.15, 1000)

    # friend_compensation is bypassed to human, never scored on the 4D-CQ axes —
    # but it merges two real stages of the same story (promise, then payout after
    # the resale plan fell through), so its final route must still be BYPASS_TO_HUMAN
    # even though the promise-stage precondition_fn (score_expectation_setting) PASSes
    # on its own. The pre-check result must show up in notes for the audit trail, but
    # must never be able to flip the final route.
    bypass_record = next(r for r in records if r["commit_id"] == "friend_compensation")
    assert bypass_record["bypassed_to_human"] is True
    assert bypass_record["route"] == "BYPASS_TO_HUMAN"
    assert "承诺阶段" in bypass_record["notes"]
    assert "实付阶段" in bypass_record["notes"]


# ---------- _resolve_regular_commit: AUTO_REPAIR must never leak out as a final route ----------
#
# Found via live-evidence testing of mcp_server/server.py's authorize() tool:
# score_air_freight_dispatch has no entry in REPAIR_REGISTRY, and evidence that lands its
# Q score in the AUTO_REPAIR band (tau_repair <= Q < tau_pass) used to make run_case return
# route="AUTO_REPAIR" as if it were a terminal state -- not a legal value (route must be
# PASS or ESCALATE here). The recorded sydney_move case never exercises this path because
# air_freight_dispatch's real evidence scores Q=1.0, which is why 42 passing tests never
# caught it. Extracted into _resolve_regular_commit specifically so this branch is testable
# without fabricating a whole second case (commits/bindings/evidence/preconditions files),
# which would contradict this repo's "only sydney_move is real-case-verified" disclosure.

def _fake_score_fn_in_auto_repair_band(evidence):
    return dict(R=1.0, C=0.4, O=1.0, Ro=0.4, verifiable_ext=True, notes="synthetic gap")


def test_resolve_regular_commit_without_repair_fn_escalates_not_auto_repair():
    config = GateConfig()
    route, result, Q, dry_rounds, repair_attempts = engine._resolve_regular_commit(
        config, _fake_score_fn_in_auto_repair_band, repair_fn=None, evidence={},
    )
    assert route == "ESCALATE"
    assert route != "AUTO_REPAIR"
    assert repair_attempts == 0


def test_resolve_regular_commit_with_repair_fn_still_auto_repairs_normally():
    """Guard against overcorrecting: a precondition_fn that DOES have a repair_fn must
    still actually attempt the repair loop, not short-circuit to ESCALATE."""
    config = GateConfig()
    calls = []

    def score_fn(evidence):
        if evidence.get("repaired"):
            return dict(R=1.0, C=1.0, O=1.0, Ro=1.0, verifiable_ext=True, notes="fixed")
        return dict(R=1.0, C=0.4, O=1.0, Ro=0.4, verifiable_ext=True, notes="gap")

    def repair_fn(evidence):
        calls.append(evidence)
        return {"repaired": True}

    route, result, Q, dry_rounds, repair_attempts = engine._resolve_regular_commit(
        config, score_fn, repair_fn, evidence={},
    )
    assert route == "PASS"
    assert repair_attempts == 1
    assert len(calls) == 1
