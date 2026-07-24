# GateFix Demo —— 悉尼远程退租案例的最小可运行实现

[![CI](https://github.com/Sherry-py/gatefix-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Sherry-py/gatefix-engine/actions/workflows/ci.yml)

**TL;DR (English):** Whether an AI agent may act on its own shouldn't be decided
by "is there a confirm button" — it should be decided by whether the action is
*reversible*, whether the evidence covers four quality dimensions (Relevance /
Coverage / Ordering / Robustness), and what residual external risk survives
even after approval. This repo is a small, runnable engine (`gate.py` +
`engine.py`, ~250 lines, one dependency) that encodes that decision rule and
runs it end-to-end on a real case: an 8-commit, cross-border, remote
lease-termination (Sydney, July 2026) with real dollar amounts and real
third-party executors (names replaced with role labels, facts kept real).
`python engine.py run --case=sydney_move` reproduces all 8 routing decisions
deterministically — no LLM call needed, the decision logic itself is the
point.

**给非技术读者的话：** 这个项目回答一个问题——AI agent 什么时候能自己往下做，
什么时候必须停下来问人？答案不该是"有没有一个确认按钮"，而是这个动作
（1）能不能反悔、（2）证据够不够充分、（3）就算放行了还剩多少甩不掉的外部风险。
案例是一次真实发生的悉尼公寓远程退租：委托人已经回国，钥匙、家具、清洁、
中介结算全部要靠 8 个不可逆决策点和多个人类执行器远程完成。代码跑一遍，
就能看到框架把这 8 个决策点分别判成"直接放行""自动补证据再判""必须叫人终审"
"这事儿机器判不了、直接交给人"四种结果——全部对应真实发生过的事，不是编的。

这不是一个抽象 demo。`evidence/sydney_move_evidence.yaml` 里的每一条都是 2026 年 7 月这次悉尼
Rosebery 公寓远程退租真实发生过的事——包括 7 月新增的空运纸箱加固决策和关税不确定性。
代码跑的是真实数据，不是虚构 case。第三方（中介、楼管、货代等）的姓名已替换为身份角色标注，
金额与事实细节保留真实。

## 机制图

![GateFix core engine — six-node skeleton with formula bindings](docs/architecture.svg)

这张图是引擎的最小骨架：组装上下文→LLM 推理提案→Precondition 判定→三态路由→执行/人工审批→写回，
每个节点标注了对应的公式。下面的 `gate.py` / `engine.py` 就是这张图的直接代码实现——图里的
③Precondition 判定对应 `preconditions/sydney_move.py` 里的打分函数，④三态路由对应 `gate.py` 里的
`GateConfig.route()`，⑤a/⑤b 对应 `engine.py` 里 AUTO_REPAIR 循环和 ESCALATE/BYPASS_TO_HUMAN 分支。

## 这个项目证明什么

GateFix 的核心主张是：agent 能不能自主执行一个动作，不该由"有没有一个确认按钮"决定，
而该由这个动作的**可逆性**、**证据是否覆盖四个维度（Relevance/Coverage/Ordering/Robustness）**、
以及**残余的外部风险**共同决定。这份代码把这套判定逻辑做成了四份按 `--case` 动态加载的
可替换配置（`commits/<case>_commits.yaml` / `bindings/<case>_bindings.yaml` /
`evidence/<case>_evidence.yaml` / `preconditions/<case>.py`）+ 一个不含场景特定逻辑的引擎
（`gate.py` + `engine.py`，靠 `importlib` 按 case 名动态导入打分函数）。

**如实说明现状**：目前只有 `sydney_move`这一个场景跑通过。上面这套"引擎/配置分离"
是架构设计、并有 `engine.py` 里的动态加载机制作为支撑，但"换个场景不用改引擎"这句话
还没有被第二个真实场景验证过——这是设计意图，不是已经过实测的复用性结论。

跑一遍能看到框架里几个关键机制在真实数据上到底长什么样：

- **PASS**：证据四维都够，直接放行执行（如"扔弃物品""实物交割"）。
- **AUTO_REPAIR**：证据有缺口但可外部核查补齐，引擎自动去补一次证据再重新判定
  （"钥匙移交中介"这一条——钥匙数量最初来源是"记忆"，Relevance 打低分，
  触发一轮 AUTO_REPAIR 后来源换成"中介邮件"，重新判定通过）。
- **ESCALATE**：证据缺口不可外部核查，必须人工终审
  （"Bond claim 确认"——RBO 退款账户户名是伴侣，不是委托人本人，
  这个不符只能靠人核实关系，engine.py 里 `verifiable_ext=False`，不会走 AUTO_REPAIR，
  直接升级给人）。
- **BYPASS_TO_HUMAN**：证据是人情类、机器根本组装不了，不进入四维打分
  （"对朋友补偿支付"——关系深浅、开口语气不在任何 API 里）。
- **外部或有闸门 Risk_ext**：`air_freight_dispatch`（空运纸箱交运）这一条即使 route=PASS，
  引擎仍会额外报告 `Risk_ext = p_inspect × Loss(a∣inspected) = 0.15 × 1240 ≈ ¥186`——
  这是本框架这次新增的理论点：**Commit(a,E)=True 不代表总成本已确定**，
  海关抽查这类第三方裁量风险不会因为 gate 放行就清零。

## 怎么跑

```bash
pip install pyyaml   # 唯一外部依赖
python engine.py run --case=sydney_move
python engine.py run --case=sydney_move --verbose   # 打印每一轮 AUTO_REPAIR 的细节
```

跑完会在终端看到 8 个 commit 逐条的路由过程，并在 `gate_record.jsonl` 里写一份
结构化的判定记录（一行一个 JSON，含 R/C/O/Ro/Q/route/notes 等字段，可直接喂给
下一步的分析或可视化）。

```bash
pip install pytest    # 跑测试额外需要这个
pytest -v
```

测试覆盖两层：`gate.py` 六个公式的单元测试（阈值边界、k_dry 耗尽、expectation_gate
真值表），以及一个端到端回归测试——跑一遍 sydney_move case，断言 8 个 commit 的
route 结果跟本 README 里描述的完全一致。改了 `commits/sydney_move_commits.yaml` /
`preconditions/sydney_move.py` 之后这个测试能立刻告诉你有没有破坏真实案例的判定结果。
注意这两层测试覆盖的是不同的东西：单元测试验证的是引擎数学本身（对任何场景都该成立），
回归测试验证的是"sydney_move 这一个场景的判定结果没被意外改坏"——不是"多场景都能跑"，
后者目前没有测试覆盖，因为目前也只有一个场景。

## 文件结构

```
.
├── docs/
│   └── architecture.svg                      # 机制图：六节点最小骨架 + 公式绑定
├── gate.py                                   # 引擎核心：GateConfig（阈值/权重）+ GateRecord（判定记录结构）
│                                              # quality_score / route / is_commit / loop_mode /
│                                              # expectation_gate / expected_external_risk 六个公式的代码实现
├── engine.py                                 # CLI 运行时：按 --case 动态加载下面四处配置→
│                                              # 打分→三态路由→(AUTO_REPAIR循环)→写回
├── commits/
│   └── sydney_move_commits.yaml               # 8 个 commit 点定义（可逆性/涉及金额/打分函数名/风险配置）
├── bindings/
│   └── sydney_move_bindings.yaml               # 每个 commit 绑定的真实执行人（以身份角色标注，姓名已脱敏）
├── preconditions/
│   └── sydney_move.py                         # 7 个打分函数——本案例特有的 Pᵢ(E,θᵢ) 具体实现
├── evidence/
│   └── sydney_move_evidence.yaml              # 真实案例证据（8 条，含 7 月新增的纸箱/关税事件）
├── tests/
│   └── test_engine.py                         # gate.py 公式单元测试 + sydney_move 端到端回归测试
└── gate_record.jsonl                          # 运行后生成的判定记录（可重复生成，已提交一份跑过的样例）
```

## 换场景怎么复用（架构设计，尚未多场景验证）

新增一个场景 `<new_case>` 需要四份新文件：`commits/<new_case>_commits.yaml`、
`bindings/<new_case>_bindings.yaml`、`evidence/<new_case>_evidence.yaml`、
`preconditions/<new_case>.py`（导出 `REGISTRY`，`REPAIR_REGISTRY` 可选），
然后 `python engine.py run --case=<new_case>`。`engine.py` 用 `importlib` 按
case 名动态加载这四处，不需要改 `engine.py` 里的任何一行。

这是"引擎领域无关、配置领域相关"这条设计原则在代码里的落地方式，但目前
只有 `sydney_move` 一个场景跑通过——这个说法描述的是架构能力，不是一个
已经用多个场景验证过的复用性结论。

## Case notes

`commits.yaml` / `bindings.yaml` / `evidence/sydney_move_evidence.yaml` are
transcribed from personal case notes on the Sydney lease termination, written
up through a six-step methodology (jurisdiction grounding → inherited-
liability assessment → commit backward-chaining → executor binding → cheap
reversible probing → evidence-package gating → custody chain → settlement
audit). Those notes are private working material, not a publication.
