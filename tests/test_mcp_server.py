"""Tests for mcp_server/server.py — the MCP wrapper around the real GateFix
gate. Unlike agent/gated_loop.py's make_case_gate_fn (which replays the
static sydney_move_evidence.yaml case), authorize() here takes live evidence
supplied by the caller — these tests exercise exactly that: build evidence
dicts by hand and check the real precondition/gate code judges them
correctly, the same way an external MCP client would.

@mcp.tool() returns the original function unwrapped, so these are called
directly as plain Python functions — no protocol/transport involved.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.server import authorize, list_precondition_functions


def test_list_precondition_functions_covers_all_six():
    """score_expectation_setting exists in REGISTRY (it's friend_compensation's
    promise-stage pre-check) but is deliberately excluded here — see
    test_list_precondition_functions_excludes_bypass_to_human."""
    fns = list_precondition_functions("sydney_move")
    names = {f["precondition_fn"] for f in fns}
    assert names == {
        "score_discard_items", "score_physical_handover",
        "score_key_to_building_manager", "score_key_to_agent",
        "score_bond_claim", "score_air_freight_dispatch",
    }


def test_list_precondition_functions_excludes_bypass_to_human():
    """friend_compensation has bypass_to_human=True — even though it also
    carries a precondition_fn (score_expectation_setting, its promise-stage
    pre-check), it must not appear here, and it must be impossible to
    "authorize" it via evidence, since that's precisely the point of
    bypass_to_human: the payout decision is inherently human judgment, and
    the pre-check passing must never be mistaken for the commit itself being
    authorized."""
    fns = list_precondition_functions("sydney_move")
    commit_ids = {f["commit_id"] for f in fns}
    fn_names = {f["precondition_fn"] for f in fns}
    assert "friend_compensation" not in commit_ids
    assert "score_expectation_setting" not in fn_names


def test_list_precondition_functions_flags_auto_repair_and_soft_commit():
    fns = {f["precondition_fn"]: f for f in list_precondition_functions("sydney_move")}
    assert fns["score_key_to_agent"]["has_auto_repair"] is True
    assert fns["score_bond_claim"]["has_auto_repair"] is False
    assert fns["score_discard_items"]["soft_commit"] is False


def test_list_precondition_functions_all_have_docs():
    """Every scoring function should document its expected evidence fields
    so an external MCP client can construct a valid evidence dict without
    reading preconditions/sydney_move.py directly."""
    fns = list_precondition_functions("sydney_move")
    for f in fns:
        assert f["doc"], f"{f['precondition_fn']} has no docstring"


def test_authorize_unknown_precondition_fn_raises():
    try:
        authorize("sydney_move", "score_does_not_exist", {})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "score_does_not_exist" in str(e)


def test_authorize_clean_evidence_passes():
    result = authorize("sydney_move", "score_discard_items", {
        "friend_selected_done": True, "organizer_selected_done": True,
        "id_documents_removed": True, "personal_info_papers_removed": True,
    })
    assert result["route"] == "PASS"
    assert result["authorized"] is True


def test_authorize_live_evidence_with_gap_auto_repairs_to_pass():
    """The core proof this is a real evidence-based gate, not a case
    replay: submit a fresh evidence dict (not from evidence/sydney_move_
    evidence.yaml) with the same real gap the case had — key count sourced
    from memory — and the real AUTO_REPAIR loop must still resolve it."""
    result = authorize("sydney_move", "score_key_to_agent", {
        "empty_house_photos_timestamped": True,
        "key_count_source": "memory",
        "key_count_matches": None,
        "cleaning_before_power_off": True,
        "utility_meter_photos": True,
    })
    assert result["route"] == "PASS"
    assert result["authorized"] is True
    assert result["repair_attempts"] >= 1


def test_authorize_mismatched_refund_account_escalates_and_is_not_authorized():
    result = authorize("sydney_move", "score_bond_claim", {
        "deductions": ["cleaning_fee"],
        "approved_deductions": ["cleaning_fee"],
        "refund_account_name": "Someone else",
        "client_name": "Client",
        "refund_account_still_active": True,
    })
    assert result["route"] == "ESCALATE"
    assert result["authorized"] is False


def test_authorize_rejects_bypass_to_human_precondition_fn():
    """score_expectation_setting is friend_compensation's promise-stage
    pre-check, not an independently authorizable function — friend_compensation
    is bypass_to_human, so the actual payout decision must always go through a
    human, regardless of what the pre-check scores. authorize() must refuse to
    run it at all, not silently return a route an external client could
    mistake for "this commit is authorized." This is the regression test for
    the exact gap that would reopen if _case_precondition_index() ever stopped
    excluding bypass_to_human commits' precondition_fn."""
    try:
        authorize("sydney_move", "score_expectation_setting", {
            "promised_amount": 500,
            "feasibility_probe_done": True,
            "feasibility_evidence": "friend quote reference",
        })
        assert False, "expected ValueError"
    except ValueError as e:
        assert "score_expectation_setting" in str(e)
        assert "bypass_to_human" in str(e)


def test_authorize_auto_repair_band_without_repair_fn_escalates_not_auto_repair():
    """Regression test for a real bug found by live-testing this exact tool call:
    score_air_freight_dispatch has no entry in REPAIR_REGISTRY. Evidence landing its Q
    score in the AUTO_REPAIR band used to make authorize() return route="AUTO_REPAIR" as
    if it were terminal -- not a value this tool's contract allows (route must be PASS,
    ESCALATE, or BYPASS_TO_HUMAN). 2 cracked boxes with only 1 of the remaining 4 thin
    boxes reinforced scores Q=0.7, squarely in the repair band with verifiable_ext=True,
    but there's no automated way to actually close that gap for this function."""
    result = authorize("sydney_move", "score_air_freight_dispatch", {
        "cracked_boxes_count": 2,
        "thin_boxes_count": 6,
        "reinforced_boxes_count": 1,
        "weight_charge_verified": True,
        "balance_formula_verified": True,
    })
    assert result["route"] == "ESCALATE"
    assert result["route"] != "AUTO_REPAIR"
    assert result["authorized"] is False
    assert result["repair_attempts"] == 0
