# 受控协议搜索：让外层开始使用反馈选择编辑

ETALON 现在可以在有限、事先授权的协议编辑集合中，根据已完成审计试验的反馈选择下一个 variant。
这是一个**共享线性模型的协议选择基线**，不是任意 DAG 生成、自动故障修复或已验证的药物发现算法创新。
MolCascade 的单个组件和自定义组合仍是基本单位；这里不把默认多层 funnel 当作唯一合法协议。

本页区分两个完全不同的入口：

- `ProtocolSearch`：在真实 campaign 账本中提出协议、显式授权执行、冻结完整面板反馈。
- `protocol_benchmark()`：完全独立的有限合成奖励表，用于检验选择器的信息隔离和公平消融。

合成奖励不是 `ProtocolRegistry` 可接受的真实证据，不能导入账本后冒充 MolCascade 试运行。
后续可选的 `audit_ei` 将完整面板的预期评分提升与显式成本代价比较，允许在预算用尽前停止；
见[经济停止契约、研究依据与独立消融](protocol-stopping.md)。本页原有三种策略保持原行为。

## 1. 外层搜索的授权与边界

`src/etalon/active/proposer.py` 的 `ProtocolSearch` 与现有 `ProtocolRegistry` 共用账本。
先绑定 seed recipe 与 `DesignSpace`，再调用 `catalogue(base_endpoint_id, space_id)`。
枚举范围是设计空间允许的、最多 `max_edits` 个编辑的**规范顺序组合**，不是任意排列或无界程序搜索。
默认最多检查 32 个组合，可显式设置 `max_variants`，最大 256；超过限额直接拒绝，不自动截断挑选。
编译失败和重复 recipe 分开记为 rejected；编译成功只说明结构/契约通过，不证明运行成功或科学准确。

`authorize()` 要求操作者预先固定：

- 实际允许进入搜索的 variant 及每次 query 的正数报价；未明确报价的组合不会自动加入。
- 4–128 个不重复的审计分子；每个分子恰有一条准入的真实 round objective 结果，不接受 imports 替代；超限拒绝，不抽样。
- 非常量 objective panel、搜索总预算、最大 variant 试验数、选择策略和数值参数。
- 明确授权理由。搜索预算属于 campaign 预算内的子限额，不是额外拨款。

预算同时记录实际 `spent/reserved` 与不可自动回收的试验报价分配 `allocated`。
每次选择意图分配完整面板报价；即使全部 action 被免费阻断，也不能把同一授权自动扩成更多试验。
实际费用超报价仍照实记账。未完成面板、编译失败或成本超支导致无法继续时，反馈保持 incomplete，
可以恢复同一意图或明确关闭搜索，不能只用当前成功子集评分。

所有候选使用同一个冻结的 objective panel。其内容、证据身份、特征词表和 reward contract 在授权时固定。
共享特征为显式截距与编辑 token 指示；编辑部分按单个组合的编辑数缩放。
当前 context 是**协议编辑特征**，没有分子特征、靶点特征或错误码交互项，不能称已学会化学情境相关的编辑选择。
评分与排序算法的版本也被绑定；版本不符时拒绝继续重算或选点，已冻结的旧 reward 仍可读取。

## 2. 提案不会隐式启动工具

生命周期为：

```text
授权有限目录 → plan 只读排序 → propose_next 持久化选择与提案
                                   ↓
                     validate 纯编译 → start_trial 显式授权
                                   ↓
                 run_audit 固定面板真实执行 → score 冻结反馈
                                   ↓
                  下一次 plan / 人工 promote / retire / close
```

`plan(search_id)` 返回 `selected`、排序、`training_hash`、预算、事件截止位置和 `snapshot_hash`。
`propose_next()` 必须携带刚检查过的 `expected_snapshot_hash`；过期快照拒绝，避免在变化的账本上执行旧决定。
它只产生一个受控提案，不编译、不启用 endpoint、不执行、不晋升。
已有未评分试验时不能继续提出下一项，避免跳过失败反馈、只训练有利结果。

