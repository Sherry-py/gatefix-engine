# 两臂沙箱实验：实现计划

> **给执行任务的 agent：** 必须配合使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务执行本计划。步骤用复选框（`- [ ]`）语法追踪进度。

**目标：** 按 `docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md` 里的设计，把两臂（governed vs ungoverned）沙箱对比实验建出来——一个真正供 commit 执行的 `SandboxWorld`，一个基于 `requires:` 的硬性 Ordering 门禁，以及两条拿到完全相同对抗性提案、唯一差别是有没有 gate 的执行臂。

**架构：** 一个新的独立包（`world/`）——一个没有判断力、只负责记录的状态机。一个新模块（`agent/two_arm_experiment.py`）装下所有沙箱专属的东西：共用的对抗性 `reason_fn`、新的 Ordering 前置检查、两个执行臂——全部靠组合既有、未改动的代码搭出来（`agent/gated_loop.py` 的 `GatedAgentLoop`/`make_case_gate_fn`/`resolve_precondition`）。`gate.py`、`engine.py`、`agent/gated_loop.py` 本计划一行都不改。

**技术栈：** Python 3.13、pytest、PyYAML（都已经在用，不引入新依赖）。

---

## 开工前：写计划过程中发现的一处 spec 错误

按对抗性顺序逐步核对 `SandboxWorld.execute()` 的实际语义时，发现 spec 里那份对比报告示例不是"举例简化"，是真的算错了。spec 说 ungoverned 臂的终态是 `key_to_building_manager=not_executed, key_to_agent=not_executed`——但对抗性顺序是 `discard_items, physical_handover, bond_claim_confirm, key_to_building_manager, key_to_agent, friend_compensation, air_freight_dispatch`，而 ungoverned 臂撞上违规之后不会停（这正是它的意义所在——没有 gate 能让它停下来）。走到第 4、5 步时，`key_to_building_manager` 和 `key_to_agent` 各自的 `requires`（`discard_items`/`physical_handover`）早就满足了，所以它们会顺利执行成功。它们不会一直"未执行"，只是**执行得太晚了**。

这其实是个比 spec 原文更精确、更有意思的结论：矛盾不是"前置条件从没发生过"，是"`bond_claim_confirm` 发生在它的前置条件之前——即使那些前置条件后来确实也发生了"。`SandboxWorld` 的单调递增 `seq` 计数器能直接证明这一点——`bond_claim_confirm` 的 `seq` 比它 `requires` 的那两步的 `seq` 更小，这比一个"未执行"的布尔值更有力、更可核验。任务 1 会把 `SandboxWorld` 设计成能暴露这一点。任务 5 的测试和任务 7 的文档修正，都是按照**修正后的**真实轨迹写的，不是照抄 spec 的示例文本。

---

## 任务 1：`SandboxWorld`——世界状态记录器

**涉及文件：**
- 新建：`world/__init__.py`
- 新建：`world/sydney_move_world.py`
- 新建：`tests/test_two_arm_experiment.py`

`SandboxWorld` 故意设计成没有判断力：`execute()` 被要求记什么就记什么，哪怕某个 commit 的 `requires` 没满足。它仍然会抛 `OrderingViolation`——但是**记录之后才抛**，不是拒绝记录。这是对 spec 里一处真实张力的解法（见上面的更正说明）：如果世界在违规时拒绝记录，ungoverned 臂就永远到不了"错了但确实记录在案"这个状态，而这正是整个实验存在的意义。执行判断该不该发生的责任在 gate（任务 3），不在 world。world 的职责是如实说出发生了什么，不是对该不该发生有意见。

- [x] **步骤 1：写失败的测试**

新建 `tests/test_two_arm_experiment.py`：

```python
"""Tests for world/sydney_move_world.py and agent/two_arm_experiment.py —
the two-arm (governed vs ungoverned) sandbox comparison. See
docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md for
the full design and the boundary invariant this code must not violate:
gate.py / engine.py / agent/gated_loop.py are never modified — the sequence
check is composed on top of make_case_gate_fn, not merged into it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world.sydney_move_world import OrderingViolation, SandboxWorld


# ---------- SandboxWorld ----------

def test_world_execute_succeeds_when_requires_met():
    world = SandboxWorld({"a": {"id": "a"}, "b": {"id": "b", "requires": ["a"]}})
    world.execute("a")
    record = world.execute("b")
    assert record["executed"] is True
    assert record["ordering_violated"] is False
    assert world.read_state()["b"]["ordering_violated"] is False


def test_world_raises_ordering_violation_on_unmet_requires():
    """The world still records the execution even though it raises — see the
    module docstring in world/sydney_move_world.py for why. This is not a
    bug: the world reflects what really happened, it doesn't gatekeep."""
    world = SandboxWorld({"a": {"id": "a"}, "b": {"id": "b", "requires": ["a"]}})
    try:
        world.execute("b")
        assert False, "expected OrderingViolation"
    except OrderingViolation as e:
        assert e.commit_id == "b"
        assert e.missing == ["a"]
    state = world.read_state()
    assert state["b"]["executed"] is True
    assert state["b"]["ordering_violated"] is True
    assert state["b"]["missing_requires"] == ["a"]
```

- [x] **步骤 2：跑测试，确认它失败**

运行：`cd "/Users/sherry/Library/Mobile Documents/com~apple~CloudDocs/harness_engineer/gatefix-engine" && python3 -m pytest tests/test_two_arm_experiment.py -v`

预期：`ModuleNotFoundError: No module named 'world'`

- [x] **步骤 3：建 `world` 包**

