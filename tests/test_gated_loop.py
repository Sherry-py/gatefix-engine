"""Tests for agent/gated_loop.py — the agent-loop pre-action authorization
wrapper around gate.py's three-state route().

Two layers, same split as test_engine.py / test_admission_gate.py:
  - unit tests with hand-built fake gate_fn/tool_fn/reason_fn, exercising the
    loop's control flow in isolation;
  - a fidelity block using make_case_gate_fn/make_case_reason_fn/
    make_case_tool_fn wired to the REAL sydney_move commits/evidence/
    preconditions — proving the seams call real code, not placeholders.

Core contract, non-negotiable: whenever the gate's final route isn't PASS,
tool_fn must never be called. There is no passthrough switch for this — see
agent/gated_loop.py's module docstring for why CLARIFY-style bypasses were
dropped from the original draft.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.gated_loop import (
    GatedAgentLoop,
    GateResult,
    make_case_gate_fn,
    make_case_reason_fn,
    make_case_tool_fn,
)


def _finish_after_one(state):
    if state.get("history"):
        return {"type": "finish", "output": "done"}, 0
    return {"type": "tool", "tool": "compute", "commit_id": "compute"}, 0


def _make_gate(route, reason=""):
    return lambda ctx, action: GateResult(
        route=route, R=1.0, C=1.0, O=1.0, Ro=1.0, Q=1.0, reason=reason)


# ---------- unit tests: hand-built fakes ----------

def test_pass_reaches_tool():
    calls = []

    def tool(action):
        calls.append(action)
        return "42", 10

    loop = GatedAgentLoop(_make_gate("PASS"), tool, _finish_after_one)
    trace = loop.run("clean context", {})
    assert len(calls) == 1
    assert trace.halted_by_gate is False
    assert trace.final_output == "done"


def test_escalate_blocks_tool_call():
    """The non-negotiable contract: ESCALATE must not let tool_fn run."""
    calls = []

    def tool(action):
        calls.append(action)
        return "leaked", 10

    gate = _make_gate("ESCALATE", reason="coverage defect: evidence gap")
    loop = GatedAgentLoop(gate, tool, _finish_after_one)
    trace = loop.run("defective context", {})
    assert calls == []
    assert trace.halted_by_gate is True
    assert "BLOCKED[ESCALATE]" in trace.final_output
    assert trace.steps[0].blocked is True


def test_bypass_to_human_blocks_tool_call():
    """The other non-PASS route engine.py produces: human-only evidence.
    Same contract as ESCALATE — tool_fn must not run."""
    calls = []
    tool = lambda a: (calls.append(a) or "x", 5)
    gate = _make_gate("BYPASS_TO_HUMAN", reason="relationship needs human confirmation")
    loop = GatedAgentLoop(gate, tool, _finish_after_one)
    trace = loop.run("human-only evidence", {})
    assert calls == []
    assert trace.halted_by_gate is True
    assert "BLOCKED[BYPASS_TO_HUMAN]" in trace.final_output


def test_telemetry_records_cost_and_latency():
    tool = lambda a: ("ok", 100)
    loop = GatedAgentLoop(_make_gate("PASS"), tool, _finish_after_one)
    trace = loop.run("ctx", {})
    step = trace.steps[0]
    assert step.gate_latency_ms >= 0
    assert step.action_tokens == 100
    assert step.total_tokens == step.gate_tokens + 100
    assert 0.0 <= trace.gate_overhead_ratio() <= 1.0


def test_max_steps_bound():
    tool = lambda a: ("ok", 1)
    reason = lambda s: ({"type": "tool", "tool": "loopy", "commit_id": "loopy"}, 0)
    loop = GatedAgentLoop(_make_gate("PASS"), tool, reason, max_steps=3)
    trace = loop.run("ctx", {})
    assert len(trace.steps) == 3


# ---------- fidelity tests: real sydney_move wiring, no fakes ----------

def test_real_case_gate_fn_auto_repairs_key_to_agent_to_pass():
    """key_to_agent's evidence starts with key_count_source='memory' (a real
    evidence gap from the case) — quality_score() alone would land it in the
    AUTO_REPAIR band. make_case_gate_fn must run the real repair_key_to_agent
    loop internally and converge to PASS, not just report the pre-repair
    score."""
    gate_fn = make_case_gate_fn("sydney_move")
    result = gate_fn("sydney_move", {"commit_id": "key_to_agent"})
    assert result.route == "PASS"
    assert result.repair_attempts >= 1


def test_real_case_gate_fn_escalates_bond_claim_confirm():
    """bond_claim_confirm's real evidence has a refund-account name mismatch
    with verifiable_ext=False — must ESCALATE, never AUTO_REPAIR or PASS."""
    gate_fn = make_case_gate_fn("sydney_move")
    result = gate_fn("sydney_move", {"commit_id": "bond_claim_confirm"})
    assert result.route == "ESCALATE"


def test_real_case_gate_fn_bypasses_friend_compensation():
    gate_fn = make_case_gate_fn("sydney_move")
    result = gate_fn("sydney_move", {"commit_id": "friend_compensation"})
    assert result.route == "BYPASS_TO_HUMAN"


def test_real_case_loop_halts_at_bond_claim_without_calling_tool():
    """End-to-end with the real gate_fn/reason_fn/tool_fn: the loop must walk
    the real 8-commit sequence, execute the first four (all real PASS/
    AUTO_REPAIR->PASS results), and halt at bond_claim_confirm (ESCALATE)
    without ever invoking tool_fn for it."""
    executed = []
    real_tool_fn = make_case_tool_fn()

    def tracking_tool_fn(action):
        executed.append(action["commit_id"])
        return real_tool_fn(action)

    loop = GatedAgentLoop(
        gate_fn=make_case_gate_fn("sydney_move"),
        tool_fn=tracking_tool_fn,
        reason_fn=make_case_reason_fn("sydney_move"),
        max_steps=8,
    )
    trace = loop.run(context="sydney_move", initial_state={})

    assert executed == [
        "discard_items", "physical_handover", "key_to_building_manager", "key_to_agent",
    ]
    assert trace.halted_by_gate is True
    assert trace.steps[-1].route == "ESCALATE"
    assert trace.steps[-1].blocked is True
    assert "bond_claim_confirm" not in executed
