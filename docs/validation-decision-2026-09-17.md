# 决策控制器迭代验证（2026-09-17）

这是上一轮 [398 项测试与旧策略负结果](validation-2026-09-17.md) 之后的独立记录。
旧文件、旧 benchmark 和运行目录全部保留，不用这轮结果覆盖历史证据。
原始文献与创新边界见 [研究定位](research-innovation-2026-09-17.md)，接口见 [使用指南](active-learning.md)。

## 实现与修正

1. 有限候选 Gaussian KG 的仿射上包络解析积分，以及原始单位的 posterior cross-covariance。
2. `mf_kg` 基线与 `decision_aware` 控制器：条件准入估计、可确认决策池、有限校准配额、确认报价 guard。
3. 一次真实 action 一次重规划；`provisional` / `attainable` / `evidence_backed` 分层推荐。
4. 所有组反馈频率一致的八组消融；区分推荐 regret 与历史最好标签 regret，并记录规划耗时。
5. 独立审查发现并修正 KG 绝对截断阈值破坏单位不变性的问题；
   GP 配对相关性退化判断也改为标准化量纲，模型标识升级为 `paired-task-gp/2`。
6. 预算 reserve 事务重新核算目标端点报价；新增协议不重置校准额度；
   新 benchmark 绑定实现源码 hash 与 NumPy/SciPy 版本，拒绝修改实现后静默续跑。

这些是工程和决策机制迭代，不是“已证明新算法优于 published 方法”的声明。

## 测试与环境

沿用独立环境 `/tmp/etalon-test-I5B7zM`，未替换主 Anaconda 软件包。
Python 3.11.5、NumPy 1.26.4、SciPy 1.11.1、Pydantic 2.13.5、PyArrow 17.0.0、RDKit 2025.3.2。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/etalon-test-I5B7zM/bin/python -m pytest -q -p no:cacheprovider
python -m ruff check src tests tools examples
git diff --check
python tools/verify_assets.py --deep
```

- 中间全量回归：452 passed，164.24 秒。
- 最终全量回归：**459 passed，173.64 秒，0 skipped**；相对上轮新增 61 项测试。
- 唯一 warning 为既有 RDKit converter 重复注册提示。
- Ruff、diff whitespace check 通过；MolCascade、PRISM 与 reference 资产逐项深度校验通过，未修改 vendored 源码。
- 测试包含本机可用 GPU 的数值核回归；本轮没有启动 docking/MD/FEP 生产计算、付费模型 API 或外部 GPU 作业。

新增覆盖包含：公共平移零 KG、解析值/数值积分/Monte Carlo 交叉核验、负相关与单位变换、
同一 observation 对全池的相关更新、分块一致性、拒绝标签数值隔离、oracle 不可见性、
新协议共享校准额度、确认预算在事务中保护、实际失败付费、只读推荐、不可确认候选排除、
不同推荐含义、相同反馈频率、续跑幂等及实现版本变化拒绝。

## 真实 MolCascade 组件复跑

在未 editable-install 的本轮验证环境中显式使用 `PYTHONPATH=src`：

```bash
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /tmp/etalon-test-I5B7zM/bin/python examples/component_learning.py \
  --workspace runs/components-decision-v2 --policy decision_aware
```

8 个真实 SMILES；第一轮性质单组件，随后注册“SA score → properties”的新协议。
最终执行 9 轮/9 条结果，全部准入：8 条 MW objective、1 条 logP 协议校准。
每轮训练样本数依次为 0–8。24 个示例报价单位中实际按报价记账 10，剩余 14，未决预留为 0。
全部 objective 候选已测量且不可重复后，控制器以 `no_eligible_actions` 停止，没有为了花完预算继续购买 proxy。

此例中校准额度为 3.6，proxy 每次报价 2，因此只支持一次配对校准，不足以学习该协议的相关性。
这验证额度确实生效，不表示找到了最优校准投入。
三个推荐均为 ethylamine，真实组件 MW 读数约 45.085 Da。
**分子量最小不是药物设计成功，logP 不是亲和力，示例报价也不是实测算力成本。**

证据保存在：

- `runs/components-decision-v2/campaign.sqlite`
- `runs/components-decision-v2/evidence.json`
- `runs/components-decision-v2/recommendation.json`
- 同目录 `calculations/`：逐 action 输入、编译计划、原始 artifact。

## 八组消融的预设控制

复用未改变的 synthetic-v1，固定 seeds `0,1,2,3,4`、64 个候选、预算上限 120、收费 warm start 32。
所有组都使用 `batch_size=1`、同一准入规则、同一特征和相同隐藏 oracle。
轮数上限 200；预算相同指上限相同，不要求策略为了花满余额执行无价值动作。
新表与历史 batch-size=4 的旧表不能作为严格的前后对照。

```bash
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /tmp/etalon-test-I5B7zM/bin/python -m etalon active decision-benchmark \
  --workspace runs/decision-validation-v2 --seeds 0,1,2,3,4 \
  --output runs/decision-validation-v2/report.json
