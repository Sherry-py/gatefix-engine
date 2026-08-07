"""Tests for MCP interface discipline (REVISION_BRIEF.md 任务 3).

Four things the brief's acceptance criteria call out:
  - stdout stays protocol-only (no bare print() in the server module)
  - importing the core (gate.py/engine.py) never pulls in an LLM/heavy SDK
  - read and write tools are registered in distinct categories
  - (covered incidentally) authorize()'s audit side effect and
    gate_history_get's read-only query actually work together
"""

import ast
import asyncio
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from mcp_server.server import mcp, MCP_TOOL_CATEGORIES, authorize, gate_history_get


# ---------- stdout discipline ----------

def test_mcp_server_source_has_no_bare_print_calls():
    """stdout 只留给 MCP 协议本身（FastMCP 走 stdio transport）——诊断信息
    应该走 stderr 或日志库，不能往 stdout 写，否则会污染协议帧。用 AST 找
    真实的 print(...) 调用节点，而不是对源码文本做字符串匹配——docstring
    里提到"print("字样（比如模块自己的说明）不该被误判成一次真调用。"""
    source = (BASE_DIR / "mcp_server" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    real_print_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    assert real_print_calls == [], (
        f"found {len(real_print_calls)} print() call(s) in mcp_server/server.py "
        "— stdout must stay protocol-only, log to stderr instead"
    )


# ---------- core dependency purity ----------

def test_importing_gate_and_engine_does_not_pull_in_llm_or_heavy_sdks():
    """核心零重依赖（任务 3）：gate.py/engine.py 是判定核心，不应该因为
    import 就拉起 mcp/openai/anthropic/langchain 这些重依赖或 LLM SDK——
    用一个全新的子进程 import，检查 sys.modules，比读源码找 import 语句
    更可靠（能抓到间接 import）。"""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import gate, engine, audit, sys; "
         "heavy = [m for m in sys.modules if m.split('.')[0] in "
         "('mcp', 'openai', 'anthropic', 'langchain', 'langgraph')]; "
         "print(','.join(heavy))"],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    pulled_in = [m for m in proc.stdout.strip().split(",") if m]
    assert pulled_in == [], f"importing gate/engine/audit pulled in: {pulled_in}"


# ---------- read/write tool categorization ----------

def test_tool_categories_cover_every_registered_tool_exactly():
    registered = {t.name for t in asyncio.run(mcp.list_tools())}
    assert set(MCP_TOOL_CATEGORIES) == registered, (
        "MCP_TOOL_CATEGORIES must be kept in sync with every @mcp.tool()-registered tool"
    )


def test_write_tools_have_side_effects_read_tools_do_not():
    assert MCP_TOOL_CATEGORIES["authorize"] == "write"
    assert MCP_TOOL_CATEGORIES["list_precondition_functions"] == "read"
    assert MCP_TOOL_CATEGORIES["gate_history_get"] == "read"


# ---------- authorize()'s audit side effect + gate_history_get's read path ----------

def test_authorize_writes_an_audit_record_queryable_via_gate_history_get(monkeypatch, tmp_path):
    """authorize() 是写工具——副作用是追加一条审计记录，gate_history_get
    是读工具——能查到刚才那条记录。两个 tool 串起来验证任务 3 的读写分离
    真的名副其实：写的东西读得到，不是各自独立、互不相干的两套代码。"""
    audit_path = tmp_path / "mcp_audit.jsonl"
    import mcp_server.server as server_module

    def redirected_append(record, path=None):
        from audit import append_gate_decision as real_append
        return real_append(record, audit_path)

    def redirected_query(path=audit_path, **filters):
        from audit import query_gate_decisions as real_query
        return real_query(audit_path, **filters)

    monkeypatch.setattr(server_module, "append_gate_decision", redirected_append)
    monkeypatch.setattr(server_module, "query_gate_decisions", redirected_query)

    result = authorize("sydney_move", "score_discard_items", {
        "friend_selected_done": True, "organizer_selected_done": True,
        "id_documents_removed": True, "personal_info_papers_removed": True,
    })
    assert result["audit_write_ok"] is True
    assert result["gate_state"] == "PASS"

    history = gate_history_get(case="sydney_move")
    assert len(history) == 1
    assert history[0]["gate_state"] == "PASS"
    assert history[0]["action_id"] == "discard_items"


def test_gate_history_get_is_read_only_and_empty_for_fresh_log(monkeypatch, tmp_path):
    audit_path = tmp_path / "empty_audit.jsonl"
    import mcp_server.server as server_module

    def redirected_query(path=audit_path, **filters):
        from audit import query_gate_decisions as real_query
        return real_query(audit_path, **filters)

    monkeypatch.setattr(server_module, "query_gate_decisions", redirected_query)
    assert gate_history_get() == []
