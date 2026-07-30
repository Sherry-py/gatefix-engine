"""Tests for agent/langgraph_loop.py — the LangGraph StateGraph rewiring of
the same reason -> gate -> act orchestration as agent/gated_loop.py.

Same fidelity philosophy as test_gated_loop.py: no fakes here, this wires
build_graph("sydney_move") to the REAL commits/evidence/preconditions and
asserts the exact same trajectory already proven for GatedAgentLoop and the
MCP server — discard_items / physical_handover / key_to_building_manager /
key_to_agent all real PASS (key_to_agent via a real internal AUTO_REPAIR
round), then a real interrupt at bond_claim_confirm (ESCALATE) that halts the
graph without ever routing that commit through executor.

Core contract, unchanged from GatedAgentLoop: route != PASS means executor
never runs for that commit. Resuming the interrupt with a human reply must
not flip this — resume only gets recorded onto the halted entry, it is never
treated as authorization.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command  # noqa: E402

from agent.langgraph_loop import build_graph, make_initial_state  # noqa: E402


def _run_to_completion(case: str, thread_id: str, resume_with: str = "reviewed, still blocked"):
    app = build_graph(case)
    thread = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(make_initial_state(case), thread)
    interrupts_seen = []
    while "__interrupt__" in result:
        interrupts_seen.append(result["__interrupt__"][0].value)
        result = app.invoke(Command(resume=resume_with), thread)
    return result, interrupts_seen


def test_real_graph_walks_sydney_move_to_the_known_trajectory():
    result, interrupts_seen = _run_to_completion("sydney_move", "test-full-trajectory")

    pass_entries = [e for e in result["processed"] if e["route"] == "PASS"]
    assert [e["commit_id"] for e in pass_entries] == [
        "discard_items", "physical_handover", "key_to_building_manager", "key_to_agent",
    ]

    # key_to_agent's real evidence starts with key_count_source="memory" —
    # only a real internal AUTO_REPAIR round converges it to PASS.
    key_to_agent_entry = next(e for e in pass_entries if e["commit_id"] == "key_to_agent")
    assert key_to_agent_entry["repair_attempts"] >= 1

    assert len(interrupts_seen) == 1
    assert interrupts_seen[0]["commit_id"] == "bond_claim_confirm"
    assert interrupts_seen[0]["route"] == "ESCALATE"

    assert result["halted"] is True
    assert "BLOCKED[ESCALATE]" in result["final_output"]


def test_bond_claim_confirm_never_reaches_executor():
    """The non-negotiable contract: a non-PASS route must never produce a
    PASS entry for that commit, no matter what the resumed human reply is."""
    result, _ = _run_to_completion(
        "sydney_move", "test-no-passthrough", resume_with="approved, go ahead")

    bond_claim_entries = [e for e in result["processed"] if e["commit_id"] == "bond_claim_confirm"]
    assert len(bond_claim_entries) == 1
    assert bond_claim_entries[0]["route"] == "ESCALATE"
    assert bond_claim_entries[0]["human_decision"] == "approved, go ahead"
    # the resume reply is recorded, never used to synthesize a PASS entry
    assert not any(e["commit_id"] == "bond_claim_confirm" and e["route"] == "PASS"
                    for e in result["processed"])


def test_interrupt_payload_carries_the_real_escalation_reason():
    """The interrupt is meant to be readable by a human — assert the reason
    text is the real one produced by preconditions/sydney_move.py, not a
    placeholder."""
    _, interrupts_seen = _run_to_completion("sydney_move", "test-interrupt-payload")
    assert "退款账户户名" in interrupts_seen[0]["reason"]
