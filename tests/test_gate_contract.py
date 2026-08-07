"""Tests for the machine-decidable gate contract (REVISION_BRIEF.md 任务 1).

Acceptance criteria from the brief: every gate_state produces a valid JSON
contract carrying schema_version, and the exit-code mapping has test
coverage. This file also checks the thing the brief explicitly warns
against: a downstream consumer relying on the structured fields (gate_state,
reason_code) rather than parsing human_readable text.
"""

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from gate import GateRecord, EXIT_CODE, EXIT_CODE_INTERNAL_ERROR, SCHEMA_VERSION
from agent.gated_loop import GateResult


# ---------- every gate_state produces a valid, JSON-serializable contract ----------

def test_contract_json_serializable_and_versioned_for_every_gate_state():
    for state in ("PASS", "ESCALATE", "BYPASS_TO_HUMAN"):
        result = GateResult(route=state, R=0.9, C=0.9, O=0.9, Ro=0.9, Q=0.9,
                             reason_code="TEST_CODE", reason="human text")
        contract = result.to_contract()
        # 必须真的能被 json.dumps，不是"看起来像字典"
        encoded = json.dumps(contract)
        decoded = json.loads(encoded)
        assert decoded["schema_version"] == SCHEMA_VERSION
        assert decoded["gate_state"] == state
        assert decoded["reason_code"] == "TEST_CODE"
        assert set(decoded["cq_scores"]) == {"relevance", "coverage", "ordering", "robustness"}


def test_gate_record_to_contract_matches_gate_result_shape():
    """两个不同的判定路径（engine.py 的 GateRecord / agent 的 GateResult）
    产出的契约字段集合必须一致——下游不应该因为走了哪条部署形态就要处理
    两种不同形状的 JSON。"""
    rec = GateRecord(commit_id="x", commit_name="x", R=1, C=1, O=1, Ro=1, Q=1,
                      route="PASS", is_commit=True, loop_mode="ON_THE_LOOP",
                      verifiable_ext=True, dry_rounds=0,
                      reason_code="PASS_ABOVE_THRESHOLD")
    result = GateResult(route="PASS", R=1, C=1, O=1, Ro=1, Q=1,
                         reason_code="PASS_ABOVE_THRESHOLD")
    assert set(rec.to_contract()) == set(result.to_contract())


def test_bypass_to_human_contract_flags_auto_repair_unavailable():
    """BYPASS_TO_HUMAN 走的是人情类分支，从没进过 AUTO_REPAIR 循环——
    auto_repair_available 必须是 False，不能因为字段没设置就默认 True。"""
    result = GateResult(route="BYPASS_TO_HUMAN", R=0, C=0, O=0, Ro=0, Q=0,
                         reason_code="BYPASS_HUMAN_JUDGMENT_REQUIRED")
    assert result.to_contract()["auto_repair_available"] is False


def test_auto_repair_available_true_when_repair_was_actually_attempted():
    result = GateResult(route="PASS", R=1, C=1, O=1, Ro=1, Q=1,
                         repair_attempts=1, reason_code="PASS_ABOVE_THRESHOLD")
    assert result.to_contract()["auto_repair_available"] is True


# ---------- reason_code vocabulary is honest about what route() actually checks ----------

def test_reason_code_vocabulary_has_no_per_dimension_attribution():
    """route() 只对聚合后的 Q 做阈值判断，不单独判断 R/C/O/Ro 哪一维——
    reason_code 词表故意不包含类似 COVERAGE_BELOW_THRESHOLD 这种按维度
    归因的码，避免暗示系统做了它实际没做的判断。"""
    from gate import ReasonCode
    codes = {v for k, v in vars(ReasonCode).items() if not k.startswith("_")}
    for dim in ("RELEVANCE", "COVERAGE", "ORDERING_SCORE", "ROBUSTNESS"):
        assert not any(dim in c for c in codes), f"found dimension-specific code near {dim}"


# ---------- exit code mapping ----------

def test_exit_code_covers_all_three_terminal_routes():
    assert EXIT_CODE == {"PASS": 0, "ESCALATE": 1, "BYPASS_TO_HUMAN": 2}
    assert EXIT_CODE_INTERNAL_ERROR == 3
    # AUTO_REPAIR 从不作为终态暴露，所以故意不在这个映射里
    assert "AUTO_REPAIR" not in EXIT_CODE


def test_engine_cli_exit_code_reflects_worst_route_in_the_batch():
    """sydney_move 案例里既有 PASS 又有 ESCALATE 又有 BYPASS_TO_HUMAN
    （见 tests/test_engine.py 的 EXPECTED_ROUTES）——批量 CLI 的退出码
    必须是这批里最需要人工介入的那个，也就是 BYPASS_TO_HUMAN 对应的 2。"""
    proc = subprocess.run(
        [sys.executable, "engine.py", "run", "--case=sydney_move"],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == EXIT_CODE["BYPASS_TO_HUMAN"] == 2
    # 跑完把 gate_record.jsonl 恢复成仓库提交的版本，不留副作用
    subprocess.run(["git", "checkout", "--", "gate_record.jsonl"], cwd=BASE_DIR)


def test_gated_loop_cli_exit_code_reflects_the_route_that_halted_it():
    """agent/gated_loop.py 按 commits.yaml 顺序推进，第一个把它挡下来的是
    bond_claim_confirm（ESCALATE）——退出码必须是 1，不是 0。"""
    proc = subprocess.run(
        [sys.executable, "agent/gated_loop.py", "--case=sydney_move"],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == EXIT_CODE["ESCALATE"] == 1


def test_worst_case_exit_code_all_pass_is_zero():
    from engine import worst_case_exit_code
    from gate import GateRecord as GR
    records = [GR(commit_id="a", commit_name="a", R=1, C=1, O=1, Ro=1, Q=1,
                   route="PASS", is_commit=True, loop_mode="ON_THE_LOOP",
                   verifiable_ext=True, dry_rounds=0)]
    assert worst_case_exit_code(records) == 0
