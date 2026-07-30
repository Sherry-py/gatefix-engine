"""
mcp_server/server.py —— 把 GateFix 的 4D-CQ gate 包成一个 MCP server。

暴露两个 tool：
  - list_precondition_functions(case="sydney_move")：列出这个 case 里有哪些
    precondition_fn 可以被 authorize() 调用，每个附带对应 commit 的名字、
    是否 soft_commit、有没有 AUTO_REPAIR，以及打分函数的 docstring（里面
    写了这个函数期望什么样的 evidence 字段）。
  - authorize(case, precondition_fn, evidence)：对调用方传入的 evidence
    做真实判定——调用的是 preconditions.<case>.REGISTRY[precondition_fn]，
    走 agent/gated_loop.py 里 resolve_precondition() 那套真实三态路由 +
    AUTO_REPAIR 重试循环（soft_commit 型走 expectation_gate）。返回的
    route 只会是 PASS / ESCALATE / BYPASS_TO_HUMAN 之一。

这是"活证据"版本，不是案例回放：evidence 由调用方（任何 MCP client）在每
次调用时提供，不读 evidence/sydney_move_evidence.yaml 里的静态数据，所以
能真的挡在别的 agent 动作前面——前提是那个动作的证据形状匹配
preconditions/sydney_move.py 里某个已有的打分函数；只认得这 7 个，不是
能判断任意领域动作的通用 gate。

bypass_to_human 的 commit（如 friend_compensation）没有 precondition_fn，
不会出现在 list_precondition_functions 里，也没法通过 authorize() 判定——
人情类证据本来就该直接交给人，不该假装能被 evidence-based gate 自动判定。

仍然是 LLM-free、确定性：不调用任何模型/外部 API，判定过程和 CLI/agent
loop 完全一样可审计、可复现。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from gate import GateConfig  # noqa: E402
from engine import load_yaml  # noqa: E402
from agent.gated_loop import resolve_precondition  # noqa: E402

mcp = FastMCP("gatefix-gate")


def _case_precondition_index(case: str) -> dict:
    """precondition_fn -> {commit_id, name_cn, soft_commit} 的映射，从
    commits/<case>_commits.yaml 里真实读出来（不是编的）。bypass_to_human
    的 commit 没有 precondition_fn 字段，天然不会进这个索引。"""
    commits_path = BASE_DIR / "commits" / f"{case}_commits.yaml"
    commits = load_yaml(commits_path)["commits"]
    index = {}
    for c in commits:
        fn_name = c.get("precondition_fn")
        if fn_name:
            index[fn_name] = {
                "commit_id": c["id"],
                "name_cn": c["name_cn"],
                "soft_commit": bool(c.get("soft_commit")),
            }
    return index


@mcp.tool()
def list_precondition_functions(case: str = "sydney_move") -> list[dict]:
    """列出 <case> 里所有可被 authorize() 调用的 precondition_fn，附带
    对应 commit 的名字、是否 soft_commit、有没有 AUTO_REPAIR，以及打分
    函数的 docstring（说明期望的 evidence 字段）。调用 authorize() 之前
    应该先调这个，搞清楚要传什么样的 evidence。"""
    module = importlib.import_module(f"preconditions.{case}")
    index = _case_precondition_index(case)
    repair_registry = getattr(module, "REPAIR_REGISTRY", {})
    out = []
    for fn_name, meta in index.items():
        fn = module.REGISTRY[fn_name]
        out.append({
            "precondition_fn": fn_name,
            "commit_id": meta["commit_id"],
            "commit_name": meta["name_cn"],
            "soft_commit": meta["soft_commit"],
            "has_auto_repair": fn_name in repair_registry,
            "doc": (fn.__doc__ or "").strip(),
        })
    return out


@mcp.tool()
def authorize(case: str, precondition_fn: str, evidence: dict) -> dict:
    """对调用方提交的 evidence 做真实的 4D-CQ 判定。返回 route
    （PASS / ESCALATE / BYPASS_TO_HUMAN，AUTO_REPAIR 已在内部收敛掉）、
    authorized（route == "PASS" 的布尔值，方便调用方直接判断能不能继续）、
    R/C/O/Ro/Q、verifiable_ext、repair_attempts、reason。

    核心契约：route != "PASS" 时，调用这个 tool 的 agent 绝不能把对应的
    动作当作已授权去执行——这和 agent/gated_loop.py 里 GatedAgentLoop 的
    契约完全一样，只是这次判定发生在 MCP 协议边界的另一侧。"""
    module = importlib.import_module(f"preconditions.{case}")
    index = _case_precondition_index(case)
    if precondition_fn not in module.REGISTRY:
        raise ValueError(
            f"unknown precondition_fn={precondition_fn!r} for case={case!r}; "
            "call list_precondition_functions first to see what's available"
        )

    meta = index.get(precondition_fn, {})
    score_fn = module.REGISTRY[precondition_fn]
    repair_fn = getattr(module, "REPAIR_REGISTRY", {}).get(precondition_fn)

    config = GateConfig()
    result = resolve_precondition(
        config, score_fn, evidence,
        repair_fn=repair_fn,
        soft_commit=meta.get("soft_commit", False),
    )
    return {
        "route": result.route,
        "authorized": result.route == "PASS",
        "R": result.R, "C": result.C, "O": result.O, "Ro": result.Ro, "Q": result.Q,
        "verifiable_ext": result.verifiable_ext,
        "repair_attempts": result.repair_attempts,
        "reason": result.reason,
    }


if __name__ == "__main__":
    mcp.run()
