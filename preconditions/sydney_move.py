"""
preconditions/sydney_move.py —— 悉尼退租案例（case=sydney_move）的 precondition 判定函数
（Pᵢ(E,θᵢ) 的具体实现）

每个函数：输入这个 commit 的 evidence（一个 dict），输出：
    R, C, O, Ro       ——4D-CQ 四个维度的分数，每个 ∈ [0,1]
    verifiable_ext    ——这个 commit 如果证据不够，缺口能不能靠外部核查补齐（AUTO_REPAIR 的前提）
    notes             ——给人看的一句话解释，写进 Gate Record

换个领域：新增 preconditions/<new_case>.py（导出同样结构的 REGISTRY，
REPAIR_REGISTRY 可选）+ commits/<new_case>_commits.yaml +
bindings/<new_case>_bindings.yaml + evidence/<new_case>_evidence.yaml，
用 --case=<new_case> 跑。engine.py 通过 importlib 按 case 名动态加载
这四处，不需要改 import。

注意：这是架构上的设计（engine.py/gate.py 不含场景特定逻辑），目前只有
sydney_move 这一个场景实际跑通过验证——"换场景不用改引擎"还没有第二个
真实场景实测过，读到这里的人不要把它当成已验证的复用性结论。
"""


def score_discard_items(evidence: dict) -> dict:
    O = 1.0 if evidence.get("friend_selected_done") and evidence.get("organizer_selected_done") else 0.4
    C = 1.0 if evidence.get("id_documents_removed") and evidence.get("personal_info_papers_removed") else 0.3
    R, Ro = 1.0, 1.0
    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=True,
        notes="Ordering: 朋友挑完→整理师挑完→才扔；Coverage: 证件/含个人信息纸张已排除",
    )


def score_physical_handover(evidence: dict) -> dict:
    C = 1.0 if evidence.get("payment_received") and evidence.get("buyer_identity_confirmed") else 0.3
    O = 1.0 if evidence.get("pickup_window_confirmed") and evidence.get("building_access_aligned_with_organizer") else 0.4
    R, Ro = 1.0, 1.0
    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=True,
        notes="款项已收、取件人身份确认、楼宇进出安排与整理师时间窗对齐",
    )


def score_key_to_building_manager(evidence: dict) -> dict:
    R = 1.0 if evidence.get("agent_written_consent") else 0.2
    C = O = Ro = 1.0
    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=True,
        notes=f"中介书面同意此安排，来源：{evidence.get('consent_source', '未提供')}",
    )


def score_key_to_agent(evidence: dict) -> dict:
    """终极 commit：钥匙移交中介。这是案例里"审批界面价值不在按钮，在按钮旁边摆了什么"的第一个体现——
    钥匙数量如果只凭记忆，Relevance 必须打低分，即使 Coverage（有没有拍照）看起来齐全。"""
    photos_ok = bool(evidence.get("empty_house_photos_timestamped"))
    key_count_ok = evidence.get("key_count_matches") is True
    C = 1.0 if (photos_ok and key_count_ok) else (0.6 if photos_ok else 0.2)

    source_ok = evidence.get("key_count_source") == "agent_email"
    R = 1.0 if source_ok else 0.4  # 数量信息来源必须是中介邮件而非记忆

    O = 1.0 if evidence.get("cleaning_before_power_off") else 0.3
    Ro = 1.0 if evidence.get("utility_meter_photos") else 0.5

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=True,  # 缺口可以靠"重新查一遍中介邮件"外部验证 → 值得先 AUTO_REPAIR
        notes=(
            f"Coverage: 空房照{'齐全' if photos_ok else '缺失'}/钥匙数量{'核对通过' if key_count_ok else '未核对'}；"
            f"Relevance: 数量来源={evidence.get('key_count_source')}；"
            f"Ordering: 清洁{'先于' if evidence.get('cleaning_before_power_off') else '未确认先于'}断电；"
            f"Robustness: 水电表{'已存档' if evidence.get('utility_meter_photos') else '未存档'}"
        ),
    )


