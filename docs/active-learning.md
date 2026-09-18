# ETALON：可组合计算协议的主动学习闭环

## 这一轮改了什么

这是架构性补齐，而不是给原有脚本增加一个 AL 按钮。现有检查、授权和基础设施封装继续使用，
MolCascade/PRISM 的 vendored 源码不修改。开发按以下迭代进行：

1. 修复旧闭环：测量值和溯源完整写入账本；合并 preflight/postflight；拒绝重复、错配、非有限标签；
   上一轮选出的批次实际限制下一轮计算；已接受的配置修改按历史顺序应用，原配置不覆盖。
2. 新增 `etalon.active`：持久化实验状态、预算预留、逐轮训练、多端点选点、执行与结果准入。
   控制器不再只学习静态实验 panel，也能从本轮计算结果更新。
3. 将 MolCascade 作为组件库：支持单组件、任意自定义 tiers、flat pipeline；增加真实组件执行器；
   不同时间可以注册新的 cascade 协议，旧数据保持原来的端点归属。
4. 增加恢复测试、真实 CPU 组件集成测试、封闭 oracle 回放、五类等预算基线和文档；
   修正旧 conformal 输出对 coverage 的过强解释。
5. 增加受限协议演化外循环：持久化精确编辑白名单、纯编译契约验证、限额试用、依据观测的显式晋升/退役；
   将静态依赖图与实际 artifact、动作和分子关联，阻止错协议结果进入受控端点的训练集。
   说明与验收见[协议学习](protocol-learning.md)和[本轮验证](validation-protocol-2026-09-17.md)。
6. 增加[受控协议搜索](protocol-search.md)：枚举并明确授权有限编辑组合，冻结共同审计面板，
   从完整面板的留一预测帮助与失败惩罚中学习下一个试验选择。反馈只冻结一次，不用晋升布尔或训练内相关性作奖励；
   这是共享线性选择基线，不是新 bandit 算法，也不是跨靶点或终端 CADD 效果证明。

已实现不等于已经证明科学优势。这里没有新增真实 docking/MD/FEP 结果，也没有完成生成模型或 RL 训练。

## 与 published 工作的关系