```

`runs/decision-validation-v1/` 是补充单位/版本绑定修正前的首轮消融，保留供追溯；
最终版本单独使用 v2 目录，不沿用旧 journal。
报告含逐 seed 推荐、配对差值、真实观测账本路径、剩余预算、停止原因及规划/运行时长；
oracle cost 不包括代理拟合/调度时间。物理真实数据尚未接入，任何表中 regret 都不是 CADD 效能指标。

## 最终消融结果

40 个 campaign 全部结束，没有一个因 200 轮上限被截断。
以下为 5 seeds 的均值；括号为 evidence-backed regret 的 seed 间样本标准差。regret 越低越好。

| 组 | 证据支持推荐 regret | 无约束模型推荐 regret | 无效结果花费 | 总花费 |
|---|---:|---:|---:|---:|
| random | 0.73606 (0.62580) | 0.87136 | 0.0 | 120.0 |
| UCB | 0.32300 (0.41927) | 0.32300 | 0.0 | 120.0 |
| 旧 cost_aware | 0.36125 (0.39955) | 0.42698 | 7.4 | 120.0 |
| mf_kg | 0.32300 (0.41927) | 0.32300 | 1.6 | 119.8 |
| 去准入折扣 | 0.32300 (0.41927) | 0.32300 | 1.4 | 118.2 |
| 去确认 guard | 0.32300 (0.41927) | 0.32300 | 1.6 | 119.0 |
| 全局准入估计 | 0.32300 (0.41927) | 0.32300 | 1.4 | 118.2 |
| decision_aware | 0.32300 (0.41927) | 0.32300 | 1.4 | 118.2 |

本次期末 attainable 和 evidence-backed 推荐 regret 相同；它们与无约束 provisional 的区别仍须保留。
模型推荐并不一定来自当前最小观测标签，例如 random 的历史最好标签 regret 均值是 0.69643，
不能拿它替代表中的 0.73606。相应四种指标都保存在 JSON 中。

完整策略与 MF-KG 的证据支持推荐 regret，**逐 seed 的差值均为 0**。
去掉准入折扣、使用全局准入、去掉确认 guard，也没有改变本例最终 regret。
因此这轮结果**没有证明条件可靠性或确认机制带来独立性能优势**。
比旧 heuristic 更少的无效花费，不能自动归因于局部可靠性模型；更简单的 UCB 在此例没有无效花费。

每 campaign 已执行轮次的平均累计规划耗时约：UCB 0.736 秒、MF-KG 1.224 秒、
完整策略 1.040 秒、旧 heuristic 2.750 秒。对应本次运行 wall time 约为 3.354、5.342、4.554、12.038 秒。
后者包含前者以及 SQLite/执行开销；不是两项可相加的成本，也不是受控硬件吞吐测试。
旧 heuristic 有更多低成本查询/轮次，不能由累计时间推断其单次 acquisition 更慢。
只读终态规划及推荐拟合的额外时间也单独记录在报告中。

合理结论：数值与工程语义得到加强，已建立可复现的公平对照；研究效果仍未优于强简单基线。
下一步应检验真实协议与证据失效机制，而不是针对 synthetic-v1 调整参数直到胜出。
自动协议图搜索、修复学习、共享 artifact、跨靶点迁移和真实亲和力验证仍未实现或验证。
