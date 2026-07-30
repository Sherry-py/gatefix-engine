"""
engine.py —— 运行时引擎：组装上下文 → LLM 推理提案（此 demo 里用真实历史决策代替）→
              Precondition 判定 → 三态路由 → 执行/升级人 → 写回

对应 commit_engine_core_v1.svg 那张最小骨架图的 6 个节点。
运行方式：
    python engine.py run --case=sydney_move
    python engine.py run --case=sydney_move --verbose

按 --case 动态加载三份场景配置（commits/<case>_commits.yaml、
bindings/<case>_bindings.yaml、evidence/<case>_evidence.yaml、
preconditions/<case>.py），engine.py 和 gate.py 本身不含任何场景特定逻辑。
目前只有 sydney_move 这一个场景跑通过——这是架构上支持多场景，
不是"已经在多个场景上验证过可复用"的实测结论。
"""

import argparse
import json
import importlib
from pathlib import Path
from datetime import datetime, timezone

import yaml

from gate import GateConfig, GateRecord

BASE_DIR = Path(__file__).parent


def _resolve_regular_commit(config, score_fn, repair_fn, evidence, verbose=False):
    """常规 commit 的三态路由 + AUTO_REPAIR 重试循环。从 run_case 抽成独立
    函数：一是不用为了测一个边界分支就构造一整个 case 的三份 yaml/py；二是
    修一个真实缺口——route=="AUTO_REPAIR" 但该 precondition_fn 没注册
    REPAIR_REGISTRY 补证函数时，原写法会让 AUTO_REPAIR 原样返回、从不收敛
    （route 只能是 PASS 或 ESCALATE，这不是合法终态）。sydney_move 案例里
    没有 commit 撞上这条路径，直到用真实但非案例内的 evidence 测 MCP
    server 的 authorize() 才发现。

    返回 (route, result, Q, dry_rounds, repair_attempts)，route 只会是
    PASS 或 ESCALATE。"""
    dry_rounds = 0
    repair_attempts = 0
    while True:
        result = score_fn(evidence)
        Q = config.quality_score(result["R"], result["C"], result["O"], result["Ro"])
        route = config.route(Q, result["verifiable_ext"], dry_rounds)

        if verbose or route != "AUTO_REPAIR":
            print(f"  R={result['R']:.2f} C={result['C']:.2f} O={result['O']:.2f} "
                  f"Ro={result['Ro']:.2f} → Q={Q:.3f}  route={route}  "
                  f"(dry_rounds={dry_rounds})")
            print(f"  说明: {result['notes']}")

        if route == "AUTO_REPAIR" and repair_fn is not None:
            repair_attempts += 1
            print(f"  [AUTO_REPAIR] 缺口可外部验证，尝试补证（第 {repair_attempts} 轮）...")
            new_evidence = repair_fn(evidence)
            if new_evidence == evidence:
                dry_rounds += 1  # 没有新证据
            else:
                evidence = new_evidence
                dry_rounds = 0  # 拿到新证据，重置计数
            if dry_rounds >= config.k_dry:
                route = "ESCALATE"
                print(f"  连续 {config.k_dry} 轮无新证据 → 强制 ESCALATE")
                break
            continue

        if route == "AUTO_REPAIR":
            # repair_fn 是 None：这个精度分数落进了"可以自动修"的区间，但这个
            # precondition_fn 没有注册对应的补证函数，没有自动化手段真的去
            # 补——不能停留在 AUTO_REPAIR 这个非终态，降级为 ESCALATE
            # （和 k_dry 耗尽时的处理方式一致）。
            route = "ESCALATE"
            print("  这个 precondition_fn 没有注册 REPAIR_REGISTRY 补证函数，"
                  "AUTO_REPAIR 无法自动执行 → 降级 ESCALATE")
        break

    return route, result, Q, dry_rounds, repair_attempts


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_number(v):
    """yaml 里的 'inf' 要转成 float('inf')。"""
    if isinstance(v, str) and v.strip().lower() == "inf":
        return float("inf")
    return v