下面是**已有 campaign、seed 与设计空间上的接入片段**，不是独立初始化脚本；
`panel_ids` 应来自操作者预先选定、已经支付成本取得的真实 objective round：

```python
from etalon.active.cascade import CascadeExecutor
from etalon.active.proposer import ProtocolSearch
from etalon.active.protocols import ProtocolRegistry, TrialPolicy

search = ProtocolSearch(store)
registry = ProtocolRegistry(store)
catalogue = search.catalogue(base_endpoint_id, space_id)
reviewed_quotes = {variant["id"]: 1.0 for variant in catalogue["variants"]}
# 在真实任务中逐项审查报价；1.0 这里只是声明的演示单位，不是测得耗时。
search_id = search.authorize(
    base_endpoint_id, space_id,
    panel_ids=panel_ids,
    quotes=reviewed_quotes,
    budget=len(panel_ids) * len(reviewed_quotes),
    max_trials=len(reviewed_quotes),
    policy="linear_ucb", beta=1.0, ridge=1.0, noise=0.5, seed=0,
    rationale="reviewed finite variants and a fixed, already acquired objective audit panel",
)
plan = search.plan(search_id)
if plan["selected"] is not None:
    proposal_id = search.propose_next(
        search_id, expected_snapshot_hash=plan["snapshot_hash"],
        rationale="review the selected finite variant under the frozen search contract",
    )
    certificate = registry.validate(proposal_id)
    if not certificate["ok"]:
        raise RuntimeError(certificate)
    record = search.get(search_id)
    chosen_id = record["experiments"][-1]["variant_id"]
    chosen = next(v for v in record["body"]["variants"] if v["id"] == chosen_id)
    registry.start_trial(
        proposal_id, TrialPolicy(**chosen["trial_limits"]),
        rationale="authorize only this frozen panel and its quoted trial cap",
    )
    executor = CascadeExecutor.from_journal(calculation_workspace, store)
    search.run_audit(search_id, executor, max_actions=len(panel_ids))
    reward = search.score(search_id)
    next_plan = search.plan(search_id)
```

示例预算必须仍能装入 campaign 的剩余预算，且目录至少存在一个有效 variant。
审计只允许当前 panel 的分子，每个分子一次尝试；未完成整个 panel 时不能冻结 reward。
执行失败也算一次尝试，不自动重试，不删掉失败行后重新定义分母。
这里“缺失读数”也可能来自合法筛选 gate，不一定是工具故障；惩罚表达其对当前完整面板目标的用途，而非宣告化学错误。
异常、无效身份/单位、缺少准入证据的结果保留成本和状态，不作为成功数值。
真实受控协议仍经过 MolCascade graph/readout 身份检查；合成 benchmark 不调用这些入口。

## 3. response 具体衡量什么

`protocol_score.panel_skill()` 在固定 panel 上做留一仿射预测：用其他有效配对行拟合 variant 到 objective 的映射，
预测当前留出行。每个 fold 的拟合、均值和尺度都排除该行 objective 标签；无法拟合时回退到其他 objective 的均值。
基线也是留一均值预测。令基线平方误差和为 `SSE_base`，则：

```text
raw_skill = 1 - SSE_variant / SSE_base
skill = clip(raw_skill, -1, 1)
effective_cost = max(reported_full_panel_cost, panel_size × per_query_quote)
utility = max(0, skill) / effective_cost
```

缺失/失败的留出行损失不是零，也不从评估中删除，而是其基线平方误差加 `SSE_base / panel_size`。
原始 skill、截断 skill、预测、fold 索引、覆盖率、失败数和成本都保留，不能只保留一个看起来有利的 utility。
账本成本仍按实际报告/明确回退方式记账；quote floor 是评价规则，不代表凭空发生的实际消费。

