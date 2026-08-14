"""Tests for the pharmacy_dispensing case — the first case in this repo whose
judgment basis is taken directly from a real, publicly adjudicated incident
(not a private-case transcription like sydney_move, not a constructed-but-
law-grounded scenario like cross_border_transfer): on 2017-12-26 at Vanderbilt
University Medical Center, nurse RaDonda Vaught used an automated dispensing
cabinet's (ADC) override function to retrieve vecuronium instead of the
ordered sedative Versed, and the patient, Charlene Murphey, died. Vaught was
convicted of criminally negligent homicide and gross neglect on 2022-03-25.
See commits/pharmacy_dispensing_commits.yaml and README "Case notes" for the
full account and sources (Wikipedia "RaDonda Vaught homicide case", KFF
Health News, UNC Journal of Law & Technology).

This gives the "engine.py/gate.py are domain-agnostic, only the four config
files change per case" architecture claim its third test, and its first test
in a domain where the commit is physical (medication administration) and a
real irreversible harm already occurred — sydney_move is human-executed
physical handover, cross_border_transfer is agent-initiated data compliance,
this case is a human operating automation equipment to execute an irreversible
physical dispensing action.

Like test_cross_border_transfer_case.py, this mirrors test_engine.py's real
end-to-end pattern (run the case, assert the route matches what the scoring
function should produce) against representative evidence constructed from the
real incident's reported structural facts — not a verbatim transcription of
trial records, which is exactly what this docstring exists to make
unmistakable."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine
from gate import GateConfig


EXPECTED_ROUTES = {
    "dispense_high_alert_drug_via_override": "ESCALATE",
}


def test_pharmacy_dispensing_case_reproduces_expected_routes():
    engine.run_case("pharmacy_dispensing")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]

    assert len(records) == len(EXPECTED_ROUTES)
    routes = {r["commit_id"]: r["route"] for r in records}
    assert routes == EXPECTED_ROUTES

    rec = records[0]
    # Drug identity doesn't match the verified order (Relevance), the two
    # independent safety checks (high-alert-drug confirmation, patient ID
    # scan) are both missing (Coverage), the order wasn't verified in the
    # system before the override fired (Ordering), and the override search
    # used a truncated query (Robustness) — the quality gate lands well below
    # tau_repair, escalating straight to a human (pharmacist / second-check
    # nurse), never getting an automated repair attempt.
    cfg = GateConfig()
    assert rec["R"] == 0.1
    assert rec["C"] == 0.2
    assert rec["O"] == 0.4
    assert rec["Ro"] == 0.3
    assert rec["Q"] < cfg.tau_repair
    assert rec["verifiable_ext"] is False
    assert rec["route"] == "ESCALATE"
    assert "药师" in rec["notes"] or "核对" in rec["notes"]


def test_pharmacy_dispensing_case_never_auto_repairs():
    """The precondition_fn deliberately registers no repair_fn (whether the
    drug in hand truly matches this patient's current order is a clinical
    identity-verification judgment, not a fact an automated re-query can fix)
    — this asserts that design choice holds, not just that the final route
    happens to be ESCALATE."""
    engine.run_case("pharmacy_dispensing")
    record_path = engine.BASE_DIR / "gate_record.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()]
    rec = records[0]
    assert rec["dry_rounds"] == 0
    assert "AUTO_REPAIR" not in rec["notes"]