新建 `world/__init__.py`（空文件——跟这个仓库里 `preconditions/__init__.py` 的做法一样）。

- [x] **步骤 4：实现 `SandboxWorld`**

新建 `world/sydney_move_world.py`：

```python
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
```

- [x] **步骤 5：跑测试，确认通过**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：两个测试都 PASS。

- [x] **步骤 6：提交**

```bash
git add world/__init__.py world/sydney_move_world.py tests/test_two_arm_experiment.py
git commit -m "feat: add SandboxWorld — records commit execution, doesn't gatekeep it"
```

---

## 任务 2：给真实案例数据加 `requires:`

**涉及文件：**
- 修改：`commits/sydney_move_commits.yaml`

这是本计划里唯一一处改真实案例数据的地方，只加 spec 里确认过的跨 commit 依赖图，别的不动——`engine.py`/`resolve_precondition` 不读这个字段，所以现有 57 个测试应该完全不受影响。

- [x] **步骤 1：加 `requires:` 字段**

在 `commits/sydney_move_commits.yaml` 里给三个 commit 加 `requires:`。文件目前是这样（相关片段）：

```yaml
  - id: key_to_building_manager
    name_cn: "钥匙移交楼管（Building Manager）"
    irreversibility: "失去直接控制"
    cost_reverse: 50
    value: 200
    cost_fix: 30
    precondition_fn: score_key_to_building_manager
```

改成：

```yaml
  - id: key_to_building_manager
    name_cn: "钥匙移交楼管（Building Manager）"
    irreversibility: "失去直接控制"
    cost_reverse: 50
    value: 200
    cost_fix: 30
    precondition_fn: score_key_to_building_manager
    requires: [discard_items]
```

```yaml
  - id: key_to_agent
    name_cn: "钥匙移交中介（终极 commit）"
    irreversibility: "失去物理访问权，一切遗漏无法补救"
    cost_reverse: inf
    value: 5000
    cost_fix: 0
    precondition_fn: score_key_to_agent
```

改成：

```yaml
  - id: key_to_agent
    name_cn: "钥匙移交中介（终极 commit）"
    irreversibility: "失去物理访问权，一切遗漏无法补救"
    cost_reverse: inf
    value: 5000
    cost_fix: 0
    precondition_fn: score_key_to_agent
    requires: [discard_items, physical_handover]
```

```yaml
  - id: bond_claim_confirm
    name_cn: "Bond claim 确认"
    irreversibility: "资金结算"
    cost_reverse: 500
    value: 3000
    cost_fix: 200
    precondition_fn: score_bond_claim
```

改成：

```yaml
  - id: bond_claim_confirm
    name_cn: "Bond claim 确认"
    irreversibility: "资金结算"
    cost_reverse: 500
    value: 3000
    cost_fix: 200
    precondition_fn: score_bond_claim
    requires: [key_to_building_manager, key_to_agent]
```

同时把文件顶部的字段说明注释块（目前第 4–11 行）补上新字段：

```yaml
# 每个 commit 定义：
#   cost_reverse: 撤销这个动作的成本（inf = 完全不可逆）
#   value:        这个动作本身的价值/涉及金额（用于 IsCommit 的 λ·Value 判定，单位：AUD 等价）
#   cost_fix:     一旦出错，修复成本（用于 on/in-loop 划界）
#   precondition_fn: preconditions/sydney_move.py 里对应的打分函数名
#   bypass_to_human: 证据人情类、机器不可组装，直接旁路给人，不进入 4D-CQ 计算
#   soft_commit: 软 commit（expectation-setting speech act），走 expectation_gate 而不是 quality_score
#   risk_ext: 外部或有闸门（如有），Commit=True 之后仍未清零的残余风险
#   requires: 跨 commit 依赖（这些 commit id 必须先在 SandboxWorld 里执行过）——
#             只被 agent/two_arm_experiment.py::check_sequence_precondition 读取，
#             engine.py / resolve_precondition 完全不读这个字段，不影响现有判定
```

- [x] **步骤 2：确认现有测试套件不受影响**

运行：`python3 -m pytest -v`
预期：全部 59 个测试通过（57 个原有的 + 任务 1 新增的 2 个）——新字段在任务 3 之前一直是"死"的，没人读它。

- [x] **步骤 3：提交**

```bash
git add commits/sydney_move_commits.yaml
git commit -m "feat: add cross-commit requires: dependency graph to sydney_move case"
```

---

## 任务 3：`check_sequence_precondition` + `make_governed_gate_fn`

**涉及文件：**
- 新建：`agent/two_arm_experiment.py`
- 修改：`tests/test_two_arm_experiment.py`

新的 Ordering 检查在这里接进来——用一个包装 `make_case_gate_fn`（来自完全没动过的 `agent/gated_loop.py`）的函数来做，不是改它。本任务不改 `gate.py`、`engine.py`、`agent/gated_loop.py` 任何一行。

- [x] **步骤 1：写失败的测试**

追加到 `tests/test_two_arm_experiment.py`：

