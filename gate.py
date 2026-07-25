"""
gate.py —— GateFix 的判定核心（对应"系统设计公式与可调参数表.md"里的核心公式 + 六条子公式）

这个文件只做一件事：判定。不碰 evidence 怎么收集（engine.py 管），
不碰某个 commit 具体怎么打分（preconditions/*.py 管）。

核心不变式（对应 Commit(a,E) = Human_Gate(a) ∧ ⋀ᵢ Pᵢ(E,θᵢ)）：
    一个动作能不能放行 = 人是否已批准 ∧ 证据在 R/C/O/Ro 四个维度上是否都过阈值。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GateConfig:
    """可调参数表的代码化版本——改这里的数字，整个系统的判定行为就变。"""

    tau_pass: float = 0.85        # ① 放行阈值
    tau_repair: float = 0.50      # ① 自动修复区间下界
    w_relevance: float = 0.25     # ① 4D-CQ 四个维度权重，Σw = 1
    w_coverage: float = 0.25
    w_ordering: float = 0.25
    w_robustness: float = 0.25
    lambda_value: float = 1.0     # ③ 可逆性判定的价值倍数
    beta_fix_cost: float = 50.0   # ④ on/in-loop 划界的修复成本上限
    k_dry: int = 3                # ② AUTO_REPAIR 连续无新证据的轮数上限（loop-until-dry）

    # ---------- ① 4D-CQ 质量分 ----------
    def quality_score(self, R: float, C: float, O: float, Ro: float) -> float:
        return (
            self.w_relevance * R
            + self.w_coverage * C
            + self.w_ordering * O
            + self.w_robustness * Ro
        )

    # ---------- ② 三态路由函数 ----------
    def route(self, q: float, verifiable_ext: bool, dry_rounds: int = 0) -> str:
        if q >= self.tau_pass:
            return "PASS"
        if self.tau_repair <= q < self.tau_pass and verifiable_ext:
            if dry_rounds >= self.k_dry:
                return "ESCALATE"  # 连续 k_dry 轮无新证据，不再自动重试
            return "AUTO_REPAIR"
        return "ESCALATE"

    # ---------- ③ Commit 判定（可逆性分类） ----------
    def is_commit(self, cost_reverse: float, value: float) -> bool:
        """cost_reverse 用 float('inf') 表示完全不可逆。"""
        if cost_reverse == float("inf"):
            return True
        return cost_reverse > self.lambda_value * value

    # ---------- ④ On/In-the-loop 划界 ----------
    def loop_mode(self, cost_reverse: float, value: float, cost_fix: float) -> str:
        if (not self.is_commit(cost_reverse, value)) and cost_fix <= self.beta_fix_cost:
            return "ON_THE_LOOP"
        return "IN_THE_LOOP"

    # ---------- ⑤ 软 commit 门（expectation gate） ----------
    @staticmethod
    def expectation_gate(contains_promise: bool, has_feasibility_evidence: bool) -> bool:
        """Send(msg) 被允许 ⟺ ¬ContainsPromise(msg) ∨ HasFeasibilityEvidence(msg)"""
        return (not contains_promise) or has_feasibility_evidence

    # ---------- ⑥ 外部或有闸门（新增，海关抽查风险场景） ----------
    @staticmethod
    def expected_external_risk(p_inspect: float, loss_if_inspected: float) -> float:
        """Risk_ext(a) = p_inspect(a) · Loss(a∣inspected)
        这不是放行判定的一部分——Commit(a,E)=True 之后这条风险依然存在，
        只用于 Total_Cost 核算，提醒"放行"和"成本已确定"是两件事。"""
        return p_inspect * loss_if_inspected


@dataclass
class GateRecord:
    """对应案例里的 Gate Record：JSONL·批准人·事后结果——留痕，不是为了合规负担，是结算期权。"""

    commit_id: str
    commit_name: str
    R: float
    C: float
    O: float
    Ro: float
    Q: float
    route: str
    is_commit: bool
    loop_mode: str
    verifiable_ext: bool
    dry_rounds: int
    notes: str = ""
    risk_ext: Optional[float] = None
    bypassed_to_human: bool = False

    def to_dict(self) -> dict:
        return {
            "commit_id": self.commit_id,
            "commit_name": self.commit_name,
            "R": round(self.R, 3),
            "C": round(self.C, 3),
            "O": round(self.O, 3),
            "Ro": round(self.Ro, 3),
            "Q": round(self.Q, 3),
            "route": self.route,
            "is_commit": self.is_commit,
            "loop_mode": self.loop_mode,
            "verifiable_ext": self.verifiable_ext,
            "dry_rounds": self.dry_rounds,
            "notes": self.notes,
            "risk_ext": self.risk_ext,
            "bypassed_to_human": self.bypassed_to_human,
        }
