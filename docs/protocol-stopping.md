# 协议搜索的经济停止：明确预算代价，而不是耗尽额度

`ProtocolSearch` 新增可选 `audit_ei`：只在**下一次完整面板试验的预期评分提升**超过明确的成本代价时，
才建议提出该试验。默认 `linear_ucb`、`random`、`fixed` 不变，旧 journal 不迁移、不重写。
这是已有 expected-improvement 思路的受控接入，不是新 AL 算法，也不是已验证的 CADD 效果提升。

## 1. 为什么不直接套内层 KG

内层 `knowledge.py` 的 KG 衡量一次观测后最佳 posterior-mean 决策的期望改善。
外层这里采用不同终局：只比较**已经完成完整审计的固定 panel skill**；模型预测再高，
未执行 variant 也不能冒充已取得证据，更不能自动晋升上线。

令 `b = max(0, 已冻结的各 variant skill)`，对尚未执行的 variant `j`：

```text
s_j = sqrt(latent_std_j² + observation_noise²)
R_j = clip(Normal(mean_j, s_j²), -1, 1)
EI_j = E[max(R_j - b, 0)] = integral from b to 1 of Phi((mean_j - t) / s_j) dt
charge_j = opportunity_cost × full_panel_quote_j
net_j = EI_j - charge_j
```

已完成的评分固定不动；相关编辑上的观测通过共享线性模型影响下一个候选的预测。
排序取最大 `net_j`，**不是** `EI/cost`。从剩余预算可完整支付、尚未执行且未被其他路径注册的候选中选择。
当所有可行候选的 `net_j <= 0` 时，返回 `selected=None, stop_reason="economic_stop"`。
恰好为零也停止。无可负担候选、未完成试验、非空闲 campaign、次数耗尽和人工关闭等状态优先保留原有原因。

零分 outside option 表示“不再获取一个新增协议的 panel score”，不是对现有 seed 协议真实价值的估计，
也不是说 seed 比所有变体差。当前目标只是搜集较好的**研究面板评分**；尚未衡量新增协议对未来分子查询的净作用。
`incumbent_ids` 是取得该评分的 variant 身份，不是部署推荐；retired 的历史评分也保留为已取得的研究结果。
晋升资格、有效覆盖率、协议退役等仍由原生命周期判断。

## 2. 需要明确授权的换算率

`opportunity_cost` 必须是有限正数，单位为 `panel_skill / campaign.cost_unit`。
例如，演示成本单位下设 `0.025`、完整面板报价为 `4`，意味着操作者要求下一项试验的预期 skill 提升超过 `0.1`。
这不是默认推荐数值、测得算力价格、未来命中率或模型自动学得的“科学价值”。真实任务需先约定有意义的尺度。

接入片段（已有 campaign、seed、空间和已取得的 objective panel）：

```python
search_id = search.authorize(
    base_endpoint_id, space_id,
    panel_ids=panel_ids, quotes=reviewed_quotes,
    budget=reviewed_search_budget, max_trials=reviewed_trial_cap,
    policy="audit_ei", opportunity_cost=reviewed_skill_per_cost_unit,
    ridge=1.0, noise=0.5,
    rationale="reviewed finite variants, fixed panel and explicit score/cost exchange rate",
)
plan = search.plan(search_id)  # 只读，不启动任何工具
if plan["stop_reason"] == "economic_stop":
    print(plan["economics"], plan["ranking"])
```

经济参数只允许 `audit_ei` 使用；不能向旧策略加一个会被静默忽略的换算率。
`beta` 与 `seed` 仍被验证、记录，但不影响 `audit_ei` 的净值排序；并列时按 variant id 确定顺序。
所有 arm 使用完整面板报价，而非单分子价、成功子集价或剩余未跑行的价。
历史参考面板和已完成试验是沉没成本：仍在真实 campaign 账本计费，但不在每个未来 `net_j` 里重复扣。
`opportunity_charge` 是评分尺度上的比较项，**不是额外的账本收费**。

授权将排序器版本、预测积分版本、换算率、成本单位、outside option 和停止规则冻结到 `economic_contract`。
配置进入 search identity 和 decision snapshot；版本、契约或成本单位漂移时拒绝继续规划，需明确新授权。
旧策略的 body、hash 和快照内容不因增加本功能而改变。

## 3. 生命周期不被模型判断改变

经济停止是一个可重放的规划结果，不会写事件、改变 search.status、退款、退役、晋升或自动调用内层 AL。
`propose_next` 仍在事务中检查最新快照，停止状态下拒绝产生新意图。
若操作者要改变成本尺度或试验设计，需新授权；不能改旧记录来偷偷继续。
独立人工研究权限不被一个数值停止信号自动撤销，已选中 protocol 的别名防护保持不变。

已有 durable intent 照原路径恢复；已开始的 trial 必须完整尝试固定面板后评分，不能因中途模型悲观就只保留部分结果。
真实失败、合法筛选导致的缺失读数、异常和超报价费用仍按已有规则保留。
`close`、`retire`、`promote` 仍是显式调用；冻结奖励后晋升不再改写原审计成本。

