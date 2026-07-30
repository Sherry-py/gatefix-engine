"""
agent/langgraph_loop.py —— 用 LangGraph 的 StateGraph 重新表达同一套
reason → gate → act 编排，作为 gatefix-engine 的第四种部署形态（CLI /
agent/gated_loop.py::GatedAgentLoop / mcp_server/server.py 之后）。

不引入任何新的判定逻辑：gate 节点直接调用
agent/gated_loop.py::resolve_precondition()——和 CLI、GatedAgentLoop、MCP
server 用的是同一个函数。这个文件只换了一层编排壳。

四个节点：
    planner       按 commits.yaml 声明的顺序，取出下一个待授权的 commit
                  （和 gated_loop.py::make_case_reason_fn 一样，是最小实现，
                  不是真 planner——本仓库没有真正的推理/规划步骤，见该模块
                  docstring）。
    gate          真实判定：bypass_to_human 的 commit 直接判 BYPASS_TO_HUMAN，
                  其余调 resolve_precondition()（AUTO_REPAIR 在内部收敛，
                  外部只看到 PASS 或非 PASS）。
    executor      只有 route == "PASS" 才会被调用，记录"已执行"。
    human_review  route != "PASS" 时进入，调用 LangGraph 的 interrupt() 真正
                  暂停图执行，等待外部通过 Command(resume=...) 恢复。这是这
                  层编排壳相对 GatedAgentLoop 多出来的能力：GatedAgentLoop
                  遇到非 PASS 直接 return，这里是可恢复的暂停，不是终止。

    重要边界：human_review 恢复后只是把人类的回复记录进 processed 历史，
    不会把这个回复当成"批准"去改判定结果或调用 executor——route != PASS
    时 executor 绝不会被调用，这个契约和 GatedAgentLoop 完全一样，不因为
    多了 interrupt/resume 就出现"人一说话就放行"的旁路。真正要把 ESCALATE
    翻成 PASS，只能是补齐证据后重新走一次 gate 判定，不是在这里加特权。
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, TypedDict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

from gate import GateConfig  # noqa: E402
from engine import load_yaml  # noqa: E402
from agent.gated_loop import GateResult, resolve_precondition  # noqa: E402


class LoopState(TypedDict):
    case: str
    remaining: list
    processed: list
    current_commit_id: Optional[str]
    gate_result: Optional[dict]
    halted: bool
    final_output: Optional[str]


def build_graph(case: str):
    """真实接线：读 commits/<case>_commits.yaml、evidence/<case>_evidence.yaml、
    preconditions/<case>.py 的 REGISTRY/REPAIR_REGISTRY——跟
    gated_loop.py::make_case_gate_fn 读的是同一份配置，不是另一套。"""
    config = GateConfig()
    precondition_module = importlib.import_module(f"preconditions.{case}")
    registry = precondition_module.REGISTRY
    repair_registry = getattr(precondition_module, "REPAIR_REGISTRY", {})

    commits_path = BASE_DIR / "commits" / f"{case}_commits.yaml"
    evidence_path = BASE_DIR / "evidence" / f"{case}_evidence.yaml"
    commits_by_id = {c["id"]: c for c in load_yaml(commits_path)["commits"]}
    all_evidence = load_yaml(evidence_path)

    def planner(state: LoopState) -> dict:
        if not state["remaining"]:
            return {"current_commit_id": None}
        next_id = state["remaining"][0]
        return {"current_commit_id": next_id, "remaining": state["remaining"][1:]}

    def route_after_planner(state: LoopState) -> str:
        return "gate" if state["current_commit_id"] else "done"

    def gate(state: LoopState) -> dict:
        commit_id = state["current_commit_id"]
        commit = commits_by_id[commit_id]

        if commit.get("bypass_to_human"):
            result = GateResult(
                route="BYPASS_TO_HUMAN", R=0, C=0, O=0, Ro=0, Q=0,
                verifiable_ext=False, reason=commit.get("bypass_reason", ""),
            )
        else:
            score_fn = registry[commit["precondition_fn"]]
            repair_fn = repair_registry.get(commit["precondition_fn"])
            evidence = dict(all_evidence.get(commit_id, {}))
            result = resolve_precondition(
                config, score_fn, evidence,
                repair_fn=repair_fn,
                soft_commit=bool(commit.get("soft_commit")),
            )

        gate_result = asdict(result)
        gate_result["commit_id"] = commit_id
        gate_result["commit_name"] = commit["name_cn"]
        return {"gate_result": gate_result}

    def route_after_gate(state: LoopState) -> str:
        return "executor" if state["gate_result"]["route"] == "PASS" else "human_review"

    def executor(state: LoopState) -> dict:
        gr = state["gate_result"]
        entry = {
            "commit_id": gr["commit_id"], "route": "PASS", "reason": gr["reason"],
            "repair_attempts": gr["repair_attempts"],
        }
        return {
            "processed": state["processed"] + [entry],
            "current_commit_id": None,
            "gate_result": None,
        }

    def human_review(state: LoopState) -> dict:
        gr = state["gate_result"]
        # 暂停在这里，等外部用 Command(resume=...) 提供人工回复；恢复后
        # decision 拿到的就是 resume 的值。这个值只被记录，不会用来放行。
        decision = interrupt({
            "commit_id": gr["commit_id"],
            "commit_name": gr["commit_name"],
            "route": gr["route"],
            "reason": gr["reason"],
        })
        entry = {
            "commit_id": gr["commit_id"], "route": gr["route"],
            "reason": gr["reason"], "human_decision": decision,
        }
        return {
            "processed": state["processed"] + [entry],
            "halted": True,
            "final_output": f"BLOCKED[{gr['route']}]: {gr['reason'] or 'gate did not authorize this action'}",
        }

    graph = StateGraph(LoopState)
    graph.add_node("planner", planner)
    graph.add_node("gate", gate)
    graph.add_node("executor", executor)
    graph.add_node("human_review", human_review)

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_after_planner, {"gate": "gate", "done": END})
    graph.add_conditional_edges("gate", route_after_gate, {"executor": "executor", "human_review": "human_review"})
    graph.add_edge("executor", "planner")
    graph.add_edge("human_review", END)

    return graph.compile(checkpointer=InMemorySaver())


def make_initial_state(case: str) -> LoopState:
    commits_path = BASE_DIR / "commits" / f"{case}_commits.yaml"
    ordered_ids = [c["id"] for c in load_yaml(commits_path)["commits"]]
    return LoopState(
        case=case, remaining=ordered_ids, processed=[],
        current_commit_id=None, gate_result=None,
        halted=False, final_output=None,
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the GateFix judgment chain as a LangGraph StateGraph")
    parser.add_argument("--case", default="sydney_move")
    parser.add_argument("--resume-with", default="reviewed, still blocked pending evidence",
                         help="模拟人工在 interrupt 处回复的内容")
    args = parser.parse_args()

    app = build_graph(args.case)
    thread = {"configurable": {"thread_id": f"{args.case}-cli-run"}}

    print("=" * 78)
    print(f"agent/langgraph_loop.py —— StateGraph judgment chain, case: {args.case}")
    print("=" * 78)

    result = app.invoke(make_initial_state(args.case), thread)
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"  [INTERRUPT] commit={payload['commit_id']!r} route={payload['route']} "
              f"reason={payload['reason']!r}")
        print(f"  [RESUME]    人工回复 / human reply -> {args.resume_with!r}")
        result = app.invoke(Command(resume=args.resume_with), thread)

    for entry in result["processed"]:
        status = "OK" if entry["route"] == "PASS" else "BLOCKED"
        print(f"  commit={entry['commit_id']!r} route={entry['route']} [{status}]")

    print(f"\n  halted={result['halted']}")
    print(f"  final_output={result['final_output']!r}")
    print("=" * 78)


if __name__ == "__main__":
    main()