```python
from gate import GateConfig
from agent.gated_loop import GateResult
from agent.two_arm_experiment import (
    check_sequence_precondition,
    make_governed_gate_fn,
    _load_commits_by_id,
)


# ---------- check_sequence_precondition ----------

def test_check_sequence_precondition_blocks_on_unmet_requires():
    world = SandboxWorld({"a": {"id": "a"}})
    commit = {"id": "b", "requires": ["a"]}
    result = check_sequence_precondition(world, commit)
    assert result is not None
    assert result.route == "ORDERING"
    assert "a" in result.reason


def test_check_sequence_precondition_allows_when_requires_met():
    world = SandboxWorld({"a": {"id": "a"}})
    world.execute("a")
    commit = {"id": "b", "requires": ["a"]}
    assert check_sequence_precondition(world, commit) is None


def test_check_sequence_precondition_allows_when_no_requires_declared():
    world = SandboxWorld({"a": {"id": "a"}})
    commit = {"id": "a"}
    assert check_sequence_precondition(world, commit) is None


# ---------- make_governed_gate_fn ----------

def test_governed_gate_fn_blocks_before_reaching_base_gate_fn():
    """bond_claim_confirm's real evidence would ESCALATE anyway (refund
    account name mismatch — see tests/test_engine.py) — this test proves the
    ORDERING block happens first and for the right reason, not that it just
    happens to also fail downstream."""
    commits_by_id = _load_commits_by_id("sydney_move")
    world = SandboxWorld(commits_by_id)
    gate_fn = make_governed_gate_fn("sydney_move", world)

    result = gate_fn("sydney_move", {"commit_id": "bond_claim_confirm"})
    assert result.route == "ORDERING"

    world.execute("key_to_building_manager")
    world.execute("key_to_agent")
    result = gate_fn("sydney_move", {"commit_id": "bond_claim_confirm"})
    assert result.route == "ESCALATE"  # falls through to the real, unrelated evidence check
```

- [x] **步骤 2：跑测试，确认失败**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：`ModuleNotFoundError: No module named 'agent.two_arm_experiment'`

- [x] **步骤 3：新建 `agent/two_arm_experiment.py`，先写这两个函数**

```python
"""
agent/two_arm_experiment.py —— governed vs ungoverned，同一个对抗性提案，
只切 gate 开关。见 docs/superpowers/specs/2026-07-31-two-arm-sandbox-
experiment-design.md 完整设计。

不改 gate.py / engine.py / agent/gated_loop.py 一行——check_sequence_
precondition 是新的硬性前置检查，make_governed_gate_fn 用组合的方式把它接
在 make_case_gate_fn（未改动）前面，不是改写判定逻辑。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from engine import load_yaml  # noqa: E402
from agent.gated_loop import (  # noqa: E402
    GateFn,
    GateResult,
    GatedAgentLoop,
    LoopTrace,
    ReasonFn,
    make_case_gate_fn,
)
from world.sydney_move_world import OrderingViolation, SandboxWorld  # noqa: E402


def _load_commits_by_id(case: str) -> dict:
    commits_path = BASE_DIR / "commits" / f"{case}_commits.yaml"
    return {c["id"]: c for c in load_yaml(commits_path)["commits"]}


def check_sequence_precondition(world: SandboxWorld, commit: dict) -> Optional[GateResult]:
    """在真正调用 world.execute() 之前跑：读 world.read_state()，检查这个
    commit 声明的 requires 是否都已经真的执行过。任何一个没满足 → 返回一个
    阻断的 GateResult（route="ORDERING"），不进入 world.execute()，也不
    影响 R/C/O/Ro 打分——这是硬性前置，不是证据质量判定的一部分。全部满足
    （或者没有声明 requires）→ 返回 None，调用方继续走原有的
    resolve_precondition 判定路径。"""
    requires = commit.get("requires")
    if not requires:
        return None
    state = world.read_state()
    missing = [r for r in requires if not state.get(r, {}).get("executed")]
    if not missing:
        return None
    return GateResult(
        route="ORDERING", R=0, C=0, O=0, Ro=0, Q=0,
        verifiable_ext=False,
        reason=f"requires {', '.join(missing)} (not executed yet)",
    )


def make_governed_gate_fn(case: str, world: SandboxWorld) -> GateFn:
    """把 make_case_gate_fn（未改动的既有判定逻辑）包一层：先跑
    check_sequence_precondition，挡住的直接返回；没挡住的原样交给
    make_case_gate_fn。gate.py / engine.py / agent/gated_loop.py 一行都
    没改——这是组合，不是修改。"""
    base_gate_fn = make_case_gate_fn(case)
    commits_by_id = _load_commits_by_id(case)

    def gate_fn(context: str, action: dict) -> GateResult:
        commit = commits_by_id[action["commit_id"]]
        blocked = check_sequence_precondition(world, commit)
        if blocked is not None:
            return blocked
        return base_gate_fn(context, action)

    return gate_fn
```

- [x] **步骤 4：跑测试，确认通过**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：这个文件里全部 6 个测试 PASS。

- [x] **步骤 5：跑全量测试，确认没有回归**

运行：`python3 -m pytest -v`
预期：全部 63 个测试通过（任务 2 之前的 59 个 + 这次新增 4 个）。

- [x] **步骤 6：提交**

```bash
git add agent/two_arm_experiment.py tests/test_two_arm_experiment.py
git commit -m "feat: add check_sequence_precondition and make_governed_gate_fn"
```

---

## 任务 4：共用的对抗性 `reason_fn` + `GovernedArm`

**涉及文件：**
- 修改：`agent/two_arm_experiment.py`
- 修改：`tests/test_two_arm_experiment.py`

- [x] **步骤 1：写失败的测试**

追加到 `tests/test_two_arm_experiment.py`：

