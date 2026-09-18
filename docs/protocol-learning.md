# ETALON：可审查的协议学习外循环

本轮把“可以重新组合 MolCascade 组件”推进为可持久化、可限额试用、可审查的协议生命周期。
它是现有分子/端点主动学习的外循环，不是自动搜索任意 DAG，也不是已证明有效的自动修复算法。
研究定位及已有方法见 [研究与创新边界](research-innovation-2026-09-17.md)；
本轮验证单独记录在 [协议验证记录](validation-protocol-2026-09-17.md)。
后续已增加[受控协议搜索](protocol-search.md)：由冻结的面板反馈排序有限、预先授权的编辑组合。
本页描述的验证、试用与晋升约束仍生效；选择器不能自行放宽权限或把排序结果当作晋升结论。

## 1. 两个循环，不要混为一谈

内循环由 `ActiveCampaign` 执行：从候选池和已准入观测学习分子—端点关系，
在当前允许的端点、预算和 trial 配额内选择查询，执行后更新模型。

外循环由显式 Python API 和操作者审查驱动：

```text
已绑定的 seed recipe + 已审查的有限编辑集合
    → propose：保存新协议提案，尚不能执行
    → validate：纯编译与 readout 检查，尚不能执行
    → start_trial：注册新端点并授予有限试用额度
    → ActiveCampaign：逐次取得真实结果，更新内循环
    → report：检查预先声明的观测数量、配对数和失败比例
    → 操作者审查：promote 继续预算内使用，或 retire 停止新查询
```

操作者可以是用户直接调用 Python，也可以是另一个经过审查的上层程序。
当前没有自主决定何时改图、自动编写新工具或根据结果自动晋升的 agent。
`proposed_by` 和 `rationale` 是可追溯记录，不是外部身份认证或密码学授权。

## 2. MolCascade 是组件库，不是固定漏斗

`compose()` 只组合明确传入的组件和必要 I/O/登记配置，不自动附加默认筛选层级。
一个 seed 可以仅计算分子性质；另一个可以串联 SA、指定 docking 与指定输出。
已有自定义 cascade 的 tier 组合仍由 MolCascade 处理，不必经过默认 funnel 的所有层。
但是组件单独使用仍须满足输入、目标与前置 artifact 契约，不能将“可组合”理解为无依赖。

本轮有限编辑针对已经冻结的 tier-first `CascadeRecipe`，不是任意 flat pipeline 的通用编辑器。
`Readout(stage_id, contract_id, value_column, filters)` 显式固定本协议要读出的量。
seed 与变体保留同一 target、quantity、units、direction 和 readout，使用不同 endpoint/protocol 身份。
旧标签不会在配置改变后被解释成新协议的结果，campaign objective 也不会被偷偷替换。

## 3. 有限设计空间：允许什么，不允许什么

`DesignSpace` 保存精确的 JSON 编辑白名单及 `max_edits`，并产生内容地址 `fingerprint`。
绑定空间需要审查理由；提案中的每个编辑必须与某个白名单成员完全匹配。
输入字典后续被外部修改不能改写已经冻结的设计空间。

| 编辑 | 含义 | 限制 |
|---|---|---|
| `set_setting` | 修改某 criterion 的现有 settings 字段 | JSON pointer 指向已有值，`expected` 必须匹配，禁止资源/代码/schema 字段，无效变更拒绝 |
| `reorder_criteria` | 重排某 tier 的 criteria | 必须为完整排列，不能增删 ID，也不能对禁用 tier 制造名义变更 |
| `insert_criterion` | 在某 tier 指定 criterion 前或末尾插入组件 | 完整 criterion、唯一 ID、精确的已知内置 backend，不能引入路径/代码等越界资源 |

这不是“任意 JSON patch”：不能通过参数编辑替换受体、权重、文件、URL、shell 命令、
导入模块、插件实现、分子登记策略或 observable。需要这些变化时必须重新构造并审查协议。
已绑定资源的内容 hash 和 MolCascade commit 会重新核查；资源变化不能借旧协议身份执行。
registry 不接受已注册 recipe 的新别名作为新提案；受控 trial/promoted/retired 协议也不能通过
legacy 注册入口增加别名来绕过配额。未纳入 registry 的旧端点仍使用原有接口与信任边界。

示例中的有限编辑只插入一个真实 CPU SA 组件：

```python
from etalon.active.mutations import DesignSpace
from etalon.campaign.design import component

edit = {
    "op": "insert_criterion",
    "tier_id": "measure",
    "before": "properties",
    "criterion": component("synthesis", "synthesis.rdkit_sa_score@0.1.0"),
}
space = DesignSpace((edit,), max_edits=1)
```

重组之后测量的仍是原 `properties` 阶段的 `mw`，并不是把 SA 数值当分子量标签。
增加 SA 不会提高分子量预测准确性；这个例子验证的是控制流程，不是科学收益。

## 4. 三种证据强度必须区分

### 4.1 编译证书：结构上能否组成合法计划

