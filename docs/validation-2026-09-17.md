# 本轮验证记录（2026-09-17）

这些结果验证工程闭环，不验证药物发现效果。运行了本地测试（含可用 GPU 上的数值核回归）、
CPU RDKit 组件与 synthetic 回放；未启动 docking/MD/FEP 生产计算、付费 API 或外部 GPU 作业，
未修改 MolCascade/PRISM vendored 源码。

## 环境

Python 3.11.5；NumPy 1.26.4；SciPy 1.11.1；Pydantic 2.13.5；PyArrow 17.0.0；
RDKit 2025.3.2；pytest 7.4.0。

基础 Anaconda 的 Pydantic 1、PyArrow 11 不满足 MolCascade 要求。因此在临时虚拟环境
`/tmp/etalon-test-I5B7zM` 中覆盖安装兼容依赖，未替换主环境软件包。
该临时环境用于本次验证，不应成为项目的永久依赖路径；重建方法见 [使用指南](active-learning.md)。

## 执行证据

- 第一轮全量回归：391 passed，1 个 RDKit converter 重复注册 warning。
- 补充回归后的第二轮：397 passed，1 个同类 warning；没有跳过测试。
- 最终全量回归：**398 passed**，1 个同类 warning，0 skipped；236.00 秒。
  最后一项新增测试保证：拟合完成后才到达的外部标签不会被误记为该模型的训练数据。
- `ruff check src tests tools examples` 通过；`git diff --check` 通过。
- `tools/verify_assets.py --deep`：两个基础设施源码树及 reference 资产均匹配 manifest。
- 仅用 Python 标准库导入 `etalon.active` 和运行原 planning CLI 成功；没有提前导入 NumPy/SciPy/RDKit。

### 真实组件闭环

命令：

```bash
python examples/component_learning.py --workspace runs/components-validation-v2
```

8 个真实 SMILES，第一轮只注册性质组件；随后注册“SA score → properties”组合的新端点。
共执行 8 轮、16 条结果全部准入；每轮拟合使用的观测数为 `0, 2, 4, 6, 8, 10, 12, 14`。
预算 24 个 demonstration quote units，记账 24，未决预留 0，余额 0。
这些成本是显式示例报价，不是实测 CPU/GPU 时长；MW/logP 也不是 binding-affinity 结果。

账本：`runs/components-validation-v2/campaign.sqlite`。
完整导出：`runs/components-validation-v2/evidence.json`。
输入、编译计划、原始 MolCascade artifact 与每个动作的独立运行目录保留在相邻 `calculations/` 中。

### 五策略回放

命令：

```bash
python -m etalon active benchmark --workspace runs/active-validation \
  --seeds 0,1,2,3,4 --output runs/active-validation/benchmark.json
```

每个 seed 64 个 synthetic 候选；同一 oracle、同一计费 warm start、相同特征与 QC，oracle 预算 120。
下面是 5 个 seed 的 simple regret 均值及样本标准差，越低越好。预算不包含代理拟合及调度的 CPU 成本。

| 策略 | regret 均值 | seed 间标准差 | 平均 oracle 花费 |
|---|---:|---:|---:|
| random | 0.45064 | 0.43003 | 120 |
| greedy | 0.32300 | 0.41927 | 120 |
| UCB | 0.32300 | 0.41927 | 120 |
| cost_only | 0.32997 | 0.42492 | 120 |
| cost_aware | 0.49321 | 0.34879 | 120 |

这里没有成本/质量加权策略优于简单基线的证据。不能删除这组结果再挑一个有利 seed，也不能据此断言
它在真实 CADD 上总体更差：样本很少、数据是 synthetic，并未进行预注册显著性检验。
它提示下一轮需要检验局部信息评分、配对校准投入、质量估计和最终高保真确认之间的取舍。

完整逐查询曲线、各 seed 结果、环境版本和账本路径在 `runs/active-validation/benchmark.json`。
`runs/` 的结果留在本机但不提交 Git；生成代码和回归测试在仓库中，可以用新输出目录重现。

## 暂未验证

真实 docking/MD/MM-PBSA/FEP 闭环、跨靶点与 scaffold 外推表现、实验命中率、
对 MF-LAL 等 published 方法的公平优越性，以及新策略的理论保证。
当前策略不能作为这些结论的替代证据。