def repair_key_to_agent(evidence: dict) -> dict:
    """AUTO_REPAIR 的具体动作：去重新查一遍中介邮件，把'记忆'这个薄弱来源换成'中介邮件'。
    这个函数模拟的就是案例里真实发生过的动作。"""
    new_evidence = dict(evidence)
    new_evidence["key_count_source"] = "agent_email"
    new_evidence["key_count_matches"] = True
    return new_evidence


def score_bond_claim(evidence: dict) -> dict:
    """本案最具代表性的 gate：RBO 退款账户户名 ≠ 委托人姓名。
    只弹"确认退款账户？[是/否]"的系统会被直接点是；这里把"户名不符"这个 Relevance 信号显式打低分，
    且 verifiable_ext=False——因为这个缺口只能靠人核实关系，不能靠外部核查补齐，不会走 AUTO_REPAIR。"""
    approved = set(evidence.get("approved_deductions", []))
    actual = set(evidence.get("deductions", []))
    scope_ok = actual.issubset(approved)
    name_match = evidence.get("refund_account_name") == evidence.get("client_name")

    R = 1.0 if (scope_ok and name_match) else 0.15
    C = 1.0 if scope_ok else 0.2
    O = 1.0
    Ro = 1.0 if evidence.get("refund_account_still_active") else 0.3

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=False,  # 户名不符只能靠人核实关系
        notes=(
            f"扣除项 {'⊆ 约定集合，通过' if scope_ok else '超出约定集合，不通过'}；"
            f"退款账户户名={evidence.get('refund_account_name')!r} "
            f"{'匹配' if name_match else '≠ 委托人姓名 ' + repr(evidence.get('client_name')) + '——需人工核实关系'}"
        ),
    )


def score_expectation_setting(evidence: dict) -> dict:
    """软 commit：不走 4D-CQ quality_score，走 gate.py 里的 expectation_gate。
    这里仍返回 R/C/O/Ro 是为了保持接口一致、方便 engine.py 统一处理和记录，
    但真正决定放行与否的是 contains_promise / has_feasibility_evidence 这两个布尔量。"""
    has_evidence = bool(evidence.get("feasibility_probe_done")) and bool(evidence.get("feasibility_evidence"))
    R = 1.0 if has_evidence else 0.2
    C = O = Ro = 1.0
    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=True,
        notes=f"承诺金额={evidence.get('promised_amount')}；可实现性证据={evidence.get('feasibility_evidence', '无')}",
        contains_promise=True,
        has_feasibility_evidence=has_evidence,
    )


def score_air_freight_dispatch(evidence: dict) -> dict:
    """空运纸箱交运（7 月新增的真实事件）：一箱已裂 → 材质缺陷是批次属性，不是孤例，
    Robustness 分数取决于"剩余薄箱是否已按这个推断加固"，而不是只看有没有裂开。"""
    remaining_thin = evidence.get("thin_boxes_count", 0) - evidence.get("cracked_boxes_count", 0)
    reinforced = evidence.get("reinforced_boxes_count", 0)
    coverage_ok = reinforced >= remaining_thin

    C = 1.0 if coverage_ok else 0.4
    cracked = evidence.get("cracked_boxes_count", 0) > 0
    Ro = 1.0 if (not cracked) or (cracked and coverage_ok) else 0.4  # 有裂但没把风险传导给剩余箱→低分
    R = 1.0 if evidence.get("weight_charge_verified") and evidence.get("balance_formula_verified") else 0.3
    O = 1.0

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        verifiable_ext=True,
        notes=(
            f"{evidence.get('cracked_boxes_count')}箱已裂→材质缺陷判定为批次属性；"
            f"剩余{remaining_thin}箱薄箱中{reinforced}箱已加固（{'覆盖' if coverage_ok else '未覆盖全部'}）；"
            f"超重费/尾款公式均已核实"
        ),
    )


# 供 engine.py 动态查找函数名用
REGISTRY = {
    "score_discard_items": score_discard_items,
    "score_physical_handover": score_physical_handover,
    "score_key_to_building_manager": score_key_to_building_manager,
    "score_key_to_agent": score_key_to_agent,
    "score_bond_claim": score_bond_claim,
    "score_expectation_setting": score_expectation_setting,
    "score_air_freight_dispatch": score_air_freight_dispatch,
}

REPAIR_REGISTRY = {
    "score_key_to_agent": repair_key_to_agent,
}