```python
from agent.two_arm_experiment import (
    ADVERSARIAL_ORDER,
    GovernedArm,
    make_adversarial_reason_fn,
)


# ---------- make_adversarial_reason_fn ----------

def test_adversarial_reason_fn_proposes_bond_before_keys():
    """The one deliberate 'mistake': bond_claim_confirm at position 3,
    before either key-handover commit. Fixed and named, not random."""
    assert ADVERSARIAL_ORDER == [
        "discard_items", "physical_handover", "bond_claim_confirm",
        "key_to_building_manager", "key_to_agent", "friend_compensation",
        "air_freight_dispatch",
    ]

    reason_fn = make_adversarial_reason_fn()
    state = {"history": []}
    proposed = []
    for _ in range(len(ADVERSARIAL_ORDER) + 1):
        action, _ = reason_fn(state)
        if action.get("type") == "finish":
            break
        proposed.append(action["commit_id"])
        state["history"].append({"action": action, "output": "x"})
    assert proposed == ADVERSARIAL_ORDER


# ---------- GovernedArm（spec 里点名的测试） ----------

def test_governed_arm_blocks_on_ordering_before_execution():
    world = SandboxWorld(_load_commits_by_id("sydney_move"))
    arm = GovernedArm("sydney_move", world, make_adversarial_reason_fn())
    trace = arm.run()

    assert trace.halted_by_gate is True
    assert trace.steps[-1].route == "ORDERING"
    assert trace.steps[-1].tool == "bond_claim_confirm"

    state = world.read_state()
    assert "bond_claim_confirm" not in state  # 从没走到 world.execute
    assert state["discard_items"]["executed"] is True
    assert state["physical_handover"]["executed"] is True
```

- [x] **步骤 2：跑测试，确认失败**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：`ImportError: cannot import name 'ADVERSARIAL_ORDER'`

- [x] **步骤 3：实现 `ADVERSARIAL_ORDER`、`make_adversarial_reason_fn`、`GovernedArm`**

追加到 `agent/two_arm_experiment.py`：

```python
ADVERSARIAL_ORDER = [
    "discard_items",
    "physical_handover",
    "bond_claim_confirm",
    "key_to_building_manager",
    "key_to_agent",
    "friend_compensation",
    "air_freight_dispatch",
]


def make_adversarial_reason_fn() -> ReasonFn:
    """写死的对抗性提案，governed/ungoverned 两臂共用同一个——唯一"错误"是
    把 bond_claim_confirm 提到两次钥匙交接之前。不是随机、不是 LLM，可
    复现、可解释（见 spec 的 Alternatives considered and rejected）。这个
    提案本身从没在现实里发生过——是构造出来测试新机制的输入，不是历史
    重现（见 spec"The gap, concretely"一节）。"""

    def reason_fn(state: dict):
        processed = {h["action"].get("commit_id") for h in state.get("history", [])}
        remaining = [cid for cid in ADVERSARIAL_ORDER if cid not in processed]
        if not remaining:
            return {"type": "finish", "output": "adversarial proposal exhausted"}, 0
        next_id = remaining[0]
        return {"type": "tool", "tool": next_id, "commit_id": next_id}, 0

    return reason_fn


class GovernedArm:
    """route==PASS 才调用 world.execute；ORDERING 硬性阻断在
    make_governed_gate_fn 里生效。世界永远不会在 requires 未满足时被这条臂
    调用——这是靠 GatedAgentLoop 现有的"非 PASS 绝不调 tool_fn"契约保证的，
    这个类自己没有写任何新规则。"""

    def __init__(self, case: str, world: SandboxWorld, reason_fn: ReasonFn,
                 max_steps: int = 8):
        self.world = world
        self.loop = GatedAgentLoop(
            gate_fn=make_governed_gate_fn(case, world),
            tool_fn=self._tool_fn,
            reason_fn=reason_fn,
            max_steps=max_steps,
        )

    def _tool_fn(self, action: dict):
        self.world.execute(action["commit_id"])
        return f"executed {action['commit_id']}", 1

    def run(self) -> LoopTrace:
        return self.loop.run(context="sydney_move", initial_state={})
```

- [x] **步骤 4：跑测试，确认通过**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：这个文件里全部 8 个测试 PASS。

- [x] **步骤 5：提交**

```bash
git add agent/two_arm_experiment.py tests/test_two_arm_experiment.py
git commit -m "feat: add shared adversarial reason_fn and GovernedArm"
```

---

## 任务 5：`UngovernedArm`

**涉及文件：**
- 修改：`agent/two_arm_experiment.py`
- 修改：`tests/test_two_arm_experiment.py`

这里就是本文档开头"修正后的理解"要发挥作用的地方：测试断言的是**实际会发生的事**（Bond 在它的前置条件**之前**被确认，而那两个前置条件后来确实也执行了），不是 spec 示例文本里写的那样（前置条件从没执行过）。

- [x] **步骤 1：写失败的测试**

追加到 `tests/test_two_arm_experiment.py`：

