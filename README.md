# GateFix Demo —— 悉尼远程退租案例的最小可运行实现

这不是一个抽象 demo。`evidence/sydney_move_evidence.yaml` 里的每一条都是 2026 年 7 月这次悉尼
Rosebery 公寓远程退租真实发生过的事——包括 7 月新增的空运纸箱加固决策和关税不确定性。
代码跑的是真实数据，不是虚构 case。第三方（中介、楼管、货代等）的姓名已替换为身份角色标注，
金额与事实细节保留真实。

## 这个项目证明什么

GateFix 的核心主张是：agent 能不能自主执行一个动作，不该由"有没有一个确认按钮"决定，
而该由这个动作的**可逆性**、**证据是否覆盖四个维度（Relevance/Coverage/Ordering/Robustness）**、
以及**残余的外部风险**共同决定。这份代码把这套判定逻辑做成了三份可替换配置
（`commits.yaml` / `bindings.yaml` / `preconditions/*.py`）+ 一个不随场景变化的引擎
（`gate.py` + `engine.py`）。换一个场景，只需要重写三份配置，引擎本身一行不用改。

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
cd gatefix_demo
pip install pyyaml   # 唯一外部依赖
python engine.py run --case=sydney_move
python engine.py run --case=sydney_move --verbose   # 打印每一轮 AUTO_REPAIR 的细节
```

跑完会在终端看到 8 个 commit 逐条的路由过程，并在 `gate_record.jsonl` 里写一份
结构化的判定记录（一行一个 JSON，含 R/C/O/Ro/Q/route/notes 等字段，可直接喂给
下一步的分析或可视化）。

## 文件结构

```
gatefix_demo/
├── gate.py                          # 引擎核心：GateConfig（阈值/权重）+ GateRecord（判定记录结构）
│                                     # quality_score / route / is_commit / loop_mode /
│                                     # expectation_gate / expected_external_risk 六个公式的代码实现
├── engine.py                        # CLI 运行时：组装上下文→打分→三态路由→(AUTO_REPAIR循环)→写回
├── commits.yaml                     # 8 个 commit 点定义（可逆性/涉及金额/打分函数名/风险配置）
├── bindings.yaml                    # 每个 commit 绑定的真实执行人（以身份角色标注，姓名已脱敏）
├── preconditions/
│   └── sydney_case.py               # 7 个打分函数——本案例特有的 Pᵢ(E,θᵢ) 具体实现
├── evidence/
│   └── sydney_move_evidence.yaml    # 真实案例证据（8 条，含 7 月新增的纸箱/关税事件）
└── gate_record.jsonl                # 运行后生成的判定记录（可重复生成，已提交一份跑过的样例）
```

## 换场景怎么复用

只改三份配置：新场景的 `commits.yaml`、新的 `preconditions/<case>.py` 打分函数、
新的 `evidence/<case>_evidence.yaml`。`gate.py` 和 `engine.py` 不用动——这是
"引擎领域无关，配置领域相关"这条设计原则的直接代码证明，不只是文档里说说。
