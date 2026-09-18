# 协议学习外循环：2026-09-17 验证记录

本页对应 [协议学习说明](protocol-learning.md)。
本页记录协议外循环及执行一致性加固，不替代此前的
[AL 验证记录](validation-2026-09-17.md)，不覆盖历史负结果。

## 1. 本轮验证对象

- 有限设计空间：允许的编辑、拒绝的资源/代码变更、不可变身份及无效变更。
- 纯编译检查：组件契约、依赖、readout 数字类型、模板图 hash。
- 运行溯源：真实 stage/artifact 与图边对应，缺失或错误来源不能假装完整。
- 生命周期：propose → validate → trial → 显式 promote/retire。
- 预算：trial 上限与 campaign 总预算共同生效，原子预留、重启和超支记录。
- 受控端点准入：真实 round/action 与已绑定图/readout 匹配；导入不能伪造试用成功。
- 审查：所有未准入尝试计失败，配对按不同分子统计，不能事后修改 trial 门槛。
- 恢复：idle round 显式中断，存在 pending action 时拒绝；recipe 从账本重建。
- 真实组件示例：单组件 seed 和显式 SA-before-properties 变体，不依赖默认 funnel。

## 2. 环境与命令

使用既有临时隔离环境，不改系统 Anaconda 依赖；CLI/示例通过 `PYTHONPATH=src` 导入工作树。

| 项目 | 本轮最终记录 |
|---|---|
| Python / 环境路径 | 3.11.5；`/tmp/etalon-test-I5B7zM/bin/python` |
| NumPy / SciPy / RDKit | 1.26.4 / 1.11.1 / 2025.03.2 |
| Pydantic / PyArrow | 2.13.5 / 17.0.0 |
| CUDA / PyTorch | NVIDIA GeForce RTX 4090 可用；PyTorch 2.7.0+cu126；完整套件含既有 GPU 数值核回归，不能称 CPU-only |
| MolCascade / PRISM | `c01a6e0b5152` / `f0492d964795`；`verify_assets.py --deep` 通过 |
| 最终代码状态 | 本地未提交工作树，含前轮修改；不是已发布版本或固定 Git commit |

可复现命令（将 `python` 指向项目独立环境）：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -q -p no:cacheprovider \
    tests/test_protocol_mutations.py tests/test_protocol_graph.py \
    tests/test_protocol_registry.py tests/test_protocol_execution.py

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -q -p no:cacheprovider

python -m ruff check src tests tools examples
python tools/verify_assets.py --deep
git diff --check
```

## 3. 最终加固版结果

| 验证 | 最终结果 | 证据/备注 |
|---|---|---|
| 协议专项测试 | 154 项通过 | 图 35、编辑 52、生命周期 51、实际执行边界 16；各专项与全量交叉验证 |
| 完整回归套件 | 613 passed，0 skipped，1 warning | 193.19 秒；相较前轮 459 项新增 154 项；warning 为既有 RDKit converter 重复注册 |
| Ruff | 通过 | `src tests tools examples` |
| Diff whitespace | 通过 | `git diff --check`；另检查 10 个新增文件无行尾空白 |
| Vendored assets deep verify | 通过 | MolCascade、PRISM 及 reference/prose 都匹配 manifest |
| 加固后的真实 CPU 示例 v2 | 通过 | 7 个 action、7 条 admitted、3 个 objective 配对；晋升后退役 |
| CLI 只读检查 | 通过 | 导出 `active protocols` 与 `active export` 前后 DB SHA256 相同 |

最终产物位于 `runs/protocol-learning-v2/`：

- [运行报告](../runs/protocol-learning-v2/report.json)。
- [协议状态、门槛与配额](../runs/protocol-learning-v2/protocols.json)。
- [action、观测和事件导出](../runs/protocol-learning-v2/evidence.json)。
- `campaign.sqlite` 及 `calculations/<action_id>/` 保留真实输入、artifact 和运行账本。

只读导出前后数据库 SHA256：
`90cd8aeb4c8add77f3e724b324c5a3c79af8ba8c59ea155a67deaf789033f648`。
这些本地运行产物按项目规则忽略，不因文档链接而自动纳入 Git。

## 4. 先前 v1 pilot：不是加固后 v2 的替代证据

根任务已报告真实 v1 组件 pilot 的历史结果：7 个 action 全部 admitted，
支出 7 个 demonstration quote 单位，总预算 24；获得 3 个目标配对后显式晋升并退役。
它验证了基本控制流程，但发生在本轮最新准入/恢复加固之前。
本页保留该历史信息，不把它标成最终 v2 结果，也不据此宣称加固测试通过。

v1 实际产物路径：`runs/protocol-learning-v1/campaign.sqlite`，保留未覆盖。
v2 使用新目录 `runs/protocol-learning-v2/`，在全部执行一致性加固后复跑。
参考协议 4 次、变体 3 次，共花费 7/24 个 quote，剩余 17、预留 0。
变体的 3 次都准入且都有 objective 配对，runtime 失败率为 0；没有 historical import。
晋升和退役事件均已写入账本，退役后 `mw-with-sa` 查询额度为 0，但模型训练大小仍为 7。
模型的配对相关系数估计为 0.5（包含小样本收缩），不能解释为性能提升。

真实示例命令：

```bash
PYTHONPATH=src python examples/protocol_learning.py --workspace runs/protocol-learning-v2 \
    --output runs/protocol-learning-v2/report.json