```python
from agent.two_arm_experiment import UngovernedArm


def test_ungoverned_arm_reaches_contradictory_state():
    """bond_claim_confirm's prerequisites (key_to_building_manager,
    key_to_agent) DO eventually execute later in the same run — the
    adversarial order only moves bond_claim_confirm early, it doesn't
    remove the other steps. The contradiction isn't "the prerequisites
    never ran" — it's that bond_claim_confirm ran *before* they did, which
    is exactly what `requires` means and exactly what the seq counter
    proves. (This corrects the spec's example report text, which claimed
    the prerequisites stay unexecuted — traced by hand, that's wrong; see
    the top of this plan.)"""
    world = SandboxWorld(_load_commits_by_id("sydney_move"))
    arm = UngovernedArm(world, make_adversarial_reason_fn())
    trace = arm.run()
    state = world.read_state()

    assert len(trace.steps) == 7
    assert [s.commit_id for s in trace.steps] == ADVERSARIAL_ORDER

    assert state["bond_claim_confirm"]["executed"] is True
    assert state["bond_claim_confirm"]["ordering_violated"] is True
    assert state["bond_claim_confirm"]["missing_requires"] == [
        "key_to_building_manager", "key_to_agent",
    ]

    # 前置条件后来确实也执行了——只是晚了，没用了
    assert state["key_to_building_manager"]["executed"] is True
    assert state["key_to_agent"]["executed"] is True

    # 拿证据说话，不是靠叙事：bond_claim_confirm 的 seq 比它 requires 的
    # 那两步更小——它真的抢在前面发生了
    assert state["bond_claim_confirm"]["seq"] < state["key_to_building_manager"]["seq"]
    assert state["bond_claim_confirm"]["seq"] < state["key_to_agent"]["seq"]
```

- [x] **步骤 2：跑测试，确认失败**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：`ImportError: cannot import name 'UngovernedArm'`

- [x] **步骤 3：实现 `UngovernedStepResult`、`UngovernedTrace`、`UngovernedArm`**

追加到 `agent/two_arm_experiment.py`：

```python
@dataclass
class UngovernedStepResult:
    step: int
    commit_id: str
    ordering_violation: bool
    detail: str = ""


@dataclass
class UngovernedTrace:
    steps: list = field(default_factory=list)


class UngovernedArm:
    """跳过 gate，无条件调用 world.execute——reason_fn 提议什么就执行什么，
    包括违反 requires 的提案。OrderingViolation 被捕获、记录，但不会让循环
    停下：这条臂没有能停下来的机制，这正是要演示的失败模式。"""

    def __init__(self, world: SandboxWorld, reason_fn: ReasonFn, max_steps: int = 8):
        self.world = world
        self.reason_fn = reason_fn
        self.max_steps = max_steps

    def run(self) -> UngovernedTrace:
        trace = UngovernedTrace()
        state: dict = {}
        for _ in range(self.max_steps):
            action, _ = self.reason_fn(state)
            if action.get("type") == "finish":
                break
            commit_id = action["commit_id"]
            try:
                self.world.execute(commit_id)
                trace.steps.append(UngovernedStepResult(
                    step=len(trace.steps) + 1, commit_id=commit_id,
                    ordering_violation=False,
                ))
            except OrderingViolation as e:
                trace.steps.append(UngovernedStepResult(
                    step=len(trace.steps) + 1, commit_id=commit_id,
                    ordering_violation=True, detail=str(e),
                ))
            state.setdefault("history", []).append(
                {"action": action, "output": "executed"}
            )
        return trace
```

- [x] **步骤 4：跑测试，确认通过**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：这个文件里全部 9 个测试 PASS。

- [x] **步骤 5：提交**

```bash
git add agent/two_arm_experiment.py tests/test_two_arm_experiment.py
git commit -m "feat: add UngovernedArm — same proposal, no gate, no halt"
```

---

## 任务 6：跨臂回归测试 + `run_comparison()` + CLI

**涉及文件：**
- 修改：`agent/two_arm_experiment.py`
- 修改：`tests/test_two_arm_experiment.py`

- [x] **步骤 1：写失败的测试**

追加到 `tests/test_two_arm_experiment.py`：

```python
def test_both_arms_receive_the_same_proposed_plan():
    """Regression test for the exact bug this design was corrected for: an
    earlier draft had governed and ungoverned arms proposing different
    sequences, which meant a governed 'block' couldn't be attributed to the
    new ordering mechanism specifically (see spec's Component design
    section). Both arms are handed the same reason_fn; the sequence of
    commit_ids each arm attempted must agree everywhere both arms actually
    got that far — governed's attempted list must be an exact prefix of
    ungoverned's."""
    shared_reason_fn = make_adversarial_reason_fn()

    g_world = SandboxWorld(_load_commits_by_id("sydney_move"))
    g_trace = GovernedArm("sydney_move", g_world, shared_reason_fn).run()
    g_attempted = [s.tool for s in g_trace.steps]

    u_world = SandboxWorld(_load_commits_by_id("sydney_move"))
    u_trace = UngovernedArm(u_world, shared_reason_fn).run()
    u_attempted = [s.commit_id for s in u_trace.steps]

    assert g_attempted == u_attempted[:len(g_attempted)]
    assert g_attempted[-1] == "bond_claim_confirm"
    assert len(u_attempted) == 7
```

- [x] **步骤 2：跑测试**

运行：`python3 -m pytest tests/test_two_arm_experiment.py -v`
预期：PASS（任务 4、5 已经把这个测试要验证的东西都建好了——这一步是回归防护，不是新production代码；照样写、照样跑、确认它是"因为对的原因通过"，不要因为"没什么可实现的"就跳过这一步）。

- [x] **步骤 3：实现 `run_comparison()`、`_print_report()`、CLI**

追加到 `agent/two_arm_experiment.py`：

