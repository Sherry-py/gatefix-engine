"""
preconditions/pharmacy_dispensing.py —— 自动配药柜（ADC）override 场景
（case=pharmacy_dispensing）的 precondition 判定函数（Pᵢ(E,θᵢ) 的具体实现）

如实说明：这是本仓库第一个判定依据直接取自真实、已公开审理案件（不是构造
情节、也不是私人案例转录）的 case——2017-12-26 范德堡大学医学中心护士
RaDonda Vaught 通过 ADC override 给患者 Charlene Murphey 错误注射
vecuronium（应给 Versed）致其死亡，2022 年被判过失杀人罪与重大疏忽罪成立。
真实事故细节见 commits/pharmacy_dispensing_commits.yaml 文件头与 README
「Case notes」。加这个 case 的目的：验证"引擎领域无关"这条架构主张能否覆盖
第三种、也是第一种"物理世界执行、且真实造成不可逆伤害后果"的领域——
sydney_move 是人执行实物交割，cross_border_transfer 是 agent 发起数据决策，
这个 case 是人操作自动化设备执行不可逆给药动作。

每个函数：输入这个 commit 的 evidence（一个 dict），输出：
    R, C, O, Ro       ——4D-CQ 四个维度的分数，每个 ∈ [0,1]
    verifiable_ext    ——这个 commit 如果证据不够，缺口能不能靠外部核查补齐
    notes             ——给人看的一句话解释，写进 Gate Record
"""


def score_pharmacy_dispensing_override(evidence: dict) -> dict:
    """通过 ADC override 发放并给予高警示药品。evidence 字段：
    drug_name_matches_verified_order（override 命中的药品是否与医嘱药品一致，
    决定 Relevance——这是"要给的到底是不是这个药"的前提，真实事故里这一条
    就是 False：命中的是 vecuronium，医嘱是 Versed）；
    high_alert_drug_confirmation_completed、patient_identity_barcode_scanned
    （高警示药品的强制附加确认、给药前身份核对，两者共同决定 Coverage——
    这两项是给药动作本该具备的独立安全层）；
    order_verified_in_system_before_override（医嘱是否在触发 override 之前
    已完成系统核验，决定 Ordering——先斩后奏的 override 不算数，真实事故里
    正是医嘱还没收录才触发了 override）；
    override_search_used_full_drug_name（搜索是否用了足够长的药品名，决定
    Robustness——搜索字符太短是导致误选药品的直接触发条件之一）。"""
    name_matches = bool(evidence.get("drug_name_matches_verified_order"))
    has_high_alert_check = bool(evidence.get("high_alert_drug_confirmation_completed"))
    has_id_check = bool(evidence.get("patient_identity_barcode_scanned"))
    order_verified_first = bool(evidence.get("order_verified_in_system_before_override"))
    full_name_search = bool(evidence.get("override_search_used_full_drug_name"))

    # Relevance：override 命中的药品当下是不是要给这个患者的那个药——不是
    # "证据填得全不全"，是"这个给药动作本身站不站得住"，真实事故里这一条
    # 就是致命缺口本身。
    R = 1.0 if name_matches else 0.1

    # Coverage：高警示药品的强制附加确认 + 给药前身份核对，两个独立安全层
    # 缺一不可。
    C = 1.0 if (has_high_alert_check and has_id_check) else 0.2

    # Ordering：医嘱核验必须先于 override 发生，不能是"先 override 后核实"。
    O = 1.0 if order_verified_first else 0.4

    # Robustness：override 搜索是否遵循了足够长的药品名输入这条流程安全措施
    # （ISMP 事后建议 ≥5 字符）——不像 cross_border_transfer 里 Ro 恒为 1.0
    # （README 里标注"待收紧"），这个 case 让 Robustness 真正随证据变化。
    Ro = 1.0 if full_name_search else 0.3

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        # 药品身份是否真的匹配这个患者当下的医嘱，是需要药师/第二核对护士
        # 现场核实的临床判断，不是"去查一下系统"就能自动补齐的事实缺口——
        # 高警示药品的给药动作，证据不够就该停，不给自动重试机会（同
        # score_cross_border_transfer 对法律判断类缺口的处理方式）。
        verifiable_ext=False,
        notes=(
            f"药品名称与医嘱{'一致' if name_matches else '不一致'}；"
            f"高警示药品确认{'已完成' if has_high_alert_check else '缺失'}/"
            f"身份条码核对{'已完成' if has_id_check else '缺失'}/"
            f"医嘱核验{'先于' if order_verified_first else '未先于'} override 完成"
            + ("" if name_matches and has_high_alert_check and has_id_check
               else " —— 药品身份或安全核对缺口未闭合，需药师/第二核对护士现场核实后终审")
        ),
    )


# 供 engine.py 动态查找函数名用
REGISTRY = {
    "score_pharmacy_dispensing_override": score_pharmacy_dispensing_override,
}

# 无 REPAIR_REGISTRY：这个 case 唯一的 commit 的证据缺口被显式标记为
# verifiable_ext=False（临床身份核实判断，不是可外部核查补齐的事实缺口），
# 不存在可以自动重试的路径，故意不注册任何补证函数（同 cross_border_transfer）。
