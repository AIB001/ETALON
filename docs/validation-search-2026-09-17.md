# 受控协议搜索：2026-09-17 验证记录

本轮在[协议生命周期](validation-protocol-2026-09-17.md)之上增加学习型有限组合选择，
实现说明见[受控协议搜索](protocol-search.md)。这是工程和机制验证，不是亲和力或药物发现效果论文。

## 1. 验证范围与环境

新增 `proposer.py`、`protocol_score.py`、`protocol_benchmark.py`，并接入原有 registry、
事务预算、内层 planner 和只读 CLI。MolCascade/PRISM 资产没有修改。

环境沿用 `/tmp/etalon-test-I5B7zM/bin/python`：Python 3.11.5、NumPy 1.26.4、SciPy 1.11.1、
RDKit 2025.03.2、Pydantic 2.13.5、PyArrow 17.0.0。
完整测试环境有 RTX 4090、PyTorch 2.7.0+cu126；已有 GPU 数值核回归不等于真实 MD/FEP campaign。
真实新示例只调用 CPU RDKit 组件，不调用 docking、MD、FEP、付费 API 或湿实验。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
    /tmp/etalon-test-I5B7zM/bin/python -m pytest -q -p no:cacheprovider
python -m ruff check src tests tools examples
PYTHONPATH=src /tmp/etalon-test-I5B7zM/bin/python tools/verify_assets.py --deep
git diff --check
```

最终全量回归：**770 passed、0 skipped、1 warning，398.65 秒**。
唯一 warning 为既有 RDKit converter 重复注册。新增专项分为评分/排序 65 项、搜索集成 54 项、
合成对照 28 项、CLI 10 项，共 157 项；此前 613 项回归保留。
Ruff、diff whitespace、vendored assets deep verify 已通过。

关键机制测试覆盖：

- 留出标签不参与该 fold 的预测、均值、标准化；单位和方向变换不改变评分。
- 缺失读数不从分母消失；全失败反馈为 −1；零成本不产生无穷效用。
- 已观察反馈改变共享编辑候选的排序；未查询真值不影响选择；重复 evidence 不增加训练样本。
- 目录只读、未授权报价/组合拒绝、面板 4–128 上限、固定版本、snapshot 过期拒绝。
- 选择意图、事件和 proposal 绑定的事务恢复；中断续接不产生新 alias。
- 已选择但未启动的协议也不能经 legacy 注册、另建 proposal 或提前存在的普通 proposal 绕过搜索控制；
  尚未被选择的目录项不因此被隐式禁用。评分必须有匹配 search/proposal 的 controlled-trial 元数据。
- 总报价分配、实际费用、trial 次数、直接 reserve 面板限制、planner 过滤和异常费用均有回归。
- 评分前不能晋升；评分后显式晋升允许普通查询，但不能重写旧 reward 或搜索成本。
- CLI 不创建不存在的数据库、不覆盖输出、不执行科学组件；合成 benchmark 不连接 SQLite。

## 2. 真实 CPU 四轮 pilot

已执行的新目录是 `runs/protocol-search-v1/`；不要复跑覆盖。命令结构如下，当前源码需设置 `PYTHONPATH=src`：

```bash
python examples/protocol_search.py --workspace runs/protocol-search-v1 \
    --output runs/protocol-search-v1/report.json
python -m etalon active searches --database runs/protocol-search-v1/campaign.sqlite \
    --output runs/protocol-search-v1/searches.json
python -m etalon active export --database runs/protocol-search-v1/campaign.sqlite \
    --output runs/protocol-search-v1/evidence.json