外层 `rank_variants()` 学习的是已冻结 `skill`，而不是隐藏 objective 值或未执行协议的真值。
当前共享 Bayesian linear working model 使用零均值权重先验、固定 ridge 与噪声标准差；
`linear_ucb` 排序项为 `max(0, mean + beta × std) / full_panel_quote`。
这里的标准差来自工作模型，不是经验证的科学误差条；线性假设不成立时排序可以变差。
不同 variant 共用面板，评分误差也未必独立；当前固定 Gaussian 噪声并未建模这种相关性。
`fixed` 和 `random` 为基线，`beta=0` 为线性 greedy 消融。

**留一验证不等于独立终局验证。** 同一 panel 被反复用于选 variant，因而是外层训练/选择数据。
panel 若由前一轮 AL 自适应挑选，其分布也未必代表全分子池。
当前代码不声称无偏地提高全池精度，更不声称提高最终实验命中率。
真正的效果检验需要另外预登记的随机/分层 held-out 分子或靶点，并给 baseline 和新策略相同信息及总预算。

## 4. 恢复、晋升与停止

选择意图先持久化，提案绑定在 registry 事务中完成；中断后使用 `resume_proposal(search_id)` 继续同一意图，
而不是创建新 endpoint alias 把重复反馈当成独立样本。
意图一旦选中某个 protocol，旧注册入口以及另起 proposal 的别名路径都不能把它转成无搜索约束的试验。
尚未选中的目录项不被这条规则自动禁用；评分时还会核对 trial 元数据确实属于当前 search 与 proposal。
重新打开 `CampaignStore` 与 `ProtocolSearch` 会恢复目录、参数、已完成 reward 和原始证据引用；
执行器通过 `CascadeExecutor.from_journal(...)` 恢复已绑定的 recipe。

`score()` 对已冻结 reward 幂等；后续排序只用已完成反馈。
真正 dispatch 过的 action 不能通过恢复操作免费撤销；pending action 必须先按账本协议核实和处理。
仅当操作者确认旧 worker 已停止且没有 pending action 时，才使用
`store.recover_idle_rounds(reason=...)` 清理空闲未结束 round；它不执行、不退款、不重试。

`promote()` 仍是人工入口，并且搜索协议必须先冻结完整审计 reward。
通过运行阈值只是具备晋升资格，不是强制晋升；即使 skill 很低，也不能自动将其解释成科学进步。
`retire()` 停止新查询但保留历史证据。`close()` 关闭搜索，不删除记录或自动撤销已明确晋升的协议。
搜索中的预算、panel 和数值策略固定；要改变研究设计，需新的明确授权，而不是偷偷换评分口径。
显式晋升后可以脱离审计 panel 执行普通 campaign 查询，仍受总预算与科学准入约束；
旧审计 reward 和截至评分时的搜索成本不随后续 exploitation 数据改变。

完整真实 CPU 示例与只读导出：

```bash
python examples/protocol_search.py --workspace runs/my-protocol-search --output runs/my-protocol-search/report.json
python -m etalon active searches --database runs/my-protocol-search/campaign.sqlite
```

## 5. 可直接运行的纯合成 benchmark

```bash
PYTHONPATH=src python -m etalon.active.protocol_benchmark --seeds 0,1,2,3,4 --budget 6 --max-trials 16
python -m etalon active protocol-benchmark --output runs/my-protocol-benchmark/report.json
python -m pytest tests/test_protocol_benchmark.py -q
```

或直接调用：

```python
from etalon.active.protocol_benchmark import protocol_benchmark

report = protocol_benchmark(seeds=(0, 1, 2, 3, 4), budget=6, max_trials=16)
print(report["summary"])
```

此模块不接触数据库、MolCascade、网络或真实数据。每个场景包含 16 个有限 variant、相同的一单位 query 报价。
默认固定 seed 集，不根据结果删除种子或调优 beta。四个场景都保留：

