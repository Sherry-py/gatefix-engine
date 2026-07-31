"""Tests for world/sydney_move_world.py and agent/two_arm_experiment.py —
the two-arm (governed vs ungoverned) sandbox comparison. See
docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md for
the full design and the boundary invariant this code must not violate:
gate.py / engine.py / agent/gated_loop.py are never modified — the sequence
check is composed on top of make_case_gate_fn, not merged into it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world.sydney_move_world import OrderingViolation, SandboxWorld


# ---------- SandboxWorld ----------

def test_world_execute_succeeds_when_requires_met():
    world = SandboxWorld({"a": {"id": "a"}, "b": {"id": "b", "requires": ["a"]}})
    world.execute("a")
    record = world.execute("b")
    assert record["executed"] is True
    assert record["ordering_violated"] is False
    assert world.read_state()["b"]["ordering_violated"] is False


def test_world_raises_ordering_violation_on_unmet_requires():
    """The world still records the execution even though it raises — see the
    module docstring in world/sydney_move_world.py for why. This is not a
    bug: the world reflects what really happened, it doesn't gatekeep."""
    world = SandboxWorld({"a": {"id": "a"}, "b": {"id": "b", "requires": ["a"]}})
    try:
        world.execute("b")
        assert False, "expected OrderingViolation"
    except OrderingViolation as e:
        assert e.commit_id == "b"
        assert e.missing == ["a"]
    state = world.read_state()
    assert state["b"]["executed"] is True
    assert state["b"]["ordering_violated"] is True
    assert state["b"]["missing_requires"] == ["a"]


from gate import GateConfig
from agent.gated_loop import GateResult
from agent.two_arm_experiment import (
    check_sequence_precondition,
    make_governed_gate_fn,
    _load_commits_by_id,
)


# ---------- check_sequence_precondition ----------

def test_check_sequence_precondition_blocks_on_unmet_requires():
    world = SandboxWorld({"a": {"id": "a"}})
    commit = {"id": "b", "requires": ["a"]}
    result = check_sequence_precondition(world, commit)
    assert result is not None
    assert result.route == "ORDERING"
    assert "a" in result.reason


def test_check_sequence_precondition_allows_when_requires_met():
    world = SandboxWorld({"a": {"id": "a"}})
    world.execute("a")
    commit = {"id": "b", "requires": ["a"]}
    assert check_sequence_precondition(world, commit) is None


def test_check_sequence_precondition_allows_when_no_requires_declared():
    world = SandboxWorld({"a": {"id": "a"}})
    commit = {"id": "a"}
    assert check_sequence_precondition(world, commit) is None


# ---------- make_governed_gate_fn ----------

def test_governed_gate_fn_blocks_before_reaching_base_gate_fn():
    """bond_claim_confirm's real evidence would ESCALATE anyway (refund
    account name mismatch — see tests/test_engine.py) — this test proves the
    ORDERING block happens first and for the right reason, not that it just
    happens to also fail downstream."""
    commits_by_id = _load_commits_by_id("sydney_move")
    world = SandboxWorld(commits_by_id)
    gate_fn = make_governed_gate_fn("sydney_move", world)

    result = gate_fn("sydney_move", {"commit_id": "bond_claim_confirm"})
    assert result.route == "ORDERING"

    # key_to_building_manager/key_to_agent have their own requires
    # (discard_items, physical_handover) — satisfy the whole chain for real
    # rather than reaching in and faking just the top-level dependency.
    world.execute("discard_items")
    world.execute("physical_handover")
    world.execute("key_to_building_manager")
    world.execute("key_to_agent")
    result = gate_fn("sydney_move", {"commit_id": "bond_claim_confirm"})
    assert result.route == "ESCALATE"  # falls through to the real, unrelated evidence check


from agent.two_arm_experiment import (
    ADVERSARIAL_ORDER,
    GovernedArm,
    make_adversarial_reason_fn,
)


# ---------- make_adversarial_reason_fn ----------

def test_adversarial_reason_fn_proposes_bond_before_keys():
    """The one deliberate 'mistake': bond_claim_confirm at position 3,
    before either key-handover commit. Fixed and named, not random."""
    assert ADVERSARIAL_ORDER == [
        "discard_items", "physical_handover", "bond_claim_confirm",
        "key_to_building_manager", "key_to_agent", "friend_compensation",
        "air_freight_dispatch",
    ]

    reason_fn = make_adversarial_reason_fn()
    state = {"history": []}
    proposed = []
    for _ in range(len(ADVERSARIAL_ORDER) + 1):
        action, _ = reason_fn(state)
        if action.get("type") == "finish":
            break
        proposed.append(action["commit_id"])
        state["history"].append({"action": action, "output": "x"})
    assert proposed == ADVERSARIAL_ORDER


# ---------- GovernedArm（spec 里点名的测试） ----------

def test_governed_arm_blocks_on_ordering_before_execution():
    world = SandboxWorld(_load_commits_by_id("sydney_move"))
    arm = GovernedArm("sydney_move", world, make_adversarial_reason_fn())
    trace = arm.run()

    assert trace.halted_by_gate is True
    assert trace.steps[-1].route == "ORDERING"
    assert trace.steps[-1].tool == "bond_claim_confirm"

    state = world.read_state()
    assert "bond_claim_confirm" not in state  # 从没走到 world.execute
    assert state["discard_items"]["executed"] is True
    assert state["physical_handover"]["executed"] is True


from agent.two_arm_experiment import UngovernedArm


def test_ungoverned_arm_reaches_contradictory_state():
    """bond_claim_confirm's prerequisites (key_to_building_manager,
    key_to_agent) DO eventually execute later in the same run — the
    adversarial order only moves bond_claim_confirm early, it doesn't
    remove the other steps. The contradiction isn't "the prerequisites
    never ran" — it's that bond_claim_confirm ran *before* they did, which
    is exactly what `requires` means and exactly what the seq counter
    proves. (This corrects the spec's example report text, which claimed
    the prerequisites stay unexecuted — traced by hand, that's wrong; see
    the top of this plan.)"""
    world = SandboxWorld(_load_commits_by_id("sydney_move"))
    arm = UngovernedArm(world, make_adversarial_reason_fn())
    trace = arm.run()
    state = world.read_state()

    assert len(trace.steps) == 7
    assert [s.commit_id for s in trace.steps] == ADVERSARIAL_ORDER

    assert state["bond_claim_confirm"]["executed"] is True
    assert state["bond_claim_confirm"]["ordering_violated"] is True
    assert state["bond_claim_confirm"]["missing_requires"] == [
        "key_to_building_manager", "key_to_agent",
    ]

    # 前置条件后来确实也执行了——只是晚了，没用了
    assert state["key_to_building_manager"]["executed"] is True
    assert state["key_to_agent"]["executed"] is True

    # 拿证据说话，不是靠叙事：bond_claim_confirm 的 seq 比它 requires 的
    # 那两步更小——它真的抢在前面发生了
    assert state["bond_claim_confirm"]["seq"] < state["key_to_building_manager"]["seq"]
    assert state["bond_claim_confirm"]["seq"] < state["key_to_agent"]["seq"]