python -m etalon active protocols \
    --database runs/protocol-learning-v2/campaign.sqlite \
    --output runs/protocol-learning-v2/protocols.json
python -m etalon active export \
    --database runs/protocol-learning-v2/campaign.sqlite \
    --output runs/protocol-learning-v2/evidence.json
```

以上为已执行命令的结构；本次实际使用表中 Python 环境，并对 CLI 同样设置 `PYTHONPATH=src`。
目录现在已有数据；复跑必须换新路径，不能删除或覆盖旧验证产物。

## 5. 回归覆盖的断言

1. 编译时没有 plugin execute、backend probe、外部 version command 或科学输出生成。
2. readout 不存在、类型错误、依赖非法或资源 hash 改变会阻止对应状态推进。
3. trial 注册与端点控制元数据同事务写入；变体用新 endpoint/protocol ID。
4. planner 与 reserve 都遵守 trial 配额；多个连接不能共同花掉同一份额度。
5. 受控 endpoint 的缺失/错配运行溯源不能入模，也不能满足晋升数量；不是恶意调用者防伪认证。
6. 受控导入保留 raw value、来源与实际费用，但 withheld；不能当免费失败重试。
7. 运行尝试中的失败、invalid、blocked、withheld 都进入 trial 失败统计；没有结果不视为零失败。
   历史导入单独报告，不进入运行失败比例分母，不提供晋升成功数，但仍占预算与 action 配额。
8. 已完成但无观测的异常状态不能被当成功；pending 未解决前拒绝审查和 idle 恢复。
9. 退役停止新查询，但保留已取得的有效旧标签与模型可用历史。
10. 重新打开 SQLite 并用 `CascadeExecutor.from_journal` 重建调度表，控制约束仍生效。
11. `recover_idle_rounds(reason=...)` 返回被标记 interrupted 的 round ID 元组，记录原因，不重跑作业。
12. CLI inspection 不启动组件，不创建不存在的账本，不自动修改协议状态。

另有执行边界测试：实际 pipeline/settings 错配在调用 `run` 前被拒绝且成本为 0；
执行后 revision/run ID 错配、图插件错配、绑定资源或 action CSV 改变均拒收并保留成本；
在 readout 读取期间才发生的资源变化也会在最终准入前拒收。
检查点 hash 不保证检测“修改后恢复”，完整方案仍需不可变 action 资源快照。

## 6. 结论的适用范围

真实组件验证仅涉及 RDKit/MolCascade 的分子性质及 SA 组件。
分子量是工程测试 objective，新增 SA 不意味着分子量更准确。
费用单位为明确标注的演示报价，不是实际 CPU/GPU 时间或货币成本。

compile certificate 只说明结构合法；lineage 只连接报告的产物；科学准入仍是独立步骤。
trial promotion 只说明预设 operational rollout 条件成立，
不证明协议更准确、更省钱、修复有效，也不证明药物设计性能提升。

本轮没有新增 docking、MD、FEP 或湿实验效力结果；
没有完成自主 DAG 搜索、自动 repair policy 或跨 campaign 自我改进效果评价。
完整测试实际包含 CUDA 路径的既有 GPU kernel 回归且未跳过，不能称为真实 MD/FEP campaign。

发表级创新仍需独立多靶点数据、相同信息/工具/预算的对照、组件与审查机制消融，
并将协议提案、失败试用、规划器和实际计算的成本共同计入。