```

面板在取得结果前固定为 ethanol、ethylamine、benzene、acetic acid 四个分子。
三项有限编辑为增加 SA、增加 `mw_min=100` 的 property gate、将 properties batch size 从 128 改为 64；
规范组合共 7 个，不修改最终 `properties.mw` readout。gate 会合法筛掉这些小分子，
故这是有意设置的运行覆盖差异，不是模拟药物效力或注入伪造标签。

| 试验 | 选中的编辑 | 有效读数 / 4 | 冻结 skill | 实际记账 |
|---|---|---:|---:|---:|
| 1 | gate + SA | 0 | −1 | 4 |
| 2 | batch size | 4 | 0.9917355372 | 4 |
| 3 | SA + batch size | 4 | 0.9917355372 | 4 |
| 4 | gate + batch size | 0 | −1 | 4 |

4 次 reference + 16 次 variant 查询，共 **20 actions、12 admitted**。
campaign 报价预算 64、支出 20、剩余 44、预留 0；搜索分配预算 16、支出 16，没有免费失败或隐性重试。
每轮重开账本，排序器训练大小依次为 0、1、2、3，最终为 4；各 trial 显式退役，搜索显式关闭。
没有自动晋升任何协议，也没有删除 8 次缺失 readout 的成本与失败记录。

分子量可由同一 RDKit 组件精确重读，0.9917 是相对于留一均值的、带 ridge 的内部面板评分，
**不是 99% 药物预测准确率**。第四轮仍探索了另一个带 gate 的组合，不能描述为已经学会避免所有失败。

产物：[完整报告](../runs/protocol-search-v1/report.json)、[只读搜索导出](../runs/protocol-search-v1/searches.json)、
[action/观测/事件](../runs/protocol-search-v1/evidence.json)。原始 SQLite 与各 action artifact 同目录保留，按规则不纳入 Git。
两次只读导出前后 DB SHA256 完全相同：
`9ffcd2d1aa79eae0185af6a501f770e43fd57f551f81420c2e8d6f42642cfc78`。

另以新目录 `runs/protocol-search-v2/` 复跑，得到完全相同的数值摘要（20 actions、20 quote、
4 条 skill 和失败计数），未覆盖 v1。第二次的
[报告](../runs/protocol-search-v2/report.json)、[搜索导出](../runs/protocol-search-v2/searches.json)、
[证据导出](../runs/protocol-search-v2/evidence.json)均保留。
最后增加的候选阶段跨 API 拦截另由最终集成回归覆盖；两次示例均使用正常的受控试验路径。
v2 的只读导出前后 DB SHA256 也未改变：
`e317cce50fdb13881774c7570539546f0446422ad5cc5d77ea7746b8b255ef5b`。

## 3. 五组策略、四场景、五 seed 的等预算合成对照

已执行：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=src \
    python -m etalon active protocol-benchmark --output runs/protocol-search-benchmark-v1/report.json
```

[报告](../runs/protocol-search-benchmark-v1/report.json)含全部 **100 runs**，未筛选 seed、未按结果调参。
每个 run 16 个候选、空初始历史、查询预算 6，每次查询成本 1；所有组使用相同 reward 表和逐次反馈。
共享特征和 one-hot 消融的初始单臂方差匹配。reward 表仅供查询后反馈和最终评价，控制器不能读取未来结果。

平均 simple regret 越小越好，免费 no-edit 的基准效用是 0：

| 固定场景 | fixed | random | linear UCB | 无迁移 UCB | linear greedy |
|---|---:|---:|---:|---:|---:|
| 加性共享特征 | 0.120000 | 0.180000 | 0.000000 | 0.120000 | 0.000000 |
| 强四阶交互 | 0 | 0 | 0 | 0 | 0 |
| 无信息特征 | 0.167256 | 0.175723 | 0.091517 | 0.167256 | 0.147952 |
| 全负收益 | 0 | 0 | 0 | 0 | 0 |

这些结果不能概括为普遍优势：

- 加性场景本来就符合线性模型；greedy 与 UCB 的最佳发现相同，不能证明探索项有独立增益。
- 强交互场景各组都发现至少一个最优值，但正收益候选 recall：UCB 为 0.35，fixed 为 0.45，greedy 为 0.25。
  模型假设不成立时没有稳定优势，单看 simple regret 会隐藏差异。
- 无信息特征只有 5 个 seed，数值上的优劣不能证明不存在结构时仍可迁移。
- 全负场景 regret 为 0，但所有组仍花完 6 个单位。当前没有经过验证的经济停止策略。

这个 benchmark 是有限黑盒表的机制检查，既不是独立面板泛化，也不是 published CADD agent 对照。
规划和编译耗时没有换算进这些合成 query 单位；真实示例同样使用报价而非实测 CPU/GPU 货币成本。

## 4. 代码与主张边界

本地工作树未提交，包含此前用户/任务修改。下列 SHA256 记录最终交付文件，不代替正式 release：

```text
proposer.py           fe6c2f3d01fc7234fa4ab773e3041b2cab3854760b7006a3468cd5d6124d9e2f
protocol_score.py     2a56a9ffd7960a1d3458a70b9070efa7d7c36cb2ab409d68b5a74ab0253a7c1d
protocol_benchmark.py cf49bca8f6572886a3257b6d77978f83c5510ecadb08e68d3e2ca0915bf07e54
protocol_search.py    14f031ffd835533d39bb7a2e22ec92957623d4a961429b921725e91b0d173f45
```

已完成的是：**受证据契约和预算约束、会使用既有反馈的有限协议选择闭环**。
算法本身是已有共享线性/bandit 思路的基线；未声称新 LinUCB、SafeOpt 保证、因果修复或无偏 OPE。
尚需真实多靶点、训练/选择与终局测试分离、相同信息/工具/预算的对照，才能检验完整 CADD loop 的增益。