运行真实 CPU 示例，另行明确请求关闭搜索后的两轮普通 AL：

```bash
PYTHONPATH=src python examples/protocol_search.py \
  --workspace runs/my-protocol-stopping --output runs/my-protocol-stopping/report.json \
  --policy audit_ei --opportunity-cost 0.025 --campaign-rounds 2
python -m etalon active searches --database runs/my-protocol-stopping/campaign.sqlite
```

示例仅执行 MolCascade/RDKit 的 CPU 属性组件、自定义组合和 gate，不执行 docking/MD/FEP。
`--campaign-rounds` 默认为零；保存的 `balance_after_search` 与 `campaign_continuation` 区分两阶段账务。
继续内层 AL 只证明剩余预算与生命周期可接续，不证明其分配在科学意义上最优。

## 4. 独立停止消融，不改写旧实验

```bash
python -m etalon active protocol-stopping-benchmark \
  --seeds 0,1,2,3,4 --budget 6 --max-trials 16 \
  --opportunity-costs 0.02,0.1,0.3 \
  --output runs/my-protocol-stopping-benchmark/report.json
```

新模块重用原有四种封闭合成表，保留原 `protocol-benchmark` 的五组策略与历史报告不变。
新实验采用固定三档成本尺度、五个种子、四个场景，全部报告，不按结果筛种子或挑成本档：

| 组 | 作用 |
|---|---|
| `linear_ucb` | 原先有硬预算但无经济停止的策略 |
| `audit_ei` | 共享特征、净提升排序与经济停止 |
| `audit_ei_no_stop` | 相同净提升排序，消去经济停止；在相同历史下排名一致 |
| `audit_ei_no_transfer` | 固定 one-hot 表示，匹配初始单臂方差，保留经济停止 |
| `no_search` | 零次新增查询、零分 outside option |

每组面对相同表、空初始反馈、报价、预算上限和试验上限。
一条合成查询代表一个完整 trial 的标量反馈；不模拟真实审计执行，不产生可导入 registry 的证据。
控制器看不到未查询标签，最终 evaluator 才能查看全表。经济停止快照保留而不虚构一次付费查询。

同时报告 `spent`、剩余预算、最佳取得分数、simple regret、正收益召回，以及：

```text
net_utility = best_revealed_positive_utility - opportunity_cost × spent
cost_adjusted_regret = simple_regret + opportunity_cost × spent
```

不同成本尺度的净效用不能不加说明地混为一项平均值。
全负场景的 simple regret 可以为零但仍浪费预算；反之，早停虽然省钱，也可能漏掉本可发现的正收益候选。
旧报告只用成本限额约束搜索，新报告额外定义上述经济评价；没有把同一 cost 在一个公式里重复扣两次。
合成成本不包含 reference acquisition、规划耗时或真实硬件耗时；真实 pilot 的账本单独报告参考取得费用。

## 5. 调研依据与创新边界

[Cost-aware Stopping for Bayesian Optimization（作者全文，2026-05-29 版）](https://arxiv.org/html/2507.12453v5)
已经研究 EI 与成本比较、统一收益/成本尺度及匹配策略的停止理论。
本实现受这一已发表方向启发，但不是论文 PBGI/LogEIPC 的复现，也不能继承其保证。
这里使用有限目录、线性工作模型和 net-EI 排序，科学面板重复使用且噪声假设未校准。

[Selecting Computations: Theory and Applications（UAI 2012，作者全文）](https://aima.eecs.berkeley.edu/~russell/papers/uai12-meta.pdf)
讨论付出计算成本、继续查询与立即决策的取舍。一阶段规则可能过早停止：单次不值得做，不表示多次联合查询也不值得做。
ETALON 当前没有为外层实现多步 lookahead。

[Multi-Information Source Optimization（NeurIPS 2017）](https://proceedings.neurips.cc/paper/2017/file/df1f1d20ee86704251795841e6a9405a-Paper.pdf)
使用信息源查询后的最佳 posterior-mean 改善衡量价值；它与这里只计已完成面板的终局效用不同。
不能把此 `audit_ei` 称为外层 KG，也不能把内层现有 KG 换个名字当原创算法。

数值积分在声明的预测分布下计算 EI，但线性模型以 Gaussian likelihood 拟合已截断的 skill，
不是对边界使用删失似然的完整 Bayesian model。同面板 variant 的误差相关性尚未建模；预测 std 不是校准置信区间。
即使历史奖励都负，也不保证应该停止；不确定性和授权成本尺度仍可能支持继续。达到 skill=1 时 EI 严格为零，
避免未截断 Gaussian 尾部虚构不可能的更高分数。

目前可检验的研究方向仍是：**在证据与化学状态契约不变的条件下，联合学习查询哪个分子、改哪个协议、何时值得修改协议**。
本轮补的是最后一项的有界基线；真正的创新主张还需要把外层价值与内层终局推荐收益连接起来，
并在独立靶点/分子划分、相同总预算和真实 CADD 读数下验证。复用面板的高分不等于药物发现优势。

本轮的完整回归、真实 CPU 接续示例、300 组合成对照及过早停止负结果，见[验证记录](validation-stopping-2026-09-17.md)。
