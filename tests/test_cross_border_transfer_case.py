"""Tests for the cross_border_transfer case — the first case added to this repo
that is NOT transcribed from a real personal case (sydney_move remains the only
real-case-verified scenario; see README "Case notes"). This case is a hypothetical
scenario grounded in a real legal precedent (TikTok fined €530M by the Irish DPC
for EU→China transfers, 2025; GDPR Chapter V is current law) — constructed to give
the "engine.py/gate.py are domain-agnostic, only the four config files change per
case" architecture claim its first test outside sydney_move's domain (human-executed
physical handover vs. this case's agent-initiated data-compliance decision).

This mirrors test_engine.py's real end-to-end pattern (run the case, assert the
route matches what the scoring function should produce) but against fabricated-for-
this-purpose evidence rather than real case notes — which is exactly what this file
name and docstring exist to make unmistakable.

Two commits, deliberately chosen to exercise different terminal routes with the
same underlying evidence shape: send_pii_to_risk_control_vendor has a substantive
legal-basis gap (no adequacy decision, no safeguards) that only a human can resolve,
while send_pii_to_ticket_archival_vendor has a purely factual gap (TIA completed but
its document hasn't synced into this evidence pipeline yet) that AUTO_REPAIR can
close on its own — so PASS, AUTO_REPAIR, and ESCALATE all show up in one case run,
not just the single-ESCALATE-point case this file used to test."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine
from gate import GateConfig


EXPECTED_ROUTES = {
    "send_pii_to_risk_control_vendor": "ESCALATE",
    "send_pii_to_ticket_archival_vendor": "PASS",
}


def test_cross_border_transfer_case_reproduces_expected_routes():
    engine.run_case("cross_border_transfer")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]

    assert len(records) == len(EXPECTED_ROUTES)
    routes = {r["commit_id"]: r["route"] for r in records}
    assert routes == EXPECTED_ROUTES

    by_id = {r["commit_id"]: r for r in records}
    rec = by_id["send_pii_to_risk_control_vendor"]
    # No adequacy decision for the destination, no SCC, no TIA, no consent, no
    # government-access-risk assessment — Relevance/Coverage/Robustness all score
    # low; the quality gate lands below tau_repair entirely (not just in the
    # AUTO_REPAIR band), so this escalates straight to a human (DPO/legal), never
    # getting an automated repair attempt.
    cfg = GateConfig()
    assert rec["R"] == 0.15
    assert rec["C"] == 0.2
    assert rec["Ro"] == 0.2
    assert rec["Q"] < cfg.tau_repair
    assert rec["verifiable_ext"] is False
    assert rec["route"] == "ESCALATE"
    assert "DPO" in rec["notes"] or "法务" in rec["notes"]


def test_cross_border_transfer_case_never_auto_repairs():
    """send_pii_to_risk_control_vendor's precondition_fn deliberately registers
    no repair_fn (destination adequacy / legal-basis sufficiency is a legal
    judgment call, not a fact an automated re-query can fix) — this asserts that
    design choice holds, not just that the final route happens to be ESCALATE."""
    engine.run_case("cross_border_transfer")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]
    rec = next(r for r in records if r["commit_id"] == "send_pii_to_risk_control_vendor")
    assert rec["dry_rounds"] == 0
    assert "AUTO_REPAIR" not in rec["notes"]


def test_cross_border_transfer_case_auto_repairs_document_sync_gap():
    """send_pii_to_ticket_archival_vendor's only gap (TIA completed but its
    document hasn't synced into this evidence pipeline) is externally
    verifiable, so it should reach PASS after exactly one AUTO_REPAIR round —
    this is the flagship case's first AUTO_REPAIR-bearing commit."""
    engine.run_case("cross_border_transfer")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]
    rec = next(r for r in records if r["commit_id"] == "send_pii_to_ticket_archival_vendor")
    assert rec["route"] == "PASS"
    assert rec["C"] == 1.0
    assert "AUTO_REPAIR" in rec["notes"]
    assert rec["dry_rounds"] == 0


def test_government_access_robustness_dimension_is_not_a_placeholder():
    """Regression guard for the gap the earlier repo review flagged: Ro used to
    be hardcoded to 1.0 for every commit in this case ('本 case 无对应证据字段
    区分，待收紧'). It now varies with the government-access-risk evidence
    fields, same as every other dimension."""
    from preconditions.cross_border_transfer import score_cross_border_transfer

    not_assessed = score_cross_border_transfer({})
    assert not_assessed["Ro"] == 0.2

    assessed_high_no_measures = score_cross_border_transfer({
        "government_access_risk_assessed": True,
        "residual_access_risk_level": "high",
        "supplementary_measures_applied": False,
    })
    assert assessed_high_no_measures["Ro"] == 0.3

    assessed_high_with_measures = score_cross_border_transfer({
        "government_access_risk_assessed": True,
        "residual_access_risk_level": "high",
        "supplementary_measures_applied": True,
    })
    assert assessed_high_with_measures["Ro"] == 1.0

    assessed_low = score_cross_border_transfer({
        "government_access_risk_assessed": True,
        "residual_access_risk_level": "low",
    })
    assert assessed_low["Ro"] == 1.0