`inspect_recipe(recipe)` 使用固定 MolCascade 边界与内置 registry，检查配置、依赖、
输入输出 contract、readout 的阶段/端口/数字类型、过滤字段及绑定资源。
返回 `graph_hash`、阶段及边、`protocol_id`、readout 和 `certificate_scope="compile_only"`。

这个入口不调用科学组件的 execute、不探测 backend、不运行版本命令，不生成科学输出文件。
但是它会读取并 hash 已绑定的资源文件，不能误解为完全没有 I/O。
证书中的 revision 使用 placeholder library，表示协议模板，而不是某次分子计算的 revision。

编译通过不证明 backend 运行依赖已经安装、不保证 nullable 字段会填值，
不保证分子能通过 gate，更不证明某项亲和力计算正确。

### 4.2 运行溯源：哪些图节点确实报告了产物

`CascadeExecutor` 为真实 action 绑定所选分子，执行后记录具体 plan/run 与 evidence graph。
执行前，`verify_execution_plan()` 根据原 recipe 和真实 action library 独立编译预期计划，
核对实际 compiled 与实际 executable pipeline 的 settings、输入绑定、端口、插件及 revision。
模板到实例只允许 source 的 library path 改变，不允许顺便换 source reader 或其它参数；
运行结果的 revision/run ID 也必须对应本次计划与动作。
`execution_lineage()` 把报告的 artifact ID 连接到编译图的依赖边，检查 stage/plugin 身份。
某节点只有自身已提交且上游可用时才标为 `available`；未报告的阶段不会凭空补成成功。

这个 lineage 函数本身不读取或校验 artifact 字节；
数据读取与完整性检查仍由 `Screen.read` 等既有边界负责。
运行实例的 revision 和模板 revision 不应混成一个 ID。

绑定资源在执行前、执行后及成功返回前检查 hash，所选分子的 CSV 也有运行后检查。
执行期间发现改动或丢失会拒收标签，但保留执行报价成本。
这是检查点一致性检测，不是不可变文件快照，不能防止两次检查间的“改动后恢复”。

### 4.3 科学准入：结果是否可用于学习与晋升

`CascadeExecutor` 仍检查单一化学状态、原候选身份、明确 readout、唯一非空有限数值和状态。
对通过 registry 管理的受控端点，账本还要求真实 round/action 执行记录、
明确匹配本次 action ID 和 candidate ID、已绑定协议的 MolCascade 图与 readout 运行溯源，
才能将该结果用作受控试用证据。
仅向 `import_evaluation()` 填入一个数值或伪装成 MolCascade 的 metadata 不构成真实试用。

不满足该门槛的导入保留原始结果和费用，但 withheld，不进入受控端点的准入标签或晋升证据。
没有通过准入不等于删除结果，也不允许把已发生费用改成零。
这些是应用内证据一致性约束，不是防止恶意 Python 调用者/数据库管理员伪造证据的安全沙箱。

合格 readout 仍只支持该 objective 所声明的结论；
若 objective 为分子量或 docking，不能据此宣称已验证药物活性、临床有效性或协议更优。

## 5. Trial 是有限试用，不是给新协议无限预算

`TrialPolicy` 在看到试用结果前声明：

```python
from etalon.active.protocols import TrialPolicy

limits = TrialPolicy(
    budget=3,
    max_actions=3,
    min_admitted=3,
    min_pairs=3,
    max_failure_fraction=0,
)
```

- `budget` 是同一 campaign 总预算内的额外上限，不是另外一笔经费。
- `max_actions` 限制所有尝试，不只统计成功执行。
- `min_admitted` 按不同分子统计准入结果，不用重复行堆数量。
- `min_pairs` 要求试用协议和固定 objective 都有同一分子的合格观测。
- `max_failure_fraction` 统计实际运行尝试中的未准入比例；失败、blocked 和 withheld 不能被悄悄排除。

历史导入不是实际 trial 运行，不进入该比例的分母，也不能增加成功数；
它的费用和已记录 action 仍占用配额，报告另外列出 `imported_results`。

局部 admission 模型可以不把 blocked 当化学失败来学习；
这不代表试用运行报告也能忽略 blocked，二者回答的问题不同。
有 pending action 时不能晋升；没有结果时不能把失败率记成零。
试用门槛不能看见结果后直接放宽，不能通过重启重置花费或 action 计数。

规划器只考虑剩余额度，`CampaignStore.reserve()` 在同一个 SQLite 写事务内再次检查配额。
因此一个旧 plan 不会绕过另一个进程已经占用的试用额度。
实际成本可能超过报价；超支必须保留，后续新派发受余额限制，不宣称报价就是硬上界。

`report()` 给出 evidence action IDs/hash、有效分子数、目标配对数、失败比例与未达成条件。
`promote()` 要求操作者理由、编译身份仍有效及预设门槛已达成。
晋升只解除额外的 trial 配额，campaign 总预算、每分子上限和科学准入仍然生效。
门槛达成不是独立对照实验，也不证明准确率、成本优势或因果修复效果。

`retire()` 禁止新查询，但不删除 endpoint、recipe、成本或历史准入标签。
模型仍可使用这些原协议身份明确的观测；退役不是对历史结果作事后撤销。
如果历史证据本身失效，需要另行设计明确的撤销机制，不能靠退役隐式修改过去。