```python
def run_comparison(case: str = "sydney_move", max_steps: int = 8) -> None:
    reason_fn = make_adversarial_reason_fn()

    governed_world = SandboxWorld(_load_commits_by_id(case))
    g_trace = GovernedArm(case, governed_world, reason_fn, max_steps=max_steps).run()

    ungoverned_world = SandboxWorld(_load_commits_by_id(case))
    u_trace = UngovernedArm(ungoverned_world, reason_fn, max_steps=max_steps).run()

    _print_report(g_trace, governed_world, u_trace, ungoverned_world)


def _print_report(g_trace: LoopTrace, g_world: SandboxWorld,
                   u_trace: UngovernedTrace, u_world: SandboxWorld) -> None:
    print("=" * 78)
    print("agent/two_arm_experiment.py —— governed vs ungoverned, same adversarial proposal")
    print("=" * 78)

    print("\ngoverned:")
    print(f"  attempted: {[s.tool for s in g_trace.steps]}")
    print(f"  halted_by_gate={g_trace.halted_by_gate}  final_output={g_trace.final_output!r}")
    g_state = g_world.read_state()
    print(f"  World end state: {g_state}")
    consistent = not any(rec.get("ordering_violated") for rec in g_state.values())
    print(f"  -> {'consistent' if consistent else 'CONTRADICTORY (unexpected!)'}")

    print("\nungoverned:")
    print(f"  attempted: {[s.commit_id for s in u_trace.steps]}")
    u_state = u_world.read_state()
    violations = [s for s in u_trace.steps if s.ordering_violation]
    print(f"  ordering violations not enforced: {len(violations)}")
    for v in violations:
        rec = u_state[v.commit_id]
        missing = rec.get("missing_requires", [])
        print(f"  {v.commit_id}: executed at seq={rec['seq']}, but requires "
              f"{missing} which hadn't executed yet at that point")
        for m in missing:
            m_seq = u_state.get(m, {}).get("seq")
            when = f"later, at seq={m_seq}" if m_seq else "never"
            print(f"    {m}: executed {when}")
    print(f"  World end state: {u_state}")
    contradictory = len(violations) > 0
    print(f"  -> {'contradictory (ran out of order)' if contradictory else 'consistent (unexpected!)'}")

    print("\nNote: this is a constructed adversarial proposal built to exercise the")
    print("new requires: mechanism, not a record of what happened in the real case —")
    print("see 'The gap, concretely' in the spec.")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description="Run the governed vs ungoverned two-arm sandbox comparison")
    parser.add_argument("--case", default="sydney_move")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    run_comparison(case=args.case, max_steps=args.max_steps)


if __name__ == "__main__":
    main()
```

- [x] **步骤 4：手动跑一遍，看输出**

运行：`python3 agent/two_arm_experiment.py`

预期：报告显示 `governed` 在 `bond_claim_confirm` 处停下、终态 `consistent`；`ungoverned` 走完全部 7 步、1 次违规未被拦、终态 `contradictory`——包括那几行"`key_to_building_manager`/`key_to_agent` 后来在 seq=N 执行了"，验证本文档开头那处修正后的理解。

- [x] **步骤 5：跑全量测试**

运行：`python3 -m pytest -v`
预期：全部 67 个测试通过（57 个原有 + 任务 1 的 2 个 + 任务 3 的 4 个 + 任务 4 的 2 个 + 任务 5 的 1 个 + 本任务的 1 个）。如果数字对不上，当成真 bug 去查，不要硬凑数字。

- [x] **步骤 6：提交**

```bash
git add agent/two_arm_experiment.py tests/test_two_arm_experiment.py
git commit -m "feat: add run_comparison(), report printing, and CLI entrypoint"
```

---

## 任务 7：把文档同步到实际建出来的样子

**涉及文件：**
- 修改：`docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md`
- 修改：`docs/sandbox_verification.svg`
- 修改：`README.md`

spec 和图现在都还写着"设计阶段，尚未实现"。这已经不是事实了，放着不改正好撞上这个仓库自己"如实说明现状"的原则想防的那种陈旧结论。这个任务顺带把 spec 和图里 ungoverned 终态的描述，按任务 5 修正后的真实轨迹改对——不是照抄 spec 原来（错的）示例。

- [x] **步骤 1：更新 spec 的状态行和对比报告示例**

在 `docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md` 里：

把头部状态行：
```
Status: approved for local-only scope (E2B deferred)
```
改成：
```
Status: implemented (local-only scope; E2B still deferred — see Punted section)
```

把 `### Comparison report` 那个 fenced 示例块（`governed:   proposed order: ...` 开头的那段）整段替换成修正版：

```
governed:   proposed order: discard_items, physical_handover, bond_claim_confirm, ...
            halted at step 3 (bond_claim_confirm) — BLOCKED[ORDERING]:
            requires key_to_building_manager, key_to_agent (neither executed yet)
            World end state: only discard_items and physical_handover recorded;
                              bond_claim_confirm never reached world.execute() at all
            -> consistent (the proposal was rejected before it could execute)

ungoverned: same proposed order, no gate — 7/7 steps attempted, step 3
            (bond_claim_confirm) executed despite unmet requires
            World end state: bond_claim_confirm executed at seq=3 with
                              ordering_violated=True; key_to_building_manager and
                              key_to_agent DO execute later (seq=4, seq=5) — the
                              contradiction isn't "the keys never got handed over,"
                              it's that the bond was confirmed before they were,
                              provably so from the seq ordering
            -> contradictory (bond confirmed out of order, proven by seq, not
               just inferred from a missing key) — ground truth read directly
               from World.read_state(), not asserted by the harness

Correction: an earlier draft of this section claimed key_to_building_manager and
key_to_agent stay unexecuted in the ungoverned end state. Tracing the adversarial
order by hand against SandboxWorld's actual semantics shows they do execute —
just after bond_claim_confirm, which is the actual violation. See
tests/test_two_arm_experiment.py::test_ungoverned_arm_reaches_contradictory_state.
```

