"""
mcp_server/authorize_stdin.py —— 给非 MCP client（比如 DeepSeek Harness 的
Cordis 插件,是 Node.js 进程,不是 MCP client)用的判定入口。

不复制任何判定逻辑:直接调用 mcp_server/server.py 里的 authorize()(和真正
MCP client 调用的是同一个函数,同一份 4D-CQ 判定、同一份审计写入)。唯一的
区别是协议——MCP 走 JSON-RPC over stdio,这里为了不在插件里实现一个完整
MCP client,简化成最小的"stdin 读一个 JSON 请求、stdout 写一个 JSON 响应"
的单次调用协议,专给 subprocess 场景用。

请求(stdin,一次性,不是流式):
    {"case": "...", "precondition_fn": "...", "evidence": {...}}

成功响应(stdout,一行 JSON,成功时 exit code 0):
    authorize() 的完整返回值(gate_state/route/reason_code/human_readable/...)

失败响应(stdout 不写,改写 stderr,exit code 1):
    调用方(TS 插件)看到非 0 exit code 或空 stdout 就必须 fail-closed,
    不能默认放行——和 gate.py 里 safe_score/safe_repair 的 fail-closed
    哲学一致,这里的"故障"包括:JSON 解析失败、case/precondition_fn 不存在、
    authorize() 内部抛异常。

stdout 纪律:和 server.py 一样,stdout 只留给这一行 JSON 响应,任何诊断
信息都写 stderr。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp_server.server import authorize  # noqa: E402


def main() -> int:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
        case = req["case"]
        precondition_fn = req["precondition_fn"]
        evidence = req["evidence"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"authorize_stdin: malformed request: {exc}", file=sys.stderr)
        return 1

    try:
        result = authorize(case, precondition_fn, evidence)
    except Exception as exc:  # noqa: BLE001 — fail-closed 兜底,同 engine.py main()
        print(f"authorize_stdin: authorize() raised: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
