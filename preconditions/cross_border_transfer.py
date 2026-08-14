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


def _score_government_access_robustness(evidence: dict) -> float:
    """Ro（Robustness）判定，这个 case 里两个 commit 共用同一套标准：安全
    保障措施（SCC/TIA）签了、文件齐了，不代表目的地政府依职权调取数据的
    能力也随之消失——这是 Schrems II 判例之后 TIA 实务本身就要求覆盖的
    维度（有没有专门评估过目的地政府的强制调取权，比如美国 FISA 702、
    中国《数据安全法》域外调取条款；评估出高风险时有没有落地"补充措施"，
    比如加密且密钥不在目的地司法辖区内）。这条和 sydney_move 里
    `score_air_freight_dispatch` 的 Robustness 用法是同一种思路：不是看
    "有没有出问题"，是看"就算出了问题，前面做的评估/措施有没有真的把
    风险挡住"。

    evidence 字段：government_access_risk_assessed（是否专门评估过目的地
    政府调取风险）；residual_access_risk_level（评估结论，"low"/"high"）；
    supplementary_measures_applied（评估出高风险时，补充措施是否已落地）。
    """
    if not evidence.get("government_access_risk_assessed"):
        return 0.2  # 没评估过这道风险，等同于没做 Robustness 尽调
    if evidence.get("residual_access_risk_level") == "low":
        return 1.0
    if evidence.get("supplementary_measures_applied"):
        return 1.0  # 评估出高风险，但补充措施（如加密、密钥隔离）已落地
    return 0.3  # 评估出高风险，且补充措施未落地——残余风险真实存在，未被清零


def score_cross_border_transfer(evidence: dict) -> dict:
    """把用户个人数据发往第三国风控服务。evidence 字段：
    destination_adequacy_decision（目的地是否在欧盟委员会充分性认定名单上，
    决定 Relevance——这是这次传输有没有*任何*合法依据的前提）；
    scc_signed、tia_completed（标准合同条款是否已签署、传输影响评估是否已
    完成，两者共同决定 Coverage——充分性缺失时，这两项是 GDPR 第 46 条允许
    的替代性安全保障措施）；explicit_consent_obtained（用户是否已明确同意本
    次跨境传输，同样计入 Coverage）；consent_obtained_before_request（同意
    是否发生在传输请求之前，决定 Ordering——先斩后奏的同意不算数）；
    Robustness 字段见 `_score_government_access_robustness`。"""
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

    Ro = _score_government_access_robustness(evidence)

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
            f"用户明确同意{'已取得' if has_consent else '缺失'}；"
            f"目的地政府调取风险{'已评估' if evidence.get('government_access_risk_assessed') else '未评估'}"
            + ("" if (has_adequacy or has_safeguard) and has_consent
               else " —— 无充分性认定且替代性安全保障措施不全，需 DPO/法务核实合法传输依据后终审")
        ),
    )


def score_send_pii_to_archival_vendor(evidence: dict) -> dict:
    """把用户个人数据发往（美国）工单存档服务，作为申诉留痕流程的一部分。
    和 `score_cross_border_transfer` 判定同一类传输合法性，但缺口性质不同：
    SCC 已签署、TIA 评估本身已完成，只是 TIA 文档还没同步进这套 evidence
    pipeline（存在文档管理系统的另一处，客观事实，查一下就能确认）——这是
    "可外部核查补齐的事实缺口"，不是法律判断本身没做完，所以（不同于
    `score_cross_border_transfer`）这个 commit 值得给一次 AUTO_REPAIR 机会。
    新增 evidence 字段：tia_document_locatable_in_system（TIA 文档是否已在
    这套 evidence pipeline 能读到的文档系统里）。"""
    has_adequacy = bool(evidence.get("destination_adequacy_decision"))
    tia_assessed = bool(evidence.get("scc_signed")) and bool(evidence.get("tia_completed"))
    tia_on_file = tia_assessed and bool(evidence.get("tia_document_locatable_in_system"))
    has_consent = bool(evidence.get("explicit_consent_obtained"))

    R = 1.0 if (has_adequacy or tia_assessed) else 0.15
    C = 1.0 if (tia_on_file and has_consent) else 0.2
    O = 1.0 if evidence.get("consent_obtained_before_request") else 0.4
    Ro = _score_government_access_robustness(evidence)

    # 唯一被认定为"可外部核查补齐"的缺口类型：SCC 已签、TIA 评估本身已完成，
    # 只是文档没同步进系统——去查文档系统就能确认，不需要法务重新做判断。
    # 除此之外（没有充分性认定、没签 SCC、TIA 本身没做、没拿到用户同意）都是
    # 实质性的法律判断缺口，不给自动重试机会。
    verifiable_ext = tia_assessed and not tia_on_file

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=verifiable_ext,
        notes=(
            f"SCC{'已签署' if evidence.get('scc_signed') else '缺失'}/"
            f"TIA{'评估已完成' if tia_assessed else '评估未完成'}/"
            f"TIA 文档{'已在系统中可查' if tia_on_file else '未同步进系统'}/"
            f"用户明确同意{'已取得' if has_consent else '缺失'}；"
            f"目的地政府调取风险{'已评估' if evidence.get('government_access_risk_assessed') else '未评估'}"
            + (" —— TIA 已完成但文档未同步，值得先查一遍文档系统再判定"
               if verifiable_ext else "")
        ),
    )


def repair_send_pii_to_archival_vendor(evidence: dict) -> dict:
    """AUTO_REPAIR 的具体动作：去查文档管理系统，把已完成但未同步的 TIA
    文档找出来——这是可外部核查补齐的事实缺口本身的定义，不是猜一个更好看
    的分数。找到之后 Coverage 缺口随之消失，同 sydney_move 里
    `repair_key_to_agent` 的补证方式。"""
    new_evidence = dict(evidence)
    new_evidence["tia_document_locatable_in_system"] = True
    return new_evidence


# 供 engine.py 动态查找函数名用
REGISTRY = {
    "score_cross_border_transfer": score_cross_border_transfer,
    "score_send_pii_to_archival_vendor": score_send_pii_to_archival_vendor,
}

REPAIR_REGISTRY = {
    # score_cross_border_transfer 不注册：它的证据缺口被显式标记为
    # verifiable_ext=False（法律判断，不是可外部核查补齐的事实缺口），
    # 不存在可以自动重试的路径，故意不给补证函数。
    "score_send_pii_to_archival_vendor": repair_send_pii_to_archival_vendor,
}
