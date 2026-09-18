# 协议经济停止：2026-09-17 验证记录

本轮在[上一阶段有限搜索](validation-search-2026-09-17.md)之上增加可选 `audit_ei`。
[方法与限制](protocol-stopping.md)单独说明；本页保留实际回归、CPU pilot 和全部预设合成配置的结果。
没有修改 MolCascade/PRISM 资产，没有运行真实 docking、MD、FEP、湿实验或付费 API。

## 1. 实现与回归

修改 `active/protocol_score.py`、`active/proposer.py`、只读/离线 CLI 和真实 CPU 示例；
新增独立 `protocol_stopping_benchmark.py`，不替换原合成基准或覆盖历史运行产物。

新增专项共 **129 项**：数学 49、搜索集成 28、停止 benchmark 42、CLI 10。
其中数学包括三种旧策略完整输出/hash 的 golden 对照；搜索包括双版本/成本单位契约、
停止只读性、预算过滤优先级、已选中完整 panel 的恢复及费用、独立人工权限。
最终数学＋新 benchmark＋CLI 专项为 **111 passed，1.77 秒**。
全量回归为 **898 passed、0 skipped、1 warning，271.93 秒**，唯一 warning 为既有 RDKit converter 重复注册。
全量测试收集后，benchmark 又补了一项“换算率乘总费用不能溢出”的输入校验回归；
该最终改动由上述 111 项专项重新验证；最终套件共有 899 项，两组回归不是同一次全量运行。

环境沿用 `/tmp/etalon-test-I5B7zM/bin/python`，Python 3.11.5、NumPy 1.26.4、SciPy 1.11.1、
RDKit 2025.03.2、Pydantic 2.13.5、PyArrow 17.0.0。
全套旧回归包含 RTX 4090 / PyTorch 2.7.0+cu126 数值核测试；不能称整个套件都是 CPU-only。
新增真实示例仅调用 CPU 组件。

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/etalon-test-I5B7zM/bin/python -m pytest -q -p no:cacheprovider
python -m ruff check src tests tools examples
PYTHONPATH=src /tmp/etalon-test-I5B7zM/bin/python tools/verify_assets.py --deep
git diff --check
```

Ruff、diff whitespace 和深度资产校验通过，资产仍匹配原 manifest。

## 2. 真实 CPU 闭环：搜索停止后，显式继续内层 AL

已执行两次独立新目录 `runs/protocol-stopping-v1/` 与 `runs/protocol-stopping-v2/`，均使用：

```bash
PYTHONPATH=src python examples/protocol_search.py \
  --workspace runs/protocol-stopping-v2 --output runs/protocol-stopping-v2/report.json \
  --policy audit_ei --opportunity-cost 0.025 --campaign-rounds 2
