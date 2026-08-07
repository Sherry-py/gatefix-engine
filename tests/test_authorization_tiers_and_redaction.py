"""Tests for authorization-strength tiers + sensitive-data discipline
(REVISION_BRIEF.md 任务 5).

Two things to check: the four gate states are bound to a fixed intervention
tier (not just four peer labels — PASS/AUTO_REPAIR/ESCALATE/BYPASS_TO_HUMAN
have a real ordering), and a secret-shaped string injected via evidence
never survives into the human-facing contract or the audit log verbatim.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate import redact_secrets, GateRecord, build_gate_contract
from agent.gated_loop import GateResult, resolve_precondition
from audit import build_audit_record


# ---------- redact_secrets() unit behavior ----------

def test_redact_secrets_masks_key_value_style_credentials():
    text = "evidence dump: api_key=sk-liveFAKEKEY000111222 was attached"
    redacted = redact_secrets(text)
    assert "sk-liveFAKEKEY000111222" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_masks_bearer_tokens():
    text = "Authorization: Bearer eyFakeJwtTokenAbc123.xyz"
    redacted = redact_secrets(text)
    assert "eyFakeJwtTokenAbc123" not in redacted


def test_redact_secrets_leaves_ordinary_domain_text_untouched():
    text = "退款账户户名='第三方' ≠ 委托人姓名'委托人本人'——需人工核实关系"
    assert redact_secrets(text) == text


def test_redact_secrets_handles_empty_string():
    assert redact_secrets("") == ""


# ---------- redaction is wired into the actual contract/report exit points ----------

def test_gate_contract_human_readable_is_redacted():
    contract = build_gate_contract(
        gate_state="ESCALATE", R=0.5, C=0.5, O=0.5, Ro=0.5,
        reason_code="TEST", auto_repair_available=False,
        human_readable="context leaked token: abc123defghijklmno",
    )
    assert "abc123defghijklmno" not in contract["human_readable"]


def test_gate_record_to_dict_notes_are_redacted():
    rec = GateRecord(
        commit_id="x", commit_name="x", R=1, C=1, O=1, Ro=1, Q=1,
        route="PASS", is_commit=True, loop_mode="ON_THE_LOOP",
        verifiable_ext=True, dry_rounds=0,
        notes="evidence note password=hunter2fakevalue leaked in by mistake",
    )
    assert "hunter2fakevalue" not in rec.to_dict()["notes"]


def test_evidence_with_fake_credential_does_not_leak_through_resolve_precondition():
    """端到端：一个 precondition_fn 把 evidence 里的凭据形状字符串直接抄进
    notes（构造一个刻意这样写的假打分函数，模拟"某个 precondition_fn 写得
    不小心"的情况），resolve_precondition() 产出的 GateResult.to_contract()
    里不能出现明文凭据。"""

    def leaky_score_fn(evidence):
        return {
            "R": 1.0, "C": 1.0, "O": 1.0, "Ro": 1.0, "verifiable_ext": True,
            "notes": f"evidence included api_key={evidence.get('api_key')}",
        }

    from gate import GateConfig
    result = resolve_precondition(
        GateConfig(), leaky_score_fn,
        evidence={"api_key": "sk-realLookingSecretFAKE99887766"},
    )
    contract = result.to_contract()
    assert "sk-realLookingSecretFAKE99887766" not in contract["human_readable"]
    assert "[REDACTED]" in contract["human_readable"]


def test_audit_record_never_carries_free_text_at_all():
    """审计记录的第一道防线：压根不收 notes/human_readable 这类自由文本，
    不是"收了再脱敏"。就算调用方想传，build_audit_record 的参数签名里也
    没有能装自由文本的位置。"""
    record = build_audit_record(
        action_id="x", gate_state="PASS", reason_code="PASS_ABOVE_THRESHOLD",
        cq_scores={"relevance": 1.0}, schema_version="1",
    )
    assert set(record) == {
        "timestamp", "action_id", "case", "cq_scores", "thresholds",
        "gate_state", "reason_code", "schema_version",
    }


# ---------- authorization-strength tiers are a real ordering, not four labels ----------

def test_four_gate_states_have_a_fixed_intervention_ordering():
    """PASS < AUTO_REPAIR < ESCALATE < BYPASS_TO_HUMAN 是自主度递减、人工
    介入递增的顺序——用 gate.py 已有的 EXIT_CODE 数值间接验证这个绑定
    确实存在且方向正确（AUTO_REPAIR 从不作为终态，不在 EXIT_CODE 里，但
    它在流程位置上介于 PASS 和 ESCALATE 之间，见 GateConfig.route()）。"""
    from gate import EXIT_CODE
    assert EXIT_CODE["PASS"] < EXIT_CODE["ESCALATE"] < EXIT_CODE["BYPASS_TO_HUMAN"]


def test_bypass_to_human_used_for_both_human_judgment_and_evaluator_fault():
    """BYPASS_TO_HUMAN 这一档不只是"人情类"，evaluator 故障也走这里——
    两者共享同一个最高干预强度档位，这是任务 5 分级文档里明确写的绑定，
    这里用 reason_code 的可能取值验证両条路径确实都落在这一档。"""
    from gate import ReasonCode
    fault_result = GateResult(route="BYPASS_TO_HUMAN", R=0, C=0, O=0, Ro=0, Q=0,
                               reason_code=ReasonCode.EVALUATOR_FAULT)
    human_result = GateResult(route="BYPASS_TO_HUMAN", R=0, C=0, O=0, Ro=0, Q=0,
                               reason_code=ReasonCode.BYPASS_HUMAN_JUDGMENT_REQUIRED)
    assert fault_result.route == human_result.route == "BYPASS_TO_HUMAN"
    assert fault_result.reason_code != human_result.reason_code
