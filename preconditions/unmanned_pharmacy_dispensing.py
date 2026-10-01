"""
preconditions/unmanned_pharmacy_dispensing.py —— 无人药房具身发药场景
（case=unmanned_pharmacy_dispensing）的 precondition 判定函数（Pᵢ(E,θᵢ) 的具体实现）

如实说明：和 cross_border_transfer 一样，这不是从真实个人案例转录来的 case——
sydney_move 至今仍是本仓库唯一的真实案例。这个 case 是假设性场景（法规依据
真实：《药品管理法》第五十八条处方药审核要求、《精神药品管理办法》管制类
药品实名/限量要求；"无人药房取药机器人视觉识别 SKU、机械臂取药"这个具体
形态是参照公开披露的无人药房商业化信息构造出来验证判定逻辑的，不代表任何
公司真实系统的实际实现）。加这个 case 的目的：验证"具身机器人执行、且直接
涉及公众用药安全"这类场景下，4D-CQ 四个维度该怎么落到具体的 evidence 字段上——
这是 sydney_move（人执行、实物交割）和 cross_border_transfer（agent 执行、
数据合规）之外，第三种性质的 commit 点：agent/机器人执行、直接的人身安全后果。

每个函数：输入这个 commit 的 evidence（一个 dict），输出：
    R, C, O, Ro       ——4D-CQ 四个维度的分数，每个 ∈ [0,1]
    verifiable_ext    ——这个 commit 如果证据不够，缺口能不能靠外部核查补齐
    notes             ——给人看的一句话解释，写进 Gate Record
"""


def score_otc_sku_dispense(evidence: dict) -> dict:
    """非处方药（OTC）发药。evidence 字段：sku_visual_confidence（视觉识别
    置信度，0-1）；barcode_cross_check_passed（有没有做条码复核这道独立验证
    锚点——决定 Relevance：纯视觉置信度再高，没有第二重验证手段，判定站不住
    满分）；expiry_date_verified（效期是否核查）；stock_deducted_before_dispense
    （库存扣减/记录是否发生在发药动作之前，决定 Ordering）；
    look_alike_drug_risk_assessed（相邻货位是否存在易混淆药品、有没有针对性
    评估过误取风险，决定 Robustness——即使这一次识别对了，系统性的误取风险
    有没有被兜住）。"""
    confidence = float(evidence.get("sku_visual_confidence", 0.0))
    has_barcode_check = bool(evidence.get("barcode_cross_check_passed"))
    has_expiry_check = bool(evidence.get("expiry_date_verified"))

    # Relevance：有独立验证锚点（条码复核）才算判定站得住；没有的话，
    # 单一视觉模型的自信程度打折计入——这不是怀疑模型能力，是"一个信号
    # 源无论多自信都不构成独立验证"这条设计原则。
    R = 1.0 if has_barcode_check else min(confidence * 0.85, 0.85)

    # Coverage：条码复核 + 效期核查两项证据是否齐备。
    if has_barcode_check and has_expiry_check:
        C = 1.0
    elif has_expiry_check:
        C = 0.5
    else:
        C = 0.2

    # Ordering：库存扣减/记录必须先于发药动作，不能是"先发药后补记录"。
    O = 1.0 if evidence.get("stock_deducted_before_dispense") else 0.4

    # Robustness：就算这一次识别/复核都过了，货架层面的易混淆风险有没有
    # 被评估过——这条风险不会因为单次判定通过而消失。
    Ro = 1.0 if evidence.get("look_alike_drug_risk_assessed") else 0.4

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        # 条码复核、效期核查都是"去扫一下/查一下就能确认"的事实缺口，
        # 不是专业判断本身没做完，值得给一次 AUTO_REPAIR 机会。
        verifiable_ext=True,
        notes=(
            f"视觉识别置信度={confidence:.2f}；"
            f"条码复核{'已通过' if has_barcode_check else '缺失'}/"
            f"效期核查{'已完成' if has_expiry_check else '缺失'}/"
            f"库存记录{'先于发药' if evidence.get('stock_deducted_before_dispense') else '晚于/未记录'}/"
            f"易混淆药品风险{'已评估' if evidence.get('look_alike_drug_risk_assessed') else '未评估'}"
            + ("" if has_barcode_check else " —— 缺条码复核这道独立验证锚点，值得先补一次再判定")
        ),
    )