```

`0.025` 在运行前明确指定，含义为每 quote 单位要求的 panel skill 提升；不是测得价格或推荐生产超参数。
v1 为开发中运行，v2 在预测契约指纹补齐后复跑，以下以 v2 为最终证据；两次数值摘要一致。
均使用上一轮相同四分子参考面板、七个有限编辑组合、trial 预算 16 和最多四次试验。

| 阶段 | 实际 action 数 | 有效数值 | 报价单位支出 | 说明 |
|---|---:|---:|---:|---|
| 固定 objective panel | 4 | 4 | 4 | 先取得并冻结参考证据 |
| trial 1：gate＋SA | 4 | 0 | 4 | gate 合法筛掉四个小分子，冻结 skill −1 |
| trial 2：batch size 修改 | 4 | 4 | 4 | 完整面板 skill 0.9917355371900827 |
| 显式普通 AL 续跑 | 2 | 2 | 2 | 查询 propanol、acetamide 的原 objective endpoint |

搜索停止时：**已付总额 12/64，搜索本身付费 8/16，搜索仍有 8、总 campaign 仍有 52 可用**。
仍有未试验组合且未达到四次上限；停止确实来自经济判断，不是额度用完。
此时最佳剩余候选预测 EI 为 `0.0012419389605353862`，完整面板换算成本为 `0.1`，净值 `−0.09875806103946462`。
所有可行候选都不值得按当前一步评分目标继续，故 `economic_stop`。

随后示例**显式**关闭 search，并根据命令中的 `--campaign-rounds 2` 请求运行普通 AL；停止本身没有调用工具。
最终 **14 actions、10 admitted、花费 14/64、剩余 50、reserved 0、无 pending**。
未晋升任何协议；两个已执行 trial 都在完整评分后显式退役。

上一轮不停止示例做四次试验、搜索花费 16，本轮两次、花费 8；这是同一 fixture 的工程观察，
但同时改变了 acquisition，不能单靠两个真实示例把全部差异因果归于 stop。下面的匹配 no-stop 合成组才隔离该机制。
分子量重读得到的接近 1 的 panel skill 不是药物活性准确率，也不是新协议优于原 seed 的证明。

最终产物：[报告](../runs/protocol-stopping-v2/report.json)、
[只读搜索导出](../runs/protocol-stopping-v2/searches.json)、
[完整动作/观测/事件](../runs/protocol-stopping-v2/evidence.json)。原始 SQLite 和 action artifact 保留，同原规则不提交 Git。
两次只读导出前后数据库 SHA256 相同：
`233bc68751618b6176002195911dd73759e539c3b7083d2adb4283bbf55dfc65`。

## 3. 三档成本、五组策略、四场景、五种子：全部 300 runs

完整报告：[protocol-stopping-benchmark-v1/report.json](../runs/protocol-stopping-benchmark-v1/report.json)。
预设 `lambda = (0.02, 0.1, 0.3)`，seed `(0,1,2,3,4)`；每个 run 预算 6、16 个单价为 1 的候选。
旧四场景生成器不变，默认参数没有根据结果重调；最终源码重算的完整 JSON 与已保存报告逐字段完全相等。

下表仅为匹配的 `audit_ei` 与 `audit_ei_no_stop` 摘要，五组完整分项、标准差、每次决策和终止快照均在报告中。
每格 `A / B` 为“经济停止 / 同 acquisition 不停止”。`过早停止` 指事后发现还有当前预算能支付、
真实单次提升大于其换算成本的未查 arm；这不是完整多步最优性判断。

| λ | 场景 | 平均花费 A/B | 平均 simple regret A/B | 平均净效用 A/B | A 过早停止 / 5 |
|---:|---|---|---|---|---:|
| 0.02 | 加性 | 6 / 6 | 0 / 0 | 0.6800 / 0.6800 | 0 |
| 0.02 | 强交互 | 6 / 6 | 0.2600 / 0.2600 | 0.2700 / 0.2700 | 0 |
| 0.02 | 特征无信息 | 6 / 6 | 0.2332 / 0.2332 | 0.3844 / 0.3844 | 0 |
| 0.02 | 全负 | 6 / 6 | 0 / 0 | −0.1200 / −0.1200 | 0 |
| 0.1 | 加性 | 3.8 / 6 | 0 / 0 | 0.4200 / 0.2000 | 0 |
| 0.1 | 强交互 | 4.2 / 6 | 0.2600 / 0.2600 | −0.0300 / −0.2100 | 0 |
| 0.1 | 特征无信息 | 4.2 / 6 | 0.2332 / 0.2332 | 0.0844 / −0.0956 | 0 |
| 0.1 | 全负 | 6 / 6 | 0 / 0 | −0.6000 / −0.6000 | 0 |
| 0.3 | 加性 | 1.8 / 6 | 0.2600 / 0 | 0 / −1.0000 | 1 |
| 0.3 | 强交互 | 1.4 / 6 | 0.2600 / 0.2600 | −0.0300 / −1.4100 | 2 |
| 0.3 | 特征无信息 | 1.6 / 6 | 0.4032 / 0.2332 | −0.1457 / −1.2956 | 3 |
| 0.3 | 全负 | 2 / 6 | 0 / 0 | −0.6000 / −1.8000 | 0 |

应保留的负结果：

- λ=0.02 时所有组照样用完预算；全负场景在 λ=0.1 也未改善，不能说已解决所有无益探索。
- λ=0.3 虽大幅节省预算，`audit_ei` 有 **6/20** 个 run 在仍有单次正净提升机会时停止；
  全部停止策略组加起来有 **10 次**这种事后风险，其中另 4 次来自 one-hot 消融。全部 300 runs 中有 54 次模型经济停止。
- acquisition 本身并非总比 UCB 好：λ=0.02 的强交互场景中，同样花费 6，UCB regret 为 0，
  两个 EI 组均为 0.26；这不是停止导致的劣势。无信息特征场景下 UCB 为 0.091517，EI 组为 0.233168。
- `no_search` 在全负场景净效用为零，优于任何付费搜索；但在加性场景漏掉 0.8 的真实机会。
  保留它是为了让“省钱”和“发现有用协议”的代价同时可见，不是建议总不搜索。
- 仅五个种子、人工小表，不能据此确定生产 λ、声称统计显著优势或推断真实 CADD 效果。

## 4. 历史兼容与源码身份

改动前后，两个上一阶段真实账本的只读 plan hash 均未变化：

| 历史目录 | plan 规范化 SHA256 |
|---|---|
| `runs/protocol-search-v1/` | `ca4752c772ffd652df5f1a0c8bf37f6820140f7aba20ccb9f7a5d69f9c0cf2af` |
| `runs/protocol-search-v2/` | `0be68f266ce1bb6eab29e600815de2edc574418acd57a74590a4072db76ddf90` |

二者原 SQLite 字节摘要也与[上一轮记录](validation-search-2026-09-17.md)一致，没有自动迁移或修改历史证据。

最终主要实现 SHA256：

| 文件 | SHA256 |
|---|---|
| `active/proposer.py` | `ac35877d031f120654a2f1c72496b54ada346f473f6799256785ff0878836b81` |
| `active/protocol_score.py` | `d6ccb5b4dce1b9903270d22a49cee475b518d604a0898a243633ac1ff14e63fa` |
| `active/protocol_stopping_benchmark.py` | `29c41706d581553ace44fdc9d1266103ae1e3a9cf5b1dd31582772648ccf60e3` |
| `examples/protocol_search.py` | `bf7038805133cb07ba8bda346b5e2950fa840ec6fe8fcda98cbc566e6214dba8` |

## 5. 下一步应验证什么

这轮证明了可选经济停止的工程契约和可复现消融，而没有证明新的 CADD 算法。
下一步需要把“新增协议的面板评分”替换/连接为“它对内层最终确认推荐的增量价值”，
保留协议设计、校准参考、失败和最终确认的共同预算；并用独立 held-out scaffold/靶点检测泛化与过早停止。
原有循环和默认策略继续保留，不把本轮有限合成收益升级为默认科学判断。