def run_case(case: str, verbose: bool = False):
    commits_path = BASE_DIR / "commits" / f"{case}_commits.yaml"
    bindings_path = BASE_DIR / "bindings" / f"{case}_bindings.yaml"
    evidence_path = BASE_DIR / "evidence" / f"{case}_evidence.yaml"

    commits = load_yaml(commits_path)["commits"]
    bindings = load_yaml(bindings_path)["bindings"]
    all_evidence = load_yaml(evidence_path)

    # 场景特定的打分函数模块——preconditions/<case>.py 必须导出 REGISTRY
    # （必须）和 REPAIR_REGISTRY（可选）。这是"engine.py 不含场景逻辑"的
    # 落地方式：新场景只需要新增这个模块，不需要改这里的 import。
    precondition_module = importlib.import_module(f"preconditions.{case}")
    REGISTRY = precondition_module.REGISTRY
    REPAIR_REGISTRY = getattr(precondition_module, "REPAIR_REGISTRY", {})

    config = GateConfig()
    records = []

    print("=" * 78)
    print(f"GateFix engine.py —— case: {case}")
    print(f"GateConfig: tau_pass={config.tau_pass} tau_repair={config.tau_repair} "
          f"k_dry={config.k_dry} lambda={config.lambda_value} beta={config.beta_fix_cost}")
    print("=" * 78)

    total_certain_cost = 0.0
    total_risk_ext = 0.0

    for commit in commits:
        cid = commit["id"]
        name = commit["name_cn"]
        cost_reverse = parse_number(commit.get("cost_reverse"))
        value = parse_number(commit.get("value", 0))
        cost_fix = parse_number(commit.get("cost_fix", 0))
        binding = bindings.get(cid, {})
        evidence = dict(all_evidence.get(cid, {}))

        is_commit = config.is_commit(cost_reverse, value)
        loop_mode = config.loop_mode(cost_reverse, value, cost_fix)

        print(f"\n--- Commit: {name} ({cid}) ---")
        print(f"  不可逆性: {commit.get('irreversibility')}")
        print(f"  IsCommit(a)={is_commit}  LoopMode(a)={loop_mode}  "
              f"执行器={binding.get('executor', '未绑定')}")

        # ---------- 分支 1：人情类，直接旁路给人 ----------
        if commit.get("bypass_to_human"):
            print(f"  [旁路] {commit.get('bypass_reason')}")
            print("  → 不进入 4D-CQ 计算，直接呈人定夺（Human_Gate(a) 定义域之外）")
            rec = GateRecord(
                commit_id=cid, commit_name=name, R=0, C=0, O=0, Ro=0, Q=0,
                route="BYPASS_TO_HUMAN", is_commit=is_commit, loop_mode=loop_mode,
                verifiable_ext=False, dry_rounds=0,
                notes=commit.get("bypass_reason", ""), bypassed_to_human=True,
            )
            records.append(rec)
            continue

        # ---------- 分支 2：软 commit，走 expectation_gate ----------
        if commit.get("soft_commit"):
            score_fn = REGISTRY[commit["precondition_fn"]]
            result = score_fn(evidence)
            allowed = config.expectation_gate(
                result.get("contains_promise", True),
                result.get("has_feasibility_evidence", False),
            )
            route = "PASS" if allowed else "ESCALATE"
            print(f"  [软 commit / expectation gate] {result['notes']}")
            print(f"  Send(msg) 被允许 = {allowed}  → route={route}")
            rec = GateRecord(
                commit_id=cid, commit_name=name,
                R=result["R"], C=result["C"], O=result["O"], Ro=result["Ro"],
                Q=config.quality_score(result["R"], result["C"], result["O"], result["Ro"]),
                route=route, is_commit=is_commit, loop_mode=loop_mode,
                verifiable_ext=result["verifiable_ext"], dry_rounds=0,
                notes=result["notes"],
            )
            records.append(rec)
            if route == "PASS":
                total_certain_cost += value
            continue

        # ---------- 分支 3：常规 commit，走三态路由（可能带 AUTO_REPAIR 循环） ----------
        score_fn = REGISTRY[commit["precondition_fn"]]
        repair_fn = REPAIR_REGISTRY.get(commit["precondition_fn"])

        route, result, Q, dry_rounds, repair_attempts = _resolve_regular_commit(
            config, score_fn, repair_fn, evidence, verbose=verbose,
        )

        rec = GateRecord(
            commit_id=cid, commit_name=name,
            R=result["R"], C=result["C"], O=result["O"], Ro=result["Ro"], Q=Q,
            route=route, is_commit=is_commit, loop_mode=loop_mode,
            verifiable_ext=result["verifiable_ext"], dry_rounds=dry_rounds,
            notes=result["notes"] + (f"　[经过 {repair_attempts} 轮 AUTO_REPAIR]" if repair_attempts else ""),
        )
        rec.repair_attempts = repair_attempts

        # ---------- 外部或有闸门（如有）：不影响 route，只报告 ----------
        risk_ext_cfg = commit.get("risk_ext")
        if risk_ext_cfg:
            r = config.expected_external_risk(
                risk_ext_cfg["p_inspect"], risk_ext_cfg["loss_if_inspected"]
            )
            rec.risk_ext = r
            total_risk_ext += r
            print(f"  [外部或有闸门] p_inspect={risk_ext_cfg['p_inspect']} × "
                  f"Loss={risk_ext_cfg['loss_if_inspected']} → Risk_ext={r:.1f}  "
                  f"({risk_ext_cfg.get('note', '')})")
            print(f"  注意：route={route} 不代表这条风险已清零，Total_Cost = Cost_certain + Risk_ext")

        if route == "PASS":
            total_certain_cost += value
        elif route == "ESCALATE":
            print(f"  → 升级给人：{binding.get('executor', '委托人本人')} 终审")

        records.append(rec)

    # ---------- 写 Gate Record（JSONL） ----------
    out_path = BASE_DIR / "gate_record.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            entry = rec.to_dict()
            entry["case"] = case
            entry["timestamp"] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------- 汇总 ----------
    n_pass = sum(1 for r in records if r.route == "PASS")
    n_escalate = sum(1 for r in records if r.route in ("ESCALATE", "BYPASS_TO_HUMAN"))
    n_repair = sum(1 for r in records if getattr(r, "repair_attempts", 0) > 0)

    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"  Commit 总数: {len(records)}")
    print(f"  PASS: {n_pass}   ESCALATE/旁路人工: {n_escalate}   经过 AUTO_REPAIR: {n_repair}")
    print(f"  Total_Cost_certain（已确定成本，仅 PASS 的 value 求和）: ¥/${total_certain_cost:.0f}")
    print(f"  Total_Risk_ext（外部或有闸门期望敞口，Commit=True 之后仍未清零）: ¥{total_risk_ext:.1f}")
    print(f"  Gate Record 已写入: {out_path}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="GateFix engine")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="运行一个 case")
    run_parser.add_argument("--case", default="sydney_move", help="case 名（决定加载 commits/bindings/evidence/preconditions 四处的哪一套配置）")
    run_parser.add_argument("--verbose", action="store_true", help="打印每一轮 AUTO_REPAIR 的详情")

    args = parser.parse_args()
    if args.command == "run":
        run_case(args.case, verbose=args.verbose)


if __name__ == "__main__":
    main()
