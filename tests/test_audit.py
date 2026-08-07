"""Tests for audit.py — the append-only audit trail (REVISION_BRIEF.md 任务 4).

Three things the brief's acceptance criteria call out specifically:
  - writing several decisions then querying them back works
  - the log is append-only (never rewritten in place)
  - a simulated audit-write failure is reported separately from (and
    without rolling back) the gate decision that already happened

All tests use a temp file path, never the repo's real gate_audit_log.jsonl.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import (
    append_gate_decision, build_audit_record, query_gate_decisions,
    AuditWriteResult,
)
import engine


def _record(action_id="commit_a", gate_state="PASS", reason_code="PASS_ABOVE_THRESHOLD"):
    return build_audit_record(
        action_id=action_id, gate_state=gate_state, reason_code=reason_code,
        cq_scores={"relevance": 1.0, "coverage": 1.0, "ordering": 1.0, "robustness": 1.0},
        schema_version="1", thresholds={"tau_pass": 0.85, "tau_repair": 0.5},
        case="sydney_move",
    )


# ---------- write + query round-trip ----------

def test_query_returns_empty_list_for_nonexistent_log(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    assert query_gate_decisions(path) == []


def test_write_several_then_query_back(tmp_path):
    path = tmp_path / "audit.jsonl"
    for i in range(3):
        result = append_gate_decision(_record(action_id=f"commit_{i}"), path)
        assert result.ok is True

    all_records = query_gate_decisions(path)
    assert len(all_records) == 3
    assert {r["action_id"] for r in all_records} == {"commit_0", "commit_1", "commit_2"}


def test_query_filters_by_action_id_and_gate_state(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_gate_decision(_record(action_id="a", gate_state="PASS"), path)
    append_gate_decision(_record(action_id="b", gate_state="ESCALATE"), path)
    append_gate_decision(_record(action_id="a", gate_state="ESCALATE"), path)

    assert len(query_gate_decisions(path, action_id="a")) == 2
    assert len(query_gate_decisions(path, gate_state="ESCALATE")) == 2
    assert len(query_gate_decisions(path, action_id="a", gate_state="PASS")) == 1


def test_query_filters_by_time_range(tmp_path):
    path = tmp_path / "audit.jsonl"
    early = build_audit_record(action_id="early", gate_state="PASS",
                                reason_code="X", cq_scores={}, schema_version="1")
    early["timestamp"] = "2020-01-01T00:00:00+00:00"
    late = build_audit_record(action_id="late", gate_state="PASS",
                               reason_code="X", cq_scores={}, schema_version="1")
    late["timestamp"] = "2030-01-01T00:00:00+00:00"
    append_gate_decision(early, path)
    append_gate_decision(late, path)

    assert [r["action_id"] for r in query_gate_decisions(path, since="2025-01-01")] == ["late"]
    assert [r["action_id"] for r in query_gate_decisions(path, until="2025-01-01")] == ["early"]


# ---------- append-only: never rewritten in place ----------

def test_log_is_append_only_not_overwritten(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_gate_decision(_record(action_id="first"), path)
    first_line = path.read_text(encoding="utf-8")

    append_gate_decision(_record(action_id="second"), path)
    contents = path.read_text(encoding="utf-8")

    # 第一条记录必须原样还在——不是被截断/改写掉了
    assert contents.startswith(first_line)
    lines = [json.loads(line) for line in contents.splitlines()]
    assert [r["action_id"] for r in lines] == ["first", "second"]


def test_engine_run_case_writes_one_audit_entry_per_commit(tmp_path):
    """端到端：真实 sydney_move case 有 7 个 commit，跑一次 run_case 之后
    审计日志里必须能查到 7 条，且 gate_state 分布和 tests/test_engine.py
    的 EXPECTED_ROUTES 一致（BYPASS_TO_HUMAN/ESCALATE/PASS 都要在里面，
    不是只测 PASS 这一种最简单的情况）。"""
    audit_path = tmp_path / "run_audit.jsonl"
    engine.run_case("sydney_move", audit_log_path=audit_path)

    records = query_gate_decisions(audit_path)
    assert len(records) == 7
    states = {r["gate_state"] for r in records}
    assert states == {"PASS", "ESCALATE", "BYPASS_TO_HUMAN"}
    assert all(r["schema_version"] == "1" for r in records)
    assert all(r["reason_code"] for r in records)

    # gate_record.jsonl (覆盖写的快照) 是这个测试的副作用，不属于这个测试
    # 的断言范围——恢复成仓库提交的版本，不留痕迹。
    import subprocess
    subprocess.run(["git", "checkout", "--", "gate_record.jsonl"],
                    cwd=Path(__file__).resolve().parent.parent)


# ---------- honest failure semantics: decision succeeds, audit write fails ----------

def test_append_failure_is_reported_not_raised(tmp_path):
    """写入目标是一个目录而不是文件——open(..., 'a') 在这种路径上必然
    抛 OSError（是 IsADirectoryError，OSError 的子类）。append_gate_decision
    必须捕获它、返回 ok=False，不能让异常冒泡出去。"""
    not_a_file = tmp_path / "this_is_a_directory"
    not_a_file.mkdir()

    result = append_gate_decision(_record(), not_a_file)
    assert isinstance(result, AuditWriteResult)
    assert result.ok is False
    assert result.error is not None


def test_run_case_reports_gate_decisions_and_audit_failures_separately(tmp_path, monkeypatch):
    """诚实的失败语义（抄 TFD）：审计写入失败时，run_case 不能因此丢掉或
    修改已经算出来的判定结果——records 仍然是完整的 7 条真实判定，audit
    write 的失败只出现在旁路统计里，不影响判定本身。"""
    import audit as audit_module

    def always_fail(record, path=None):
        return AuditWriteResult(ok=False, error="simulated disk full")

    monkeypatch.setattr(audit_module, "append_gate_decision", always_fail)
    monkeypatch.setattr(engine, "append_gate_decision", always_fail)

    records = engine.run_case("sydney_move", audit_log_path=tmp_path / "unused.jsonl")

    # 判定本身完全没受影响：还是真实的 7 条，路由分布和平时一样
    assert len(records) == 7
    assert {r.route for r in records} == {"PASS", "ESCALATE", "BYPASS_TO_HUMAN"}

    import subprocess
    subprocess.run(["git", "checkout", "--", "gate_record.jsonl"],
                    cwd=Path(__file__).resolve().parent.parent)