## 6. 完整可运行示例

先按项目依赖说明准备支持 MolCascade 的独立环境，避免改系统 Anaconda 依赖。
在仓库根目录运行：

```bash
python examples/protocol_learning.py --workspace runs/protocol-learning-example \
    --output runs/protocol-learning-example/report.json
python -m etalon active protocols --database runs/protocol-learning-example/campaign.sqlite
python -m etalon active recommend --database runs/protocol-learning-example/campaign.sqlite
```

完整源码见 [examples/protocol_learning.py](../examples/protocol_learning.py)。
工作区必须是新的；已有数据库时示例明确拒绝覆盖。重复验证请换一个新路径。
示例执行真实 RDKit/MolCascade CPU 组件，不执行 docking、MD、FEP 或付费模型调用。

示例先对真实 SMILES 取得 objective 观测，再提案 SA-before-properties 变体，
纯编译后授权限额试用，重新打开账本恢复 executor，取得配对结果，显式晋升并退役。
最后验证旧观测不变、退役端点不再被派发、模型没有因为退役丢掉标签。
其中分子量目标和 demonstration quote 成本仅用于工程验证。

以下是完整示例中主要的外循环 API；调用时 `store`、`seed` 和 objective 已经创建：

```python
from etalon.active.protocols import ProtocolRegistry

registry = ProtocolRegistry(store)
registry.bind_seed(objective.id, seed, rationale="reviewed reference recipe")
space_id = registry.bind_space(space, rationale="reviewed finite CPU variation")
proposal_id = registry.propose(
    objective.id, "mw-with-sa", space_id=space_id, edits=[edit], cost=1,
    rationale="test one bounded component variation", proposed_by="operator",
)
certificate = registry.validate(proposal_id)
if not certificate["ok"]:
    raise ValueError(certificate)
registry.start_trial(proposal_id, limits, rationale="authorize bounded trial")
# Real campaign actions must acquire evidence before this review.
report = registry.report(proposal_id)
if report["rollout_criteria_met"]:
    registry.promote(proposal_id, rationale="reviewed the recorded trial evidence")
```

状态变更都要求完整结束当前 round，并且没有 reserved/running action。
CLI `active protocols` 只读账本，展示提案、报告、配额和已绑定 recipe；
它不自动 validate、启动试用、晋升、退役或运行科学计算。
Python API 是显式的可变更入口，`--output` 仅可创建新的 JSON 导出文件而不覆盖旧文件。

## 7. 重启与 idle round 恢复

协议、设计空间、编译证书、trial 限额和审查记录均存入同一 campaign SQLite。
不依赖某个 Python 进程中的临时 `executor.register()` 才能恢复：

```python
from etalon.active import ActiveCampaign, CampaignStore
from etalon.active.cascade import CascadeExecutor

store = CampaignStore("runs/protocol-learning-example/campaign.sqlite")
executor = CascadeExecutor.from_journal("runs/protocol-learning-example/calculations", store)
campaign = ActiveCampaign(store, executor)
# Explicitly decide whether to continue; construction launches no computation.
```

恢复后的 executor 可以保留退役 recipe 以便历史检查，
但控制器与原子 reserve 仍会拒绝对退役或试用额度耗尽的协议派发。
恢复不会重置 trial、重写旧标签或自动重复昂贵任务。

进程中断可能留下没有 pending action 的 `running` round：例如最后一个结果已经落盘，
但 round 结束事件尚未写入。此时可显式恢复空闲轮次，保留中断审计：

```python
recovered_round_ids = store.recover_idle_rounds(
    reason="operator checked jobs; no unresolved actions remain",
)
# Returns a tuple of interrupted round IDs. It does not execute or retry actions.
```

调用前还必须确认没有 worker 正在继续派发动作；账本暂时没有 pending 不能证明 worker 已停止。
只要账本中存在 pending action，这个 API 就拒绝恢复，不能用它取消预算预留或假定作业没执行。
先核对真实作业，再用 `store.resolve(action_id, Evaluation(...))` 写入真实结局；
不要虚构成功标签或把未知成本归零来解锁协议状态变更。

## 8. 失败引导的提案不是自动修复策略

`propose(..., source_action_id=...)` 可以引用 base endpoint 的已完成、未准入运行结果，不能引用历史导入。
引用必须有记录下来的结构化失败码；每个编辑的 `allowed_failures` 必须允许其中至少一个码。
来源 action、结果/ruling hash 和编辑被固定保存；不能只凭一段错误文字推断修复原因。

这只证明提案与某个已知失败有可追溯联系。
它不自动判断应该修什么、不保证只改一个因素、不证明变体导致恢复，
也没有通过检索记忆或 reinforcement learning 学到一套 repair policy。
要研究修复收益，还需匹配失败类型的对照、固定试验预算及未见任务上的验证。

当前交付是一个受约束、可恢复的协议实验外循环。
自动图搜索、跨 action 中间产物复用、独立物理 replica、协议性能比较和跨靶点经验迁移，
仍是后续需要分别实现和验证的研究工作。
