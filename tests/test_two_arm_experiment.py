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
