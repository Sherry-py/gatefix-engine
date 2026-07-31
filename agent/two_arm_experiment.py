"""
agent/two_arm_experiment.py —— governed vs ungoverned，同一个对抗性提案，
只切 gate 开关。见 docs/superpowers/specs/2026-07-31-two-arm-sandbox-
experiment-design.md 完整设计。

不改 gate.py / engine.py / agent/gated_loop.py 一行——check_sequence_
precondition 是新的硬性前置检查，make_governed_gate_fn 用组合的方式把它接
在 make_case_gate_fn（未改动）前面，不是改写判定逻辑。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from engine import load_yaml  # noqa: E402
from agent.gated_loop import (  # noqa: E402
    GateFn,
    GateResult,
    GatedAgentLoop,
    LoopTrace,
    ReasonFn,
    make_case_gate_fn,
)
from world.sydney_move_world import OrderingViolation, SandboxWorld  # noqa: E402


def _load_commits_by_id(case: str) -> dict:
    commits_path = BASE_DIR / "commits" / f"{case}_commits.yaml"
    return {c["id"]: c for c in load_yaml(commits_path)["commits"]}


def check_sequence_precondition(world: SandboxWorld, commit: dict) -> Optional[GateResult]:
    """在真正调用 world.execute() 之前跑：读 world.read_state()，检查这个
    commit 声明的 requires 是否都已经真的执行过。任何一个没满足 → 返回一个
    阻断的 GateResult（route="ORDERING"），不进入 world.execute()，也不
    影响 R/C/O/Ro 打分——这是硬性前置，不是证据质量判定的一部分。全部满足
    （或者没有声明 requires）→ 返回 None，调用方继续走原有的
    resolve_precondition 判定路径。"""
    requires = commit.get("requires")
    if not requires:
        return None
    state = world.read_state()
    missing = [r for r in requires if not state.get(r, {}).get("executed")]
    if not missing:
        return None
    return GateResult(
        route="ORDERING", R=0, C=0, O=0, Ro=0, Q=0,
        verifiable_ext=False,
        reason=f"requires {', '.join(missing)} (not executed yet)",
    )


def make_governed_gate_fn(case: str, world: SandboxWorld) -> GateFn:
    """把 make_case_gate_fn（未改动的既有判定逻辑）包一层：先跑
    check_sequence_precondition，挡住的直接返回；没挡住的原样交给
    make_case_gate_fn。gate.py / engine.py / agent/gated_loop.py 一行都
    没改——这是组合，不是修改。"""
    base_gate_fn = make_case_gate_fn(case)
    commits_by_id = _load_commits_by_id(case)

    def gate_fn(context: str, action: dict) -> GateResult:
        commit = commits_by_id[action["commit_id"]]
        blocked = check_sequence_precondition(world, commit)
        if blocked is not None:
            return blocked
        return base_gate_fn(context, action)

    return gate_fn


ADVERSARIAL_ORDER = [
    "discard_items",
    "physical_handover",
    "bond_claim_confirm",
    "key_to_building_manager",
    "key_to_agent",
    "friend_compensation",
    "air_freight_dispatch",
]


def make_adversarial_reason_fn() -> ReasonFn:
    """写死的对抗性提案，governed/ungoverned 两臂共用同一个——唯一"错误"是
    把 bond_claim_confirm 提到两次钥匙交接之前。不是随机、不是 LLM，可
    复现、可解释（见 spec 的 Alternatives considered and rejected）。这个
    提案本身从没在现实里发生过——是构造出来测试新机制的输入，不是历史
    重现（见 spec"The gap, concretely"一节）。"""

    def reason_fn(state: dict):
        processed = {h["action"].get("commit_id") for h in state.get("history", [])}
        remaining = [cid for cid in ADVERSARIAL_ORDER if cid not in processed]
        if not remaining:
            return {"type": "finish", "output": "adversarial proposal exhausted"}, 0
        next_id = remaining[0]
        return {"type": "tool", "tool": next_id, "commit_id": next_id}, 0

    return reason_fn


class GovernedArm:
    """route==PASS 才调用 world.execute；ORDERING 硬性阻断在
    make_governed_gate_fn 里生效。世界永远不会在 requires 未满足时被这条臂
    调用——这是靠 GatedAgentLoop 现有的"非 PASS 绝不调 tool_fn"契约保证的，
    这个类自己没有写任何新规则。"""

    def __init__(self, case: str, world: SandboxWorld, reason_fn: ReasonFn,
                 max_steps: int = 8):
        self.world = world
        self.loop = GatedAgentLoop(
            gate_fn=make_governed_gate_fn(case, world),
            tool_fn=self._tool_fn,
            reason_fn=reason_fn,
            max_steps=max_steps,
        )

    def _tool_fn(self, action: dict):
        self.world.execute(action["commit_id"])
        return f"executed {action['commit_id']}", 1

    def run(self) -> LoopTrace:
        return self.loop.run(context="sydney_move", initial_state={})