| 场景 | 隐藏奖励机制 | 检查目的 |
|---|---|---|
| `shared_additive` | 四个公开编辑特征上的线性加性函数 | 共享表示可能有用的条件 |
| `strong_interaction` | 四阶 parity 交互 | 主效应线性模型失配时的局限 |
| `uninformative_features` | 与特征独立、生成一次后冻结的随机表 | 没有可迁移结构时不能假设优势 |
| `negative_utility` | 全部 variant 低于零效用 no-edit 选择 | 无正收益时的行为及无益查询成本 |

五组为 `fixed`、`random`、`linear_ucb`、`linear_ucb_no_transfer`、`linear_greedy`。
one-hot 组固定使用完整 catalog 词表，不因查询或 affordability 变化重新编码；它是特征迁移消融。
合成 catalog 的共享特征与 one-hot 特征均为单位范数，匹配初始单臂先验不确定性，避免把特征长度差异当成迁移收益。
所有组从空历史开始、查询一次得到一次反馈。负收益也付全额成本，没有免费预热或重抽 reward。

控制器只接收公开特征/报价和已获得的 reward；仅查询调度器与最终 evaluator 访问封闭奖励表。
每轮记录选择前 snapshot、已取得标签、训练 hash、选中项和查询成本。
snapshot 保留已观察 variant 的特征，不能因其不再 eligible 而丢失模型训练引用。
合成 `evidence_hash` 只是表格回复的身份，不是实验/计算 provenance，也不经过真实 registry。

主要输出为 `best_revealed_positive_utility`、相对初始可负担 oracle 的 `simple_regret`、
以及正收益候选的 `opportunity_recall`。免费 no-edit 效用为零；没有正收益时 recall 为 `null`。
查询成本用于限制搜索预算，不再从表格效用中重复扣除；所以全负场景中 regret 为零仍可能浪费查询预算，必须同时看 `spent`。
这里不是独立测试集：评价访问同一封闭表，只衡量有限黑盒搜索是否发现好的表格单元。
不能把它解读为真实 panel 泛化、协议正确性、药物活性或对其他 agent 的优势。

本轮真实 CPU pilot、完整测试和 100 组固定配置合成对照见[搜索验证记录](validation-search-2026-09-17.md)。

## 6. 已有方法与新意边界

[LinUCB（WWW 2010）](https://doi.org/10.1145/1772690.1772758) 已提供基于 action/context 特征的线性置信上界选择。
用编辑 token 共享参数不是新 bandit 算法；本实现的 Gaussian working model 与成本归一化也不自动继承原论文全部理论。
[Bandits with Knapsacks（FOCS 2013；作者扩展全文）](https://arxiv.org/html/1305.2545)
已经研究学习收益/资源消耗与预算约束；ETALON 的预算检查或 `UCB/cost` 不等于其最优性结果。

[SafeOpt（ICML 2015）](https://proceedings.mlr.press/v37/sui15.html) 在明确函数正则性等假设下研究安全可达优化。
编译证书、allowlist、限额和人工晋升是运行边界，不是对 docking/assay 质量的统计安全保证。
这里仅在已授权编辑空间内选择和组合，不生成任意代码、不自行放宽证据契约、不证明故障被因果修复。

[Doubly Robust Policy Evaluation and Learning（ICML 2011）](https://icml.cc/2011/papers/554_icmlpaper.pdf)
指出部分反馈与历史动作分布会影响离线策略评价。本实现 `propensity=None`，选择是给定 seed 的确定性过程，
不声称随机日志或无偏 OPE；不能事后把 UCB 分数转换成真实选择概率。
潜在研究贡献仍需检验：在化学状态/证据身份不变的约束下，学习受控协议编辑是否为内层 AL 提供更有效证据，
并在相同总预算和独立终局验证下改善最终推荐。当前只建立可检验、可审计的基线和边界。