| 工作 | 应当吸收的能力 | 本次 ETALON 的对应落实 / 尚缺部分 |
|---|---|---|
| [MF-LAL，ICML 2025](https://proceedings.mlr.press/v267/eckmann25a.html) | 结合多成本 oracle、代理模型和生成过程的反馈 | 实现按端点学习与查询预算；尚无其联合生成模型，不能称为复现或超越 MF-LAL |
| [REINVENT + ESMACS，JCTC 2024](https://pubs.acs.org/doi/10.1021/acs.jctc.4c00576) | 将物理计算反馈用于生成式 AL，并重视 ensemble 估计 | 结果成为下一轮训练数据；不把单次轨迹、帧间 SD 或单次 docking 当成可靠的亲和力精度 |
| [LIDDIA，EMNLP 2025](https://aclanthology.org/2025.emnlp-main.603/) | 面向任务的 LLM 编排及多靶点评估 | 保留 MCP/LLM 接口，数值策略和预算由可审计控制器执行；尚缺相同任务集上的 agent 级对照 |

AL、多保真、LLM 工具编排本身都不是 ETALON 的新颖性。值得检验的研究假设是：
**在计算质量不均、成本不确定且工作流可重组的情况下，联合选择分子、协议和重复/修复动作，
能否提高单位真实成本获得的有效科学信息或实验命中数？**
初版实现了协议选择、端点级质量折扣、成本更新和通用重复动作；后续本轮进一步补入全池 KG、
分子条件化准入估计、校准限额和确认预算保护（见下节）。自动修复规划和自动图结构搜索仍未实现。
这是研究路线，不是已经成立的创新声明。完整的先例核实与创新边界见
[深度研究与创新定位](research-innovation-2026-09-17.md)。

## MolCascade 不是固定漏斗

`campaign.pipeline.DEFAULT_FUNNEL` 仍是历史成本规划示例，**不是 `active` 执行器的必经路线**。
MolCascade 的 default cascade 也不被自动附加到新设计上。

```text
整个候选池 + 已取得的合格标签 + 剩余预算
                 ↓ 每轮重新拟合
         分子 × 已注册协议 × replicate
                 ↓
     单组件 / 自定义 cascade / 显式其他 executor
                 ↓
       原始结果、成本、检查、协议身份入库
                 └──────────→ 下一轮
```

一个协议可以只调用性质计算；另一个可以是“SA score → 指定 docking → 指定 readout”。
也可以在同一个 tier 中使用 `all`、`any`、`at_least`，或重排串行组件。
输入、输出 contract、依赖及 `evidence_from` 由 MolCascade compiler 检查；“单独使用”不意味着
可以忽略一个组件所需的受体、构象或前置数据。

```python
from etalon.campaign.design import component, compose
from etalon.active.cascade import CascadeRecipe, Readout

properties = component("properties", "features.rdkit_properties@0.1.0")
config = compose("properties-only", [{
    "id": "measure", "title": "One selected component", "criteria": [properties]
}])
recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
endpoint = recipe.endpoint("mw-v1", target="example", quantity="molecular_weight",
                           units="Da", cost=1.0)
```

`compose` 只提供 I/O、登记和你传入的组件；不自动插入其它科学筛选层，也不隐式添加阈值 gate。
默认登记策略明确保留所选 tautomer；其它化学状态策略可通过 `standardize_settings` 配置。
上面的分子量例子只是可运行验证，不是优化药物的目标。完整的多轮示例见
[`examples/component_learning.py`](../examples/component_learning.py)。

`Screen.plan(config, library)` 编译 cascade；`Screen.plan(flat_pipeline_config)` 编译 flat pipeline。
flat pipeline 自己绑定输入与 target，额外传 `library`/`target` 会报错，不会静默忽略参数。
`python -m etalon active components` 同时列出科学 criterion 和底层 plugin 的输入/输出契约。

### 跨轮重设计

先冻结新配置、readout 和资源，再创建新端点：

```python
# new_recipe 是由新的组件组合冻结得到的 CascadeRecipe。
new_endpoint = new_recipe.endpoint("dock-protocol-v2", target="same-target",
    quantity="docking_score", units="kcal/mol", cost=2.0)
store.register_endpoints([new_endpoint], rationale="evaluate a revised docking workflow")
executor.register(new_endpoint, new_recipe)
campaign.run(max_rounds=3)
```

注册多个候选协议后，AL 可以在它们之间选择。当前不会凭空搜索和生成任意科学协议；
新配置仍须由使用者或上层设计器明确构造并注册。已有端点不能换配置、换单位或换靶点，
最终 objective 也不能在同一实验账本里悄悄改变。

上面的直接注册方式保留为 legacy API。对于需要限额探索、修复来源和晋升审查的新变体，使用
[`ProtocolRegistry`](protocol-learning.md)：`propose → validate → start_trial → promote/retire`。
编译成功不自动注册，开始试用也不自动晋升；受控协议不能借新 endpoint 别名绕过约束。
`CascadeExecutor.from_journal(...)` 可恢复已绑定配方，无需依赖旧进程的内存 dispatch table。

`CascadeRecipe` hash 包含配置、指定输出、MolCascade commit 以及绑定文件的内容 hash。
绝对 `path`/`*_path` 输入自动绑定，其它权重/资源应显式放入 `files`。执行前、执行后和成功准入前检查内容；
这不是不可变资源快照，不能检测两次核验之间“改动后恢复”的情况。实际执行计划另外与配方独立编译结果核对。
对每个 action 只写入所选分子的输入，运行目录独立；不会每选一个分子就重跑整个原始库。
当前桥接器逐分子执行，尚未实现 GPU 大批量合并和跨 action 的共享前置计算调度。

## 学习对象与安全边界

- `Candidate`：稳定的化学状态 ID、SMILES、特征、scaffold、来源和可选 handoff。
  `molecular_candidates` 可接收全库，不依赖默认 cascade 的最终幸存者；无法解析的 ID 单独返回。
  真实状态被 standardization 改变时，不将新状态的数值硬贴到旧状态上。
- `Endpoint`：靶点、observable、单位、protocol、方向、噪声下限、成本和 replicate 上限。
  docking、MM/PBSA 和 pIC50 是不同端点；共享 `kcal/mol` 不代表同一个物理量。
  RBFE 的 ΔΔG 是边标签，本接口明确拒绝把它当成单分子绝对亲和力。
- `Evaluation`：数值、单位、实际或明确标注的估计成本、状态、检查及 provenance。
  failed/invalid/blocked 仍保存，默认不进入回归模型；错配结果保留原始记录后隔离。
- `Action`：具体分子、端点、replicate、预留成本和执行前的预测/选择理由。
  同一分子/端点的失败尝试也消耗 replicate 配额，不做隐形重试。

昂贵 handoff executor 执行前要求实际 receptor 文件、preflight 和授权 token；
`PrismStage` 现在还会核对实际 receptor 与授权内容，防止检查后文件被替换。
这不是进程级安全沙箱：直接绕过 ETALON 调用底层工具仍是使用者的责任。

无 handoff 的性质组件和离线 oracle 显式设置 `requires_handoff=False`，不是给 MD 绕过检查的捷径。
通用 `StageExecutor` 接已有 expensive-stage callable；`CascadeExecutor` 接组件组合。
二者都不能凭空制造缺失的亲和力结果。旧 `PrismStage` 仍不自动完成 MM/PBSA 分析或独立 replica 工作流；
不能把“build/MD 跑过”写成“得到有效 binding affinity”。

## 当前策略：可检验的基线，不是最优算法声明

`MultiEndpointGP` 对各端点分开标准化，再用同一分子上的配对已观测数据估计端点相关性。
相关性向零收缩，允许负相关，任务协方差为半正定；没有足够配对证据时不假定低保真能传递信息。
每轮只用准入后的新旧观测重新拟合。模型 hash、训练 action IDs 和计数写入 round。

`cost_aware` 使用局部 objective 方差下降及 expected improvement 的启发式，除以成本，
再乘端点级 Beta-Bernoulli 准入率估计。包含显式高保真 bootstrap、配对校准、随机不确定性探索、
批内 scaffold 惩罚。`explore_fraction` 是单个报价切换到不确定性评分的概率，
不是严格的每批固定探索配额。批内不进行精确 fantasy 更新，每个分子每批最多一个动作。

成本报价为配置初值与正成本观测经验 90% 分位数的较大值；这是保守调度经验，不是运行时间上界。
`cost_only` 只去掉可靠性折扣，**保留完全相同的质量准入检查**，用于区分 QC 与选点策略的效果。
`random`、`greedy`、`ucb` 是 objective-only 基线，不冒充 MF-LAL 的复现。

模型标准差是经验 Bayes 后验量，不具备已证实的 conformal/frequentist coverage。
旧 `learn.conformal` 的 coverage 字段复用了拟合分位数的 OOF 残差，现明确标为 calibration diagnostic，
不是独立测试集覆盖率，更不能宣称在自适应采样之后自动保留原有保证。

当前精确 GP 面向小规模 pilot：默认最多 1,500 个准入观测、5,000 个候选。
超过时明确报错，不静默丢弃历史数据；大库需要稀疏/深度代理和分块 acquisition backend。

完整性审计后，GP/决策版本更新为 `paired-task-gp/3`、`evidence-decision/3`：拒绝重复 action
证据、错误 admitted 状态与非有限数值；极端单位使用稳定中间计算，无法可靠表示时要求换单位，
不把溢出/下溢伪装成零不确定性。`cost_aware` 仍包含历史 bootstrap、校准与多样性启发式，
并非严格单位不变的 acquisition。无环分组使用 canonical SMILES 身份，不再依赖输入行号；
这能保持同一分子的稳定分组，但不表示已经识别了所有相近的无环化学系列。

### 第二轮：面向可确认决策的证据选择

新增策略需要显式选择，**不会修改旧账本的 `cost_aware` 策略**：

- `mf_kg`：有限候选池上的单次 Gaussian knowledge gradient / 成本，作为已有方法基线。
- `decision_aware`：KG 加条件准入估计、可确认的决策池、有限协议校准额度和最终确认报价保护。

一次观测对候选池的影响是同一个随机变量 `Z` 引起的相关后验更新；
`knowledge.py` 对 `E[max_j(mu_j + b_j Z)] - max_j(mu_j)` 的仿射上包络作解析积分，
`b_j` 来自跨候选、跨端点的 posterior covariance。
它会把“所有候选共同平移而排序不变”的信息计为零价值。
“exact”仅限固定模型参数、有限集合和单次观测，不涵盖超参数学习、未知相关性学习或完整未来策略。
这不是新发明的 acquisition，也没有借用先例的理论保证。

`decision_aware` 的决策池包含已有合格 objective 证据，或当前可再执行并付得起 objective 的候选。
已失败且耗尽目标端点尝试次数的分子不会被当成可交付的终端选择；它仍可通过 proxy 观测帮助其它分子。
科学工具的 preflight/postflight 检查仍须实际执行，预测的可行性不等于检查通过。

新策略每轮只执行 **一个** action（即使配置 `batch_size > 1`），随后根据真实反馈重新拟合。
这是顺序控制，不是并行 batch KG。默认 `max_kg_candidates=512`；查询按块计算，但不偷偷裁掉候选。
增加此上限会提高计算开销；每个已执行 round 记录 `planning_seconds`，不把这部分时间冒充 oracle 预算。

配置项：

| 字段 | 默认 | 意义 |
|---|---:|---|
| `calibration_fraction` | 0.15 | 全 campaign 共享的配对校准预算份额；注册新协议不会重置额度 |
| `confirmation_reserve` | 1 | `decision_aware` 为仍可确认的分子保留的 objective 报价数；0 是消融 |
| `validity_mode` | `local` | kernel 加权准入估计；`global` 和 `none` 作消融 |
| `max_kg_candidates` | 512 | 全局 KG 显式规模限制，不是静默抽样数 |

校准独立标为 `protocol-calibration`，不能将其人为优先级算成 KG；至少三对有效配对数据之前，
现有 GP 不传递未知协议的信息。校准预算是报价层面的派发上限，意外的实际超支仍保留真实记账。
如果份额不足以建立相关性，策略可以退回目标端点，而不是无限买校准数据。

`local` 使用已取得的准入结局和分子特征核，每个分子的多次尝试合并为一个平均结局，
防止重复观测被当成许多独立的可靠性样本。拒绝结果的数值不参与计算；blocked preflight 不作为化学失败。
这是 kernel Beta 伪计数估计，不是概率校准保证，也未对失败与潜在目标相关的情形进行联合建模。

保护确认额度的检查在 SQLite reserve 事务中再次执行，所以旧计划不能绕过新成本报价。
实际计算仍可能超支或检查失败；保留的是**报价额度，不是成功确认承诺**。
无法再支付目标端点时明确返回 `confirmation_unaffordable`；没有有用且可执行的动作时可以保留余额停止。

```bash
python -m etalon active demo --workspace runs/decision-demo --policy decision_aware --rounds 100
python -m etalon active recommend --database runs/decision-demo/campaign.sqlite
python -m etalon active decision-benchmark --workspace runs/decision-benchmark \
  --seeds 0,1,2,3,4 --output runs/decision-benchmark/report.json
python examples/component_learning.py --workspace runs/components-decision --policy decision_aware
```

`ActiveCampaign.recommend()` 和 CLI `recommend` 只读，不查询 oracle，不派发工具。输出三个明确不同的对象：

1. `provisional`：不受可执行性限制的模型预测最优，可能暂时不能确认。
2. `attainable`：已有目标证据，或当前可执行并付得起目标确认的候选中，后验均值最优者。
3. `evidence_backed`：已有准入 objective 观测的候选中，后验均值最优者；列出真实 observation IDs/数值/协议。

没有合格目标观测时第三项为 `null`。三项均不声称真实全局最优或实验活性；若 objective 为 docking，
第三项也只是合格 docking 证据。它们不使用单次含噪标签的极小值来代替推荐决策。

新 `decision-benchmark` 保留原 synthetic-v1，不改答案、不挑种子，并将所有组的 `batch_size` 统一为 1。
比较 random、UCB、旧 heuristic、MF-KG、去准入折扣、去确认 guard、全局准入和完整策略；
报告证据支持推荐 regret、纯模型推荐 regret、历史最好标签 regret、失败花费、目标查询数和实际规划耗时。
这些结果与旧 batch-size=4 的历史表不能直接当作受控前后对比；两份记录都保留。
两套分子策略基准账本都绑定控制器/指标/QC/预算模块源码 hash 和 NumPy/SciPy 版本；改变实现后须用新输出目录，
不能将旧轮次与新算法静默拼为同一次实验。这不等于已经冻结整个操作系统或外部科学工具环境。
没有预先绑定实现身份的旧执行不能事后被当前版本追认；需保留旧结果并使用新目录开始比较。

## 持久化、预算和恢复

SQLite 同一事务记录状态和事件。顺序为 reserve → started → resolved；消耗成本以最后的实际记录为准。
`plan`、`recommend`、`inspect` 与 `export` 内部使用一致的提交快照；组合 `inspect`/CLI plan/MCP plan
只拟合一次模型，计划和推荐共享 `event_cutoff`。执行启动使用该截止事件做 CAS 校验，过期计划不派发。
并发连接不能重复预留同一份可用预算。外部工具可以超出估算，因此实际余额可能为负；此时停止后续派发，
不会把超支抹平。executor 未返回计量值而直接异常时，保守按预留金额记账，并标明“实际成本未知”。

进程中断留下的 running/reserved action 不自动重跑。先查实际作业，再明确恢复：

```python
from etalon.active import CampaignStore, Evaluation

store = CampaignStore("runs/live/campaign.sqlite")
pending = store.status()["pending"]
# 核实调度器/结果文件后，为某个 pending action 写入真实结局，而不是猜测它没执行。
# store.resolve(action_id, Evaluation(candidate_id, endpoint_id, actual_value,
#               units, actual_cost, status="ok", provenance={"job_id": verified_job_id}))
# 确认旧 worker 已停止，并解决全部 pending 后，显式结束遗留 round：
# store.recover_idle_rounds(reason="operator confirmed worker stopped and reconciled every real outcome")
```

不完整 round 不会被新 worker 自动接管，即使暂时还没有 pending action。
只有在核实旧 worker 已停止、解决全部 pending 并显式调用 `recover_idle_rounds` 后，才标为 interrupted；
随后可以开始新 round。不要把没有 pending 等同于旧 worker 已退出。
历史导入必须给稳定 `source_id`，重复导入幂等；已发生但不计入新预算的 sunk cost 可以显式填 0，
否则导入成本计入同一预算。不要通过反复导入不同 source ID 人为复制同一个实验。

CLI `status`、`plan`、`export` 只读现有数据库，不创建空账本；`export --output ...` 只创建你指定的输出文件。
`--output` 不覆盖已有文件。MCP 增加 `etalon_active_status`、`etalon_active_plan`、`etalon_active_replay`；
其中 replay 只执行明确给定的离线 oracle，不导入任意 Python executor 或启动真实外部计算。

## 可复现实验

建议使用项目独立虚拟环境，避免升级系统 Anaconda 依赖：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test,dev,cascade]'
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m ruff check src tests tools examples
.venv/bin/python tools/verify_assets.py --deep
```

三种验证不能混为一谈：

1. 数值/工程测试：三轮模型更新、已选批次执行、准入隔离、并发预算、错误结果、重启和成本追踪。
2. 真实 CPU 组件测试：对真实 SMILES 调用 RDKit/MolCascade，独立计算 readout 校验，验证单组件和重排。
   分子量/logP 的测试通过不等于 docking、MD 或亲和力优化有效。
3. synthetic 回放基准：64 个候选、固定 charged warm start、每 seed 同一 oracle/预算/特征/QC，比较五种策略。
   隐藏标签只有 executor 查询或事后 evaluator 可以读取；不传给选点器。
   此处比较的是 oracle 查询预算，不包括代理模型拟合和调度的 CPU 成本。

本轮实际运行的环境、测试和负结果见 [验证记录](validation-2026-09-17.md)。
后续决策控制器的 459 项回归、真实组件复跑和八组公平消融见
[决策迭代验证](validation-decision-2026-09-17.md)。新策略与 UCB/MF-KG 在本例的最终 regret 持平，
尚无局部准入模型带来独立收益的证据。

```bash
python -m etalon active demo --workspace runs/demo --rounds 5
python -m etalon active status --database runs/demo/campaign.sqlite
python -m etalon active plan --database runs/demo/campaign.sqlite
python -m etalon active export --database runs/demo/campaign.sqlite --output runs/demo/evidence.json
python -m etalon active benchmark --workspace runs/benchmark --seeds 0,1,2,3,4 \
    --output runs/benchmark/report.json
python examples/component_learning.py --workspace runs/components
```

`runs/` 不纳入 Git，但保留在本机；实验源码和测试纳入版本控制。回放 manifest 固定为
`schema_version=1`，包含 `spec`、`endpoints`、`candidates`、`oracle`、可选 `initial`。
oracle 每条使用 `Evaluation` 字段，加可选 `replicate`；initial 每条为 `source_id` + `result`。
配置结构见 `synthetic_manifest()`；替换为真实离线表时需要完整覆盖所声明的可查询动作。
oracle 的 hash 绑定账本，不能在续跑时偷偷更换未查询答案。部分覆盖数据集需要显式可行域模型，
不能把“数据库没记录”当作可免费重采样的失败。

首轮五 seed smoke benchmark **没有显示成本/质量感知策略稳定优于简单基线**。
这是必须保留的负结果；不可用通过单元测试、较低 docking 分数或 synthetic regret 来宣称发表级效果。

## 下一轮真正需要推进的研究工作

1. 选择公开、协议一致的多保真数据与固定外部测试集，至少覆盖多个靶点及 scaffold 外推。
   数据准备和测试集冻结在调参之前完成，失败计算计入真实预算。
2. 与标准 MF-BO/MF-LAL、相同优化器加相同 QC、仅 QC、仅成本、仅可靠性、固定 cascade 比较。
   对配置重组单独做消融，不能把更好的工具/更大的预算归功于 agent。
3. 验证新条件准入估计和末轮确认机制，进一步研究分子条件成本模型、失败与性质相关的模型以及
   未知协议相关性学习的真正前瞻价值；当前局部核估计与独立校准配额只是可检验基线。
4. 接通经验证的独立 MD replicas、MM/PBSA 分析和实际资源计量；RBFE 另建图/边观测模型和网络分配策略。
5. 基于真实反馈轨迹增加受限的修复动作和协议图提议，验证能否降低无效花费。
   生成模型/RL 是后续可接入的策略，不应先于可审计闭环和可靠 benchmark。

发表所需的创新证据还没有完成。当前交付的是可执行、可恢复、可比较、可重新组合组件的研究底座。