把结尾的 `## Status / next step` 部分，从：
```
Design approved by the case owner (dependency graph confirmed against the
real facts, delivery scope confirmed as local-only). Next step on approval of
this revision: `writing-plans` to turn this into an implementation plan.
```
改成：
```
Implemented. See `world/sydney_move_world.py`, `agent/two_arm_experiment.py`,
`tests/test_two_arm_experiment.py`. Run `python agent/two_arm_experiment.py`
for the live comparison report. Still punted: E2B backend, LangGraph/MCP
server wiring, third-party validation of the `requires:` graph — see the
Punted list above, unchanged by this implementation.
```

- [x] **步骤 2：修正图里 ungoverned 终态框的文字**

在 `docs/sandbox_verification.svg` 里，找到这几行（ungoverned 终态框里）：

```
<text x="1195" y="728" font-size="9.5" fill="#A03B45" text-anchor="middle" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">world.read_state()：key_to_agent=未执行，</text>
<text x="1195" y="744" font-size="9.5" fill="#A03B45" text-anchor="middle" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">bond_claim_confirm=已执行（已退款）</text>
<text x="1195" y="768" font-size="9.5" font-weight="700" fill="#A03B45" text-anchor="middle" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">同一个提案，没了 gate 就直接执行——矛盾从 World 读出，不是 harness 编的</text>
```

替换成：

```
<text x="1195" y="728" font-size="9.5" fill="#A03B45" text-anchor="middle" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">world.read_state()：bond_claim_confirm 在 seq=3 执行，key_to_building_manager/</text>
<text x="1195" y="744" font-size="9.5" fill="#A03B45" text-anchor="middle" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">key_to_agent 后来确实也执行了（seq=4/5）——但已经晚了</text>
<text x="1195" y="768" font-size="9.5" font-weight="700" fill="#A03B45" text-anchor="middle" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">不是"钥匙没交"，是"Bond 抢跑了"——矛盾从 seq 顺序读出，不是 harness 编的</text>
```

- [x] **步骤 3：更新图里的状态框**

在 `docs/sandbox_verification.svg` 里，"状态与来源"那个框（大约第 204-210 行）目前完整内容是：

```
<rect x="810" y="1076" width="770" height="150" rx="12" fill="#F7F5EE" stroke="#DEDED8" stroke-width="1.6"/>
<text x="830" y="1102" font-size="13" font-weight="700" fill="#3A3A36" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">状态与来源</text>
<text x="830" y="1126" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">状态：设计阶段，spec 已确认，尚未实现——这张图不代表已经跑通的结论。</text>
<text x="830" y="1146" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">修订记录：早期草稿曾让两臂走不同提案，导致 governed 的拦截会被误记成新机制</text>
<text x="830" y="1166" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">的功劳——已改为两臂共用同一提案，见 test_both_arms_receive_the_same_proposed_plan。</text>
<text x="830" y="1188" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">spec：docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md</text>
<text x="830" y="1210" font-size="9.3" fill="#8A8A84" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">写法参考：Rust RFC（alternatives/drawbacks）、Amazon PR/FAQ（最难问题自答）、Nygard ADR（决策跟代价）</text>
```

把五行正文 `<text>`（`<rect>` 和"状态与来源"标题行不动，同样的框大小、同样的 y 坐标——新内容套进同样五个位置）替换成：

```
<text x="830" y="1126" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">状态：已实现——见 world/sydney_move_world.py、agent/two_arm_experiment.py、</text>
<text x="830" y="1146" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">tests/test_two_arm_experiment.py；跑 python agent/two_arm_experiment.py 看实时报告。</text>
<text x="830" y="1166" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">修订记录：两臂曾用不同提案、ungoverned 终态描述也曾算错——都已修正，见测试与 spec。</text>
<text x="830" y="1188" font-size="10" fill="#5C5C56" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">spec：docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md</text>
<text x="830" y="1210" font-size="9.3" fill="#8A8A84" font-family="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif">写法参考：Rust RFC（alternatives/drawbacks）、Amazon PR/FAQ（最难问题自答）、Nygard ADR（决策跟代价）</text>
```

不需要改框的大小——新内容正好套进原来五行用过的 y 坐标。

- [x] **步骤 4：更新 README**

在 `README.md` 里，把：

```
### 沙箱验证机制（设计阶段，尚未实现）
```

改成：

```
### 沙箱验证机制
```

那个小节末尾，把：

```
**如实说明现状**：这套机制目前只有 spec（`docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md`），还没有代码落地，图里标了"本次范围"和"后续工作"的边界，不代表已经跑通的结论。
```

改成：

```
**如实说明现状**：这套机制已经实现并测过（`world/sydney_move_world.py` + `agent/two_arm_experiment.py` + `tests/test_two_arm_experiment.py`），跑 `python agent/two_arm_experiment.py` 能看到实时的两臂对比报告。仍然按 spec 里"Punted"部分列的边界:没接 E2B（进程内实现,还不是真正对 agent 隔离的沙箱)、没接 LangGraph/MCP server（同款插槽，本次没接）、`requires:` 依赖图没有第三方机械校验。
```

再更新文件树部分。目前是这样（相关片段，原文照抄）：

