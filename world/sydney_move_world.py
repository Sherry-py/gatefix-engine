"""
world/sydney_move_world.py —— 沙箱世界状态：commit 真正执行的地方。

判定逻辑（gate.py / engine.py / resolve_precondition）完全不在这个文件里，
这个文件也不 import 它们——这是两臂对比实验（agent/two_arm_experiment.py）
唯一新增的"世界"组件，代表 commit 被授权之后真正发生、真正能被独立读回的
状态，不是又一层判定。

关键设计：execute() 检查 requires，缺了就抛 OrderingViolation——但状态仍然
会被记录，抛异常不等于世界拒绝执行。这是故意的：世界只负责记录"真的发生了
什么"，不负责判断"该不该发生"——判断是 gate 的事（见
agent/two_arm_experiment.py::check_sequence_precondition，在真正调用
execute() 之前跑）。GovernedArm 靠这个前置检查，让 execute() 永远不会在
requires 未满足时被调用；UngovernedArm 没有这层检查，直接调用 execute()，
会撞上真实发生的"违规但已执行"状态——这正是两臂对比要演示的东西。如果
execute() 自己拒绝执行、不记录状态，ungoverned 臂就永远没法产生这个矛盾
状态，实验就没有意义了。

本次范围：进程内 dict 实现，不接 E2B（见 spec 的 Punted 部分）。
"""

from __future__ import annotations

from typing import Optional


class OrderingViolation(Exception):
    """execute() 在 requires 未满足时抛出。抛出之前状态已经写入——世界记录
    了"真的发生了什么"，异常只是通知调用方"这次执行违反了声明的依赖"。
    GovernedArm 靠前置检查根本不会走到这里；UngovernedArm 会捕获它、记录、
    继续——世界自己不会因为异常而撤销已经记录的状态。"""

    def __init__(self, commit_id: str, missing: list):
        self.commit_id = commit_id
        self.missing = missing
        super().__init__(
            f"{commit_id!r} requires {missing} to have executed first, "
            f"but they haven't"
        )


class SandboxWorld:
    """进程内的世界状态机。commits_by_id 只用来读每个 commit 的 requires
    字段，不读 precondition_fn/evidence 之类判定相关的字段——这个类不做
    判定，只做执行和记录。"""

    def __init__(self, commits_by_id: dict):
        self._commits_by_id = commits_by_id
        self._state: dict = {}
        self._seq = 0

    def execute(self, commit_id: str, payload: Optional[dict] = None) -> dict:
        requires = self._commits_by_id[commit_id].get("requires") or []
        missing = [r for r in requires if not self._state.get(r, {}).get("executed")]

        self._seq += 1
        record = {
            "executed": True,
            "payload": dict(payload) if payload else {},
            "seq": self._seq,
            "ordering_violated": bool(missing),
        }
        if missing:
            record["missing_requires"] = list(missing)
        self._state[commit_id] = record

        if missing:
            raise OrderingViolation(commit_id, missing)
        return dict(record)

    def read_state(self) -> dict:
        """只读快照——每次调用返回一份拷贝，调用方改不了内部状态。"""
        return {cid: dict(rec) for cid, rec in self._state.items()}
