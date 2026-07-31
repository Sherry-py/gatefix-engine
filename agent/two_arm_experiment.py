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


@dataclass
class UngovernedStepResult:
    step: int
    commit_id: str
    ordering_violation: bool
    detail: str = ""


@dataclass
class UngovernedTrace:
    steps: list = field(default_factory=list)


class UngovernedArm:
    """跳过 gate，无条件调用 world.execute——reason_fn 提议什么就执行什么，
    包括违反 requires 的提案。OrderingViolation 被捕获、记录，但不会让循环
    停下：这条臂没有能停下来的机制，这正是要演示的失败模式。"""

    def __init__(self, world: SandboxWorld, reason_fn: ReasonFn, max_steps: int = 8):
        self.world = world
        self.reason_fn = reason_fn
        self.max_steps = max_steps

    def run(self) -> UngovernedTrace:
        trace = UngovernedTrace()
        state: dict = {}
        for _ in range(self.max_steps):
            action, _ = self.reason_fn(state)
            if action.get("type") == "finish":
                break
            commit_id = action["commit_id"]
            try:
                self.world.execute(commit_id)
                trace.steps.append(UngovernedStepResult(
                    step=len(trace.steps) + 1, commit_id=commit_id,
                    ordering_violation=False,
                ))
            except OrderingViolation as e:
                trace.steps.append(UngovernedStepResult(
                    step=len(trace.steps) + 1, commit_id=commit_id,
                    ordering_violation=True, detail=str(e),
                ))
            state.setdefault("history", []).append(
                {"action": action, "output": "executed"}
            )
        return trace


def run_comparison(case: str = "sydney_move", max_steps: int = 8) -> None:
    reason_fn = make_adversarial_reason_fn()

    governed_world = SandboxWorld(_load_commits_by_id(case))
    g_trace = GovernedArm(case, governed_world, reason_fn, max_steps=max_steps).run()

    ungoverned_world = SandboxWorld(_load_commits_by_id(case))
    u_trace = UngovernedArm(ungoverned_world, reason_fn, max_steps=max_steps).run()

    _print_report(g_trace, governed_world, u_trace, ungoverned_world)


def _print_report(g_trace: LoopTrace, g_world: SandboxWorld,
                   u_trace: UngovernedTrace, u_world: SandboxWorld) -> None:
    print("=" * 78)
    print("agent/two_arm_experiment.py —— governed vs ungoverned, same adversarial proposal")
    print("=" * 78)

    print("\ngoverned:")
    print(f"  attempted: {[s.tool for s in g_trace.steps]}")
    print(f"  halted_by_gate={g_trace.halted_by_gate}  final_output={g_trace.final_output!r}")
    g_state = g_world.read_state()
    print(f"  World end state: {g_state}")
    consistent = not any(rec.get("ordering_violated") for rec in g_state.values())
    print(f"  -> {'consistent' if consistent else 'CONTRADICTORY (unexpected!)'}")

    print("\nungoverned:")
    print(f"  attempted: {[s.commit_id for s in u_trace.steps]}")
    u_state = u_world.read_state()
    violations = [s for s in u_trace.steps if s.ordering_violation]
    print(f"  ordering violations not enforced: {len(violations)}")
    for v in violations:
        rec = u_state[v.commit_id]
        missing = rec.get("missing_requires", [])
        print(f"  {v.commit_id}: executed at seq={rec['seq']}, but requires "
              f"{missing} which hadn't executed yet at that point")
        for m in missing:
            m_seq = u_state.get(m, {}).get("seq")
            when = f"later, at seq={m_seq}" if m_seq else "never"
            print(f"    {m}: executed {when}")
    print(f"  World end state: {u_state}")
    contradictory = len(violations) > 0
    print(f"  -> {'contradictory (ran out of order)' if contradictory else 'consistent (unexpected!)'}")

    print("\nNote: this is a constructed adversarial proposal built to exercise the")
    print("new requires: mechanism, not a record of what happened in the real case —")
    print("see 'The gap, concretely' in the spec.")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description="Run the governed vs ungoverned two-arm sandbox comparison")
    parser.add_argument("--case", default="sydney_move")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    run_comparison(case=args.case, max_steps=args.max_steps)


if __name__ == "__main__":
    main()
