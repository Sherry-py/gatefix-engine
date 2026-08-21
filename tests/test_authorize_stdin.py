"""Tests for mcp_server/authorize_stdin.py — the subprocess bridge used by
the DeepSeek Harness Cordis plugin (dsh_plugin/). Spawns the real script as a
real subprocess against real cross_border_transfer evidence, the same way
the TS plugin does — no mocking, so a broken stdin/stdout contract shows up
here before it shows up in the Node.js side.
"""

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BASE_DIR / "mcp_server" / "authorize_stdin.py"


def _run(payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=BASE_DIR,
    )


def test_pass_evidence_returns_route_pass_on_stdout():
    proc = _run({
        "case": "cross_border_transfer",
        "precondition_fn": "score_cross_border_transfer",
        "evidence": {
            "destination_adequacy_decision": True,
            "scc_signed": True,
            "tia_completed": True,
            "explicit_consent_obtained": True,
            "consent_obtained_before_request": True,
            "government_access_risk_assessed": True,
            "residual_access_risk_level": "low",
        },
    })
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["route"] == "PASS"
    assert out["gate_state"] == "PASS"


def test_insufficient_evidence_returns_route_escalate_not_pass():
    proc = _run({
        "case": "cross_border_transfer",
        "precondition_fn": "score_cross_border_transfer",
        "evidence": {
            "destination_adequacy_decision": False,
            "scc_signed": False,
            "tia_completed": False,
            "explicit_consent_obtained": False,
            "consent_obtained_before_request": False,
            "government_access_risk_assessed": False,
        },
    })
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["route"] == "ESCALATE"
    assert out["authorized"] is False


def test_auto_repair_resolves_to_pass_when_document_is_findable():
    proc = _run({
        "case": "cross_border_transfer",
        "precondition_fn": "score_send_pii_to_archival_vendor",
        "evidence": {
            "destination_adequacy_decision": False,
            "scc_signed": True,
            "tia_completed": True,
            "tia_document_locatable_in_system": False,
            "explicit_consent_obtained": True,
            "consent_obtained_before_request": True,
            "government_access_risk_assessed": True,
            "residual_access_risk_level": "low",
        },
    })
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["route"] == "PASS"
    assert out["repair_attempts"] == 1


def test_malformed_stdin_fails_closed_with_nonzero_exit_and_no_stdout():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=BASE_DIR,
    )
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "malformed request" in proc.stderr


def test_unknown_precondition_fn_fails_closed():
    proc = _run({
        "case": "cross_border_transfer",
        "precondition_fn": "score_does_not_exist",
        "evidence": {},
    })
    assert proc.returncode == 1
    assert proc.stdout == ""