```
├── docs/
│   ├── architecture.svg                      # 机制图①：六节点最小骨架 + 公式绑定
│   ├── decision_chain.svg                    # 机制图②：判定链主干 + 形式化表达 + 完整四态判定式
│   ├── autonomy_layering.svg                 # 机制图③：四态自主度谱系 + 引擎/配置分层带
│   └── sandbox_verification.svg              # 机制图④：沙箱验证（设计阶段，见 docs/superpowers/specs/）
├── docs/superpowers/specs/
│   └── 2026-07-31-two-arm-sandbox-experiment-design.md  # 沙箱验证机制的完整 spec，尚未实现
├── gate.py                                   # 引擎核心：GateConfig（阈值/权重）+ GateRecord（判定记录结构）
│                                              # quality_score / route / is_commit / loop_mode /
│                                              # expectation_gate / expected_external_risk 六个公式的代码实现
├── engine.py                                 # CLI 运行时：按 --case 动态加载下面四处配置→
│                                              # 打分→三态路由→(AUTO_REPAIR循环)→写回
├── agent/
│   ├── gated_loop.py                         # reason→gate→act 循环 + resolve_precondition()
│   │                                          # （三态路由+AUTO_REPAIR+soft_commit 的共享实现，
│   │                                          # mcp_server/server.py、langgraph_loop.py 也调用它）
│   └── langgraph_loop.py                     # 同一套编排换成 LangGraph StateGraph 表达，
│                                              # human_review 节点用 interrupt()/Command(resume=…)
├── mcp_server/
```

把这些行整体替换成：

```
├── docs/
│   ├── architecture.svg                      # 机制图①：六节点最小骨架 + 公式绑定
│   ├── decision_chain.svg                    # 机制图②：判定链主干 + 形式化表达 + 完整四态判定式
│   ├── autonomy_layering.svg                 # 机制图③：四态自主度谱系 + 引擎/配置分层带
│   └── sandbox_verification.svg              # 机制图④：沙箱验证，同一提案两臂对比（已实现，见 world/ + agent/two_arm_experiment.py）
├── docs/superpowers/specs/
│   └── 2026-07-31-two-arm-sandbox-experiment-design.md  # 沙箱验证机制的完整 spec，已实现
├── gate.py                                   # 引擎核心：GateConfig（阈值/权重）+ GateRecord（判定记录结构）
│                                              # quality_score / route / is_commit / loop_mode /
│                                              # expectation_gate / expected_external_risk 六个公式的代码实现
├── engine.py                                 # CLI 运行时：按 --case 动态加载下面四处配置→
│                                              # 打分→三态路由→(AUTO_REPAIR循环)→写回
├── world/
│   ├── __init__.py
│   └── sydney_move_world.py                  # SandboxWorld：记录 commit 真实执行的状态，不做判定
│                                              # （execute() 检查 requires，缺了照样记录、只是附带抛异常）
├── agent/
│   ├── gated_loop.py                         # reason→gate→act 循环 + resolve_precondition()
│   │                                          # （三态路由+AUTO_REPAIR+soft_commit 的共享实现，
│   │                                          # mcp_server/server.py、langgraph_loop.py 也调用它）
│   ├── langgraph_loop.py                     # 同一套编排换成 LangGraph StateGraph 表达，
│   │                                          # human_review 节点用 interrupt()/Command(resume=…)
│   └── two_arm_experiment.py                 # governed/ungoverned 两臂对比：同一个对抗性提案，
│                                              # 只切 gate 开关；组合 make_case_gate_fn，不改它
├── mcp_server/
```

另外，`tests/` 那块目前结尾是：

```
├── tests/
│   ├── test_engine.py                         # gate.py 公式单元测试 + sydney_move 端到端回归测试
│   ├── test_admission_gate.py                 # precondition 打分函数的准入自检（见上文"不是 benchmark，也不是 LLM judge"）
│   ├── test_gated_loop.py                     # agent loop 控制流单测 + 真实 sydney_move 数据的端到端断言
│   ├── test_mcp_server.py                     # MCP tool 的活证据判定测试（真实 AUTO_REPAIR/ESCALATE/soft_commit）
│   └── test_langgraph_loop.py                  # StateGraph 真实数据端到端：interrupt/resume 不会让非 PASS 变 PASS
└── gate_record.jsonl                          # 运行后生成的判定记录（可重复生成，已提交一份跑过的样例）
```

替换成：

```
├── tests/
│   ├── test_engine.py                         # gate.py 公式单元测试 + sydney_move 端到端回归测试
│   ├── test_admission_gate.py                 # precondition 打分函数的准入自检（见上文"不是 benchmark，也不是 LLM judge"）
│   ├── test_gated_loop.py                     # agent loop 控制流单测 + 真实 sydney_move 数据的端到端断言
│   ├── test_mcp_server.py                     # MCP tool 的活证据判定测试（真实 AUTO_REPAIR/ESCALATE/soft_commit）
│   ├── test_langgraph_loop.py                  # StateGraph 真实数据端到端：interrupt/resume 不会让非 PASS 变 PASS
│   └── test_two_arm_experiment.py             # SandboxWorld + 两臂对比：governed 一致 / ungoverned 矛盾，同一提案
└── gate_record.jsonl                          # 运行后生成的判定记录（可重复生成，已提交一份跑过的样例）
```

- [x] **步骤 5：确认图渲染没问题**

在浏览器（或 Claude Browser 工具）里打开 `docs/sandbox_verification.svg`，肉眼确认步骤 2、3 改完之后没有文字重叠。

- [x] **步骤 6：最后再跑一次全量测试**

运行：`python3 -m pytest -v`
预期：全部 67 个测试通过，跟任务 6 完成时一样（这个任务只动文档）。

- [x] **步骤 7：提交**

```bash
git add docs/superpowers/specs/2026-07-31-two-arm-sandbox-experiment-design.md docs/sandbox_verification.svg README.md
git commit -m "docs: mark two-arm sandbox experiment as implemented, fix ungoverned end-state description"
```