def repair_otc_sku_dispense(evidence: dict) -> dict:
    """AUTO_REPAIR 的具体动作：对本次待发药品补做一次条码复核——这是可外部
    核查补齐的事实缺口本身的定义（去扫一下条码就能确认，不需要重新做判断），
    同 sydney_move 里 repair_key_to_agent、cross_border_transfer 里
    repair_send_pii_to_archival_vendor 的补证方式。"""
    new_evidence = dict(evidence)
    new_evidence["barcode_cross_check_passed"] = True
    return new_evidence


def score_prescription_dispense_pharmacist_review(evidence: dict) -> dict:
    """处方药发药——执业药师审核。evidence 字段：prescription_uploaded（是否
    有处方，决定 Relevance——无处方发处方药本身站不住，不是证据齐不齐的
    问题）；pharmacist_review_completed、drug_interaction_checked（执业药师
    审核是否完成、药物相互作用是否核查，共同决定 Coverage——《药品管理法》
    第五十八条要求的强制审核）；pharmacist_review_before_dispense（审核是否
    发生在发药动作之前，决定 Ordering——先发后补审核不算数）；
    prescription_source_verifiable（处方来源是否可核实真伪，比如接入医院
    电子处方平台校验，还是仅凭顾客自行上传的照片——决定 Robustness：即使
    审核流程走完了，处方本身被伪造的风险有没有被兜住）。"""
    has_prescription = bool(evidence.get("prescription_uploaded"))
    review_done = bool(evidence.get("pharmacist_review_completed"))
    interaction_checked = bool(evidence.get("drug_interaction_checked"))

    # Relevance：这次发药有没有处方这个前提本身站不站得住——不是"证据填得
    # 全不全"，是"这件事本身有没有合法依据"，同 cross_border_transfer 里
    # score_cross_border_transfer 对 R 的处理方式。
    R = 1.0 if has_prescription else 0.1

    C = 1.0 if (review_done and interaction_checked) else 0.2

    O = 1.0 if evidence.get("pharmacist_review_before_dispense") else 0.3

    Ro = 1.0 if evidence.get("prescription_source_verifiable") else 0.3

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        # 药师有没有真的审核，是《药品管理法》要求的专业判断本身，不是
        # "查一下文档系统"就能自动核实补齐的事实缺口——同
        # score_cross_border_transfer 对法律判断缺口的处理方式：直接需要
        # 药师/法务终审，不给 AUTO_REPAIR 机会。
        verifiable_ext=False,
        notes=(
            f"处方{'已上传' if has_prescription else '缺失'}；"
            f"执业药师审核{'已完成' if review_done else '未完成'}/"
            f"药物相互作用{'已核查' if interaction_checked else '未核查'}/"
            f"审核{'先于发药' if evidence.get('pharmacist_review_before_dispense') else '晚于/未记录'}/"
            f"处方来源真伪{'可核实' if evidence.get('prescription_source_verifiable') else '不可核实（仅凭上传照片）'}"
            + ("" if review_done and interaction_checked
               else " —— 执业药师审核未完成，需药师终审后才能发药，不因视觉识别准确率高而绕过")
        ),
    )


# 供 engine.py 动态查找函数名用
REGISTRY = {
    "score_otc_sku_dispense": score_otc_sku_dispense,
    "score_prescription_dispense_pharmacist_review": score_prescription_dispense_pharmacist_review,
}

REPAIR_REGISTRY = {
    "score_otc_sku_dispense": repair_otc_sku_dispense,
    # score_prescription_dispense_pharmacist_review 不注册：verifiable_ext=False
    # （专业判断缺口，不是可外部核查补齐的事实缺口），不存在自动重试路径。
}
