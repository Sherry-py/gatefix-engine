"""Admission-gate self-check for the precondition scoring functions
(preconditions/sydney_move.py) — inspired by Tencent WorkBuddy Bench's
task-admission gate (arXiv:2607.20911v1): before trusting a benchmark task,
they require baseline_reward <= 0.3 (an untouched workspace must NOT already
satisfy the task) and oracle_reward == 1.0 (the gold patch must fully satisfy
it). If a task's own reference environment can't tell "not done" from "done",
the task is thrown out before it ever reaches an agent.

The same failure mode is possible here: a precondition scoring function
Pᵢ(E,θᵢ) that always returns a high score regardless of evidence quality
would make the whole gate a rubber stamp — it would look like a real check
while actually approving everything. This file is the check that catches
that, adapted to GateFix's own vocabulary (route(), not a bare reward number):

    baseline (incomplete/known-bad evidence)  → route() must NOT be "PASS"
    oracle   (complete/known-good evidence)   → route() must be "PASS"

This is a weaker bar than WorkBuddy's numeric baseline<=0.3 / oracle=1.0 —
GateFix's routing is three-valued (PASS / AUTO_REPAIR / ESCALATE), not a
0-1 reward, so "not PASS" is the meaningful failure-to-rubber-stamp signal,
not a specific low number. Where a function also controls verifiable_ext
(bond_claim), that is exercised too, since route() depends on both.

Known limitation surfaced by writing this (kept, not silently fixed):
score_discard_items and score_physical_handover hard-code R=Ro=1.0
regardless of evidence — only C and O actually vary with the evidence dict.
The baseline cases below still land in AUTO_REPAIR (not PASS) because C/O
alone pull Q under tau_pass, but a function that can ONLY be pulled down by
2 of 4 axes is weaker evidence of "this precondition genuinely discriminates"
than one where all 4 axes move. Left as-is rather than reverse-engineered a
fix to make this file pass more convincingly — flagged here and in the
README as a real follow-up, not resolved.

air_freight_dispatch's baseline evidence lands at Q=0.525, only 0.025 above
tau_repair (0.50) — the smallest margin of the six. Worth widening if this
case is ever used as a template for a new domain's precondition functions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate import GateConfig
from preconditions import sydney_move as p

cfg = GateConfig()


def route_for(result: dict) -> str:
    """Same two calls engine.py makes: score -> quality_score -> route."""
    q = cfg.quality_score(result["R"], result["C"], result["O"], result["Ro"])
    return cfg.route(q, result["verifiable_ext"], dry_rounds=0)


# ---------- discard_items ----------

def test_discard_items_baseline_not_pass():
    baseline = {}  # nothing removed/selected yet
    assert route_for(p.score_discard_items(baseline)) != "PASS"


def test_discard_items_oracle_is_pass():
    oracle = {
        "friend_selected_done": True, "organizer_selected_done": True,
        "id_documents_removed": True, "personal_info_papers_removed": True,
    }
    assert route_for(p.score_discard_items(oracle)) == "PASS"


# ---------- physical_handover ----------

def test_physical_handover_baseline_not_pass():
    baseline = {}
    assert route_for(p.score_physical_handover(baseline)) != "PASS"


def test_physical_handover_oracle_is_pass():
    oracle = {
        "payment_received": True, "buyer_identity_confirmed": True,
        "pickup_window_confirmed": True, "building_access_aligned_with_organizer": True,
    }
    assert route_for(p.score_physical_handover(oracle)) == "PASS"


# ---------- key_to_building_manager ----------

def test_key_to_building_manager_baseline_not_pass():
    baseline = {}  # no written consent on file
    assert route_for(p.score_key_to_building_manager(baseline)) != "PASS"


def test_key_to_building_manager_oracle_is_pass():
    oracle = {"agent_written_consent": True, "consent_source": "agent email"}
    assert route_for(p.score_key_to_building_manager(oracle)) == "PASS"


# ---------- key_to_agent (the case's real first-round evidence IS the baseline) ----------

def test_key_to_agent_real_first_round_evidence_is_not_pass():
    """This is not a synthetic worst case — it's the literal evidence dict
    from evidence/sydney_move_evidence.yaml before AUTO_REPAIR ran. If this
    function can't tell that "count came from memory" is weaker than "count
    came from the agent's email", the AUTO_REPAIR story for this commit
    (the one the README calls out by name) would be fiction."""
    baseline = {
        "empty_house_photos_timestamped": True,
        "key_count_source": "memory", "key_count_matches": None,
        "cleaning_before_power_off": True, "utility_meter_photos": True,
    }
    assert route_for(p.score_key_to_agent(baseline)) != "PASS"


def test_key_to_agent_post_repair_evidence_is_pass():
    baseline = {
        "empty_house_photos_timestamped": True,
        "key_count_source": "memory", "key_count_matches": None,
        "cleaning_before_power_off": True, "utility_meter_photos": True,
    }
    oracle = p.repair_key_to_agent(baseline)  # the real repair function, not hand-authored
    assert route_for(p.score_key_to_agent(oracle)) == "PASS"


# ---------- bond_claim (the one gate that must ESCALATE, not just avoid PASS) ----------

def test_bond_claim_real_mismatched_account_escalates():
    """The real case: refund account name != client name. This must not
    just "fail to reach PASS" — it must specifically ESCALATE, because
    verifiable_ext=False for this function (a name mismatch can't be closed
    by an automated re-check, only by a human confirming the relationship).
    AUTO_REPAIR would be the wrong failure mode here, not just a weaker one."""
    baseline = {
        "deductions": ["cleaning_fee", "break_fee_1100"],
        "approved_deductions": ["cleaning_fee", "break_fee_1100"],
        "refund_account_name": "Third party", "client_name": "Client",
        "refund_account_still_active": True,
    }
    assert route_for(p.score_bond_claim(baseline)) == "ESCALATE"


def test_bond_claim_matching_account_name_is_pass():
    oracle = {
        "deductions": ["cleaning_fee", "break_fee_1100"],
        "approved_deductions": ["cleaning_fee", "break_fee_1100"],
        "refund_account_name": "Client", "client_name": "Client",
        "refund_account_still_active": True,
    }
    assert route_for(p.score_bond_claim(oracle)) == "PASS"


# ---------- air_freight_dispatch ----------

def test_air_freight_dispatch_baseline_not_pass():
    """Uncharged overweight fee + unverified balance + a crack with zero
    reinforcement on the remaining thin boxes — the three failure modes the
    real function actually checks for."""
    baseline = {
        "box_count": 10, "thin_boxes_count": 5, "cracked_boxes_count": 3,
        "reinforced_boxes_count": 0, "overweight_kg": 11,
        "weight_charge_verified": False, "balance_formula_verified": False,
    }
    assert route_for(p.score_air_freight_dispatch(baseline)) != "PASS"


def test_air_freight_dispatch_oracle_is_pass():
    oracle = {
        "box_count": 10, "thin_boxes_count": 5, "cracked_boxes_count": 1,
        "reinforced_boxes_count": 4, "overweight_kg": 11,
        "weight_charge_verified": True, "balance_formula_verified": True,
    }
    assert route_for(p.score_air_freight_dispatch(oracle)) == "PASS"


# ---------- expectation_setting (soft commit — expectation_gate, not route()) ----------

def test_expectation_setting_unsupported_promise_is_blocked():
    baseline = {"promised_amount": 1000, "feasibility_probe_done": False, "feasibility_evidence": None}
    result = p.score_expectation_setting(baseline)
    allowed = cfg.expectation_gate(result["contains_promise"], result["has_feasibility_evidence"])
    assert allowed is False


def test_expectation_setting_supported_promise_is_allowed():
    oracle = {
        "promised_amount": 1000, "feasibility_probe_done": True,
        "feasibility_evidence": "friend quote + marketplace listings",
    }
    result = p.score_expectation_setting(oracle)
    allowed = cfg.expectation_gate(result["contains_promise"], result["has_feasibility_evidence"])
    assert allowed is True


# ---------- out of scope, by design ----------
#
# friend_compensation has bypass_to_human=True in commits/sydney_move_commits.yaml
# and no precondition_fn at all — it never enters 4D-CQ scoring (see engine.py's
# "分支 1" branch), so there is nothing here for an admission check to exercise.
# That is the point of BYPASS_TO_HUMAN as a fourth outcome, not a gap in this file.
