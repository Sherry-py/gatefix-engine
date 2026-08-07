"""Tests for fail-closed semantics (REVISION_BRIEF.md 任务 2).

The distinction this file exercises: a precondition_fn that runs cleanly and
returns a low score is a SUBSTANTIVE rejection (route=ESCALATE, reason_code
describes which threshold wasn't met) — the gate did its job. A
precondition_fn that raises is a FAULT — the gate never got to do its job at
all, and the one thing it must never do in that situation is default to
PASS. These are injected as evaluators that raise, at every layer that calls
score_fn/repair_fn/gate_fn: gate.py's safe_score/safe_repair primitives,
engine.py's regular/soft-commit branches, agent/gated_loop.py's
resolve_precondition, and — the outermost backstop — GatedAgentLoop.run()
itself, where the real invariant under test is "tool_fn is never called."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate import GateConfig, ReasonCode, safe_score, safe_repair
import engine
from agent.gated_loop import GatedAgentLoop, GateResult, resolve_precondition


def _raising_score_fn(evidence):
    raise RuntimeError("evaluator dependency unavailable")


def _raising_repair_fn(evidence):
    raise TimeoutError("repair backend timed out")


def _auto_repair_band_score_fn(evidence):
    return dict(R=1.0, C=0.4, O=1.0, Ro=0.4, verifiable_ext=True, notes="in repair band")


# ---------- gate.py primitives ----------

def test_safe_score_catches_exception_and_flags_fault():
    result, fault = safe_score(_raising_score_fn, {})
    assert fault is True
    assert result["verifiable_ext"] is False
    assert result["R"] == result["C"] == result["O"] == result["Ro"] == 0.0


def test_safe_score_passes_through_clean_result_unflagged():
    result, fault = safe_score(lambda ev: {"R": 1.0, "notes": "ok"}, {})
    assert fault is False
    assert result == {"R": 1.0, "notes": "ok"}


def test_safe_repair_catches_exception_and_returns_none():
    new_evidence, fault = safe_repair(_raising_repair_fn, {})
    assert fault is True
    assert new_evidence is None


# ---------- engine.py::_resolve_regular_commit ----------

def test_resolve_regular_commit_evaluator_fault_is_bypass_not_pass():
    config = GateConfig()
    route, result, Q, dry_rounds, repair_attempts, reason_code = engine._resolve_regular_commit(
        config, _raising_score_fn, repair_fn=None, evidence={},
    )
    assert route == "BYPASS_TO_HUMAN"
    assert route != "PASS"
    assert reason_code == ReasonCode.EVALUATOR_FAULT


def test_resolve_regular_commit_repair_fn_fault_is_bypass_not_pass():
    config = GateConfig()
    route, result, Q, dry_rounds, repair_attempts, reason_code = engine._resolve_regular_commit(
        config, _auto_repair_band_score_fn, repair_fn=_raising_repair_fn, evidence={},
    )
    assert route == "BYPASS_TO_HUMAN"
    assert route != "PASS"
    assert reason_code == ReasonCode.EVALUATOR_FAULT
    assert repair_attempts == 1


# ---------- agent/gated_loop.py::resolve_precondition ----------

def test_resolve_precondition_regular_branch_evaluator_fault_is_bypass():
    config = GateConfig()
    result = resolve_precondition(config, _raising_score_fn, evidence={})
    assert result.route == "BYPASS_TO_HUMAN"
    assert result.route != "PASS"
    assert result.reason_code == ReasonCode.EVALUATOR_FAULT


def test_resolve_precondition_repair_fn_fault_is_bypass():
    config = GateConfig()
    result = resolve_precondition(
        config, _auto_repair_band_score_fn, evidence={}, repair_fn=_raising_repair_fn,
    )
    assert result.route == "BYPASS_TO_HUMAN"
    assert result.reason_code == ReasonCode.EVALUATOR_FAULT


def test_resolve_precondition_soft_commit_evaluator_fault_is_bypass_not_pass():
    """soft_commit 走 expectation_gate，不走三态阈值——如果 evaluator 异常，
    绝不能被 expectation_gate 误当成 contains_promise=False 那样直接 PASS。"""
    config = GateConfig()
    result = resolve_precondition(config, _raising_score_fn, evidence={}, soft_commit=True)
    assert result.route == "BYPASS_TO_HUMAN"
    assert result.route != "PASS"
    assert result.reason_code == ReasonCode.EVALUATOR_FAULT


# ---------- GatedAgentLoop.run(): the real invariant is "tool_fn never called" ----------

def test_gated_loop_backstop_never_calls_tool_when_gate_fn_raises():
    calls = []

    def tool_fn(action):
        calls.append(action)
        return "executed", 1

    def raising_gate_fn(context, action):
        raise ValueError("gate_fn itself has a bug")

    def reason_fn(state):
        if state.get("history"):
            return {"type": "finish", "output": "done"}, 0
        return {"type": "tool", "tool": "risky_action"}, 0

    loop = GatedAgentLoop(gate_fn=raising_gate_fn, tool_fn=tool_fn, reason_fn=reason_fn)
    trace = loop.run(context="test", initial_state={})

    assert calls == [], "tool_fn must never run when the gate itself faulted"
    assert trace.halted_by_gate is True
    assert trace.steps[-1].route == "BYPASS_TO_HUMAN"
    assert "BLOCKED[BYPASS_TO_HUMAN]" in trace.final_output


def test_gated_loop_backstop_does_not_interfere_with_normal_pass():
    """Guard against overcorrecting: a gate_fn that behaves normally must
    still let tool_fn run — the try/except shouldn't swallow non-exceptional
    PASS results or change their shape."""
    calls = []

    def tool_fn(action):
        calls.append(action)
        return "executed", 1

    def gate_fn(context, action):
        return GateResult(route="PASS", R=1.0, C=1.0, O=1.0, Ro=1.0, Q=1.0)

    def reason_fn(state):
        if state.get("history"):
            return {"type": "finish", "output": "done"}, 0
        return {"type": "tool", "tool": "safe_action"}, 0

    loop = GatedAgentLoop(gate_fn=gate_fn, tool_fn=tool_fn, reason_fn=reason_fn)
    trace = loop.run(context="test", initial_state={})

    assert len(calls) == 1
    assert trace.halted_by_gate is False
