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
name and docstring exist to make unmistakable."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine
from gate import GateConfig


EXPECTED_ROUTES = {
    "send_pii_to_risk_control_vendor": "ESCALATE",
}


def test_cross_border_transfer_case_reproduces_expected_routes():
    engine.run_case("cross_border_transfer")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]

    assert len(records) == len(EXPECTED_ROUTES)
    routes = {r["commit_id"]: r["route"] for r in records}
    assert routes == EXPECTED_ROUTES

    rec = records[0]
    # No adequacy decision for the destination, no SCC, no TIA, no consent —
    # Relevance and Coverage both score low; the quality gate lands below
    # tau_repair entirely (not just in the AUTO_REPAIR band), so this escalates
    # straight to a human (DPO/legal), never getting an automated repair attempt.
    cfg = GateConfig()
    assert rec["R"] == 0.15
    assert rec["C"] == 0.2
    assert rec["Q"] < cfg.tau_repair
    assert rec["verifiable_ext"] is False
    assert rec["route"] == "ESCALATE"
    assert "DPO" in rec["notes"] or "法务" in rec["notes"]


def test_cross_border_transfer_case_never_auto_repairs():
    """The precondition_fn deliberately registers no repair_fn (destination
    adequacy / legal-basis sufficiency is a legal judgment call, not a fact an
    automated re-query can fix) — this asserts that design choice holds, not
    just that the final route happens to be ESCALATE."""
    engine.run_case("cross_border_transfer")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]
    rec = records[0]
    assert rec["dry_rounds"] == 0
    assert "AUTO_REPAIR" not in rec["notes"]
