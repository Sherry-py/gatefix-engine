"""
preconditions/cross_border_transfer.py —— 跨境个人数据传输场景（case=cross_border_transfer）
的 precondition 判定函数（Pᵢ(E,θᵢ) 的具体实现）

如实说明：这是本仓库第一个不是从真实个人案例转录来的 case——sydney_move 至今
仍是唯一的真实案例，这个 case 是假设性场景（法理依据真实：TikTok 因 EU→China
传输被爱尔兰 DPC 处罚 €530M，2025；GDPR 第五章为现行法规。具体情节"客服 Agent
准备把用户数据发往中国境内风控服务"是基于该判例法理构造出来的，不是任何公司
真实发生过的事）。加这个 case 的目的：README「换场景怎么复用」一节说"引擎领域
无关、配置领域相关"是架构设计、尚未被第二个场景验证过——这个 case 就是第一次
验证：sydney_move 是人执行、实物交割的场景，这个 case 是 agent 执行、数据合规
的场景，两者除了共用 gate.py/engine.py 之外没有任何代码耦合。

每个函数：输入这个 commit 的 evidence（一个 dict），输出：
    R, C, O, Ro       ——4D-CQ 四个维度的分数，每个 ∈ [0,1]
    verifiable_ext    ——这个 commit 如果证据不够，缺口能不能靠外部核查补齐
    notes             ——给人看的一句话解释，写进 Gate Record
"""


def score_cross_border_transfer(evidence: dict) -> dict:
    """把用户个人数据发往第三国风控服务。evidence 字段：
    destination_adequacy_decision（目的地是否在欧盟委员会充分性认定名单上，
    决定 Relevance——这是这次传输有没有*任何*合法依据的前提）；
    scc_signed、tia_completed（标准合同条款是否已签署、传输影响评估是否已
    完成，两者共同决定 Coverage——充分性缺失时，这两项是 GDPR 第 46 条允许
    的替代性安全保障措施）；explicit_consent_obtained（用户是否已明确同意本
    次跨境传输，同样计入 Coverage）；consent_obtained_before_request（同意
    是否发生在传输请求之前，决定 Ordering——先斩后奏的同意不算数）。"""
    has_adequacy = bool(evidence.get("destination_adequacy_decision"))
    has_safeguard = bool(evidence.get("scc_signed")) and bool(evidence.get("tia_completed"))
    has_consent = bool(evidence.get("explicit_consent_obtained"))

    # Relevance：这次传输当下有没有任何 GDPR 第五章认可的合法依据
    # （充分性认定，或者充分性缺失时的替代性安全保障措施）——不是"证据填得全不全"，
    # 是"这个传输这件事本身站不站得住"，所以只看 has_adequacy/has_safeguard，不看 consent。
    R = 1.0 if (has_adequacy or has_safeguard) else 0.15

    # Coverage：安全保障措施的证据文件是否齐备——SCC、TIA、用户同意三者缺一不可。
    C = 1.0 if (has_safeguard and has_consent) else 0.2

    # Ordering：同意必须先于传输请求发生，不能是"先传后补同意"。
    O = 1.0 if evidence.get("consent_obtained_before_request") else 0.4

    # Robustness：本 case 无对应证据字段区分，待收紧（同 sydney_move 里若干函数的处理方式）。
    Ro = 1.0

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        # 目的地是否充分、现有安全保障措施是否足以替代充分性认定，是法律判断，
        # 不是"去查一下文档系统"就能自动补齐的事实缺口——所以不给 AUTO_REPAIR
        # 机会，直接需要 DPO/法务核实（同 score_bond_claim 里"户名不符"的处理方式）。
        verifiable_ext=False,
        notes=(
            f"目的地充分性认定={'有' if has_adequacy else '无'}；"
            f"SCC{'已签署' if evidence.get('scc_signed') else '缺失'}/"
            f"TIA{'已完成' if evidence.get('tia_completed') else '缺失'}/"
            f"用户明确同意{'已取得' if has_consent else '缺失'}"
            + ("" if (has_adequacy or has_safeguard) and has_consent
               else " —— 无充分性认定且替代性安全保障措施不全，需 DPO/法务核实合法传输依据后终审")
        ),
    )


# 供 engine.py 动态查找函数名用
REGISTRY = {
    "score_cross_border_transfer": score_cross_border_transfer,
}

# 无 REPAIR_REGISTRY：这个 case 唯一的 commit 的证据缺口被显式标记为
# verifiable_ext=False（法律判断，不是可外部核查补齐的事实缺口），
# 不存在可以自动重试的路径，故意不注册任何补证函数。
