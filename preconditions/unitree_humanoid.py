"""
preconditions/unitree_humanoid.py —— 宇树人形机器人公开事故（case=unitree_humanoid）
的 precondition 判定函数（Pᵢ(E,θᵢ) 的具体实现）

如实说明：和 cross_border_transfer（假设场景，法理真实）、
unmanned_pharmacy_dispensing（假设场景，法规真实）都不同——这个 case 不是
构造出来的，是对两起已被公开报道的真实事故做事后的 commit-gate 拆解，反推
"如果当时用这套框架看这个动作，证据够不够格放行"。反推不是马后炮式的苛责——
是尽调该问的问题：这几项证据缺口，公司在事故之后有没有实际补上，还是只是
舆论平息了。详细信源见 commits/unitree_humanoid_commits.yaml 文件头。

每个函数：输入这个 commit 的 evidence（一个 dict），输出：
    R, C, O, Ro       ——4D-CQ 四个维度的分数，每个 ∈ [0,1]
    verifiable_ext    ——这个 commit 如果证据不够，缺口能不能靠外部核查补齐
    notes             ——给人看的一句话解释，写进 Gate Record
"""


def score_high_speed_track_deviation(evidence: dict) -> dict:
    """高速开放场景运动执行（2025-08-15 事故原型）。evidence 字段：
    test_coverage_includes_high_speed_human_interaction（高速运动中的人机
    交互场景是否被测试覆盖，决定 Relevance——连测试场景都没覆盖，谈不上有
    把握判定"这个动作在人员在场时执行是安全的"）；
    real_time_path_deviation_detection、emergency_stop_triggered_before_contact
    （是否有实时路径偏离检测、急停是否发生在接触之前，共同决定 Coverage 和
    Ordering）。"""
    tested = bool(evidence.get("test_coverage_includes_high_speed_human_interaction"))
    deviation_detected = bool(evidence.get("real_time_path_deviation_detection"))
    estop_before_contact = bool(evidence.get("emergency_stop_triggered_before_contact"))

    # Relevance：这个动作"在人员在场时高速执行是安全的"这个判断本身站不站
    # 得住——不是证据填得全不全，是有没有测试场景支撑这个判断的前提。
    R = 1.0 if tested else 0.15

    # Coverage：实时偏离检测 + 急停能力两项证据是否齐备。
    C = 1.0 if (deviation_detected and estop_before_contact) else 0.2

    # Ordering：急停必须发生在接触之前，不能是"撞完之后才反应"。
    O = 1.0 if estop_before_contact else 0.2

    # Robustness：就算前面都过了，"偏离赛道后进入人员密集区"这个系统性风险
    # 有没有被真正兜住——已经真实发生碰撞，说明没有。
    Ro = 1.0 if (deviation_detected and estop_before_contact) else 0.2

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        # 测试场景覆盖够不够、有没有实时中止机制，是安全工程判断本身，
        # 不是"查一下文档系统"就能核实补齐的事实缺口——同
        # score_prescription_dispense_pharmacist_review 对专业判断缺口的处理
        # 方式：不给 AUTO_REPAIR，需要工程团队实质性补上这几项能力后重新评估，
        # 不是这套判定引擎能自动验证的事。
        verifiable_ext=False,
        notes=(
            f"高速人机交互场景测试覆盖：{'有' if tested else '无（据报道 2000 小时测试未覆盖此场景）'}；"
            f"实时路径偏离检测：{'有' if deviation_detected else '无公开证据'}；"
            f"接触前急停触发：{'有' if estop_before_contact else '无（碰撞已发生）'}"
            + ("" if tested and deviation_detected and estop_before_contact
               else " —— 这几项在事故发生当时均不成立，需工程团队实质性补上并经独立验证后才能重新评估，不是自动可判定的事")
        ),
    )


def score_sensor_ambiguity_stabilization(evidence: dict) -> dict:
    """传感器信号矛盾时的姿态修正执行（2025-05 事故原型）。evidence 字段：
    conflicting_sensor_signal_detected_as_anomaly（系统能不能把"信号矛盾"
    本身识别为异常状态，而不是直接采信并放大，决定 Relevance——这是这条
    修正逻辑"设计上是否安全"的前提）；correction_force_rate_bounded、
    fallback_to_safe_stop_on_signal_conflict（修正力度是否有硬性上限、信号
    矛盾时是否回退到安全停止，共同决定 Coverage 和 Ordering）；
    root_cause_publicly_confirmed（根因是否已被公开承认，计入 Coverage——
    诚实定位问题比什么都不说要好，但不等于约束机制已经修复）。"""
    detects_conflict = bool(evidence.get("conflicting_sensor_signal_detected_as_anomaly"))
    force_bounded = bool(evidence.get("correction_force_rate_bounded"))
    fallback_safe_stop = bool(evidence.get("fallback_to_safe_stop_on_signal_conflict"))
    root_cause_confirmed = bool(evidence.get("root_cause_publicly_confirmed"))

    # Relevance：这套修正逻辑"设计上不会因为信号矛盾而失控"这个判断站不站
    # 得住——已经真实发生剧烈甩动，说明不成立。
    R = 1.0 if detects_conflict else 0.15

    # Coverage：力度上限约束是核心证据；公开承认根因算一点加分，但远不够。
    C = 0.3 if (root_cause_confirmed and not force_bounded) else (
        1.0 if force_bounded else 0.1)

    # Ordering：回退到安全停止必须先于力度继续加码，不能是"甩到停不下来
    # 才罢休"。
    O = 1.0 if fallback_safe_stop else 0.2

    # Robustness：就算单次信号矛盾的识别和上限约束都做对了，系统性的
    # "传感器信号互相矛盾"场景有没有被真正兜住——已经真实发生，说明没有。
    Ro = 1.0 if (force_bounded and fallback_safe_stop) else 0.2

    return dict(
        R=R, C=C, O=O, Ro=Ro,
        # 同上一个 commit：修正力度有没有上限、有没有安全回退，是姿态控制
        # 算法设计本身要不要重做的问题，不是外部核查一下文档就能补齐的事实
        # 缺口。
        verifiable_ext=False,
        notes=(
            f"信号矛盾识别为异常：{'有' if detects_conflict else '无（张力信号被直接采信为持续跌倒）'}；"
            f"修正力度上限约束：{'有' if force_bounded else '无（持续加码直至剧烈甩动）'}；"
            f"信号矛盾时回退安全停止：{'有' if fallback_safe_stop else '无'}；"
            f"根因是否已公开承认：{'是' if root_cause_confirmed else '否'}"
            + ("" if force_bounded and fallback_safe_stop
               else " —— 承认根因不等于约束机制已修复，需工程团队实质性补上力度上限/安全回退并经独立验证")
        ),
    )


# 供 engine.py 动态查找函数名用
REGISTRY = {
    "score_high_speed_track_deviation": score_high_speed_track_deviation,
    "score_sensor_ambiguity_stabilization": score_sensor_ambiguity_stabilization,
}

# 两个 commit 都不注册 REPAIR_REGISTRY：verifiable_ext=False，这是安全工程/
# 测试覆盖的实质判断，不存在"查一下就能自动补齐"的路径。
REPAIR_REGISTRY = {}
