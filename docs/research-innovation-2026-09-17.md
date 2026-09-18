# ETALON 深度调研：从执行工具到学习证据决策

调研日期：2026-09-17。本文区分已发表机制、当前工程实现、待检验研究假设。
文献状态以出版机构、会议论文集、作者论文及官方代码为依据；预印本不写成同行评审成果。
本文不是系统综述，也不构成“全球首次”的检索证明。

## 1. 核心判断

ETALON 应继续做本质性的决策层改造，但没有理由推倒已有的科学检查、协议身份和执行账本。
应研究的不是“LLM 能否调用更多 CADD 工具”，而是：

> 在预算有限、计算可能失败、不同协议有偏且组件可以重新组合的情况下，
> agent 能否学会下一步最值得购买的证据，并最终给出有合格目标端点证据支持的分子推荐？

暂定研究方向：**面向最终决策的、证据约束下的可组合 CADD 协议主动学习**。
英文工作描述：decision-focused evidence acquisition over composable CADD protocols。
这是一项需要多靶点实验支持的假设，不是已经完成的算法创新声明。

三个容易混淆的“学习”必须分开：

1. 学习分子：更新结构到性质的代理模型，选择下一个分子。
2. 学习证据获取：选择分子、测量协议和独立重复，判断何时值得确认最终结论。
3. 学习工作流：生成或修改组件图，学习有效的前置步骤、修复策略及跨任务经验。

现有工程已具备第 1 层和部分第 2 层；后续迭代也推进了第 3 层中有限、已授权编辑组合的反馈选择。
注册一个新的 cascade 后在端点之间选择，或在有限目录内学习排序，不等于已完成自主图搜索。

## 2. 从 biomedical agents 借什么，以及什么已经不新

### 2.1 BioClaw：先消歧，再评价

本次重点核实的是官网 `bioclaw.tech` 指向的
[Runchuan-BU/BioClaw](https://github.com/Runchuan-BU/BioClaw)，不是另两个同名的
`qinheming/BIoClaw` 或 `babisingh/BioClaw`；不同项目的功能不能拼在一起。
其 [BioClaw 论文](https://www.biorxiv.org/content/10.64898/2026.04.11.716807v1)
于 2026-04-14 发布在 bioRxiv；截至本次检索，核实到的是预印本。

官方论文和 [技术文档](https://bioclaw.tech/docs/BioClaw_Technical_Document.pdf)
描述群聊中的人机协作、分组持久状态、隔离容器和可发现技能；
官方 `src/index.ts` 也展示了消息、队列、会话与执行器之间的编排。
值得吸收的是长任务恢复、共享工作区和可检查的中间产物。
这些材料没有证明它在优化“分子 × 协议”的多保真预算分配；
反过来，ETALON 也不能把加聊天入口或技能库当作科学创新。

### 2.2 已发表 agent 与 ETALON 的机制关系

| 系统与正式状态 | 原始材料支持的机制 | 对 ETALON 的启发与边界 |
|---|---|---|
| [ChemCrow，Nature Machine Intelligence 2024](https://www.nature.com/articles/s42256-024-00832-8) | 专家化学工具配合语言推理、合成与设计；专家评估揭示 LLM judge 可能偏爱流畅的错误答案 | 使用真实工具输出和科学判据，不用语言自评冒充结果正确性；工具增强与执行循环不是新贡献 |
| [Coscientist，Nature 2023](https://www.nature.com/articles/s41586-023-06792-0) | 文档检索、代码、实验接口和结果驱动的反应优化 | 借鉴执行 grounding 与反馈重规划；论文仍有人工物理操作，不能夸大为无人工端到端实验室 |
| [LIDDIA，EMNLP 2025](https://aclanthology.org/2025.emnlp-main.603/) | Reasoner/Executor/Evaluator/Memory；依据结果选择生成、优化、筛选；30 个靶点的 in-silico 评价 | 反馈选择不同动作与插件 evaluator 已有先例；其正式版 AR/NR3C4 案例不等于实验命中验证 |
| [Biomni，Science 2026](https://pubmed.ncbi.nlm.nih.gov/42424436/) | action discovery、检索辅助规划、代码执行，动态组成生物医学工作流 | 2026-08-20 已正式发表，不应仍一律标成 2025 预印本；“没有固定模板”已不是独有能力 |
| [BioMedAgent，Nature Biomedical Engineering 2026](https://www.nature.com/articles/s41551-026-01634-6) | interactive exploration 学工具，memory retrieval 利用经验组合可执行流程，并做外部任务评价 | “从经验中学工具和工作流”已有同行评审先例；应借鉴探索/记忆消融与外部泛化评价 |
| [Robin，Nature 2026](https://www.nature.com/articles/s41586-026-10652-y) | 文献假设、实验数据分析、下一轮假设；多个分析轨迹形成共识 | 借鉴原始证据到后续验证的闭环；人类审核并实施实验，作者还将常见固定顺序转为 notebook 提升稳定性 |

Biomni 的 [官方实现](https://github.com/snap-stanford/Biomni/blob/main/biomni/agent/a1.py)
提供工具检索、注册和 MCP 接入；ETALON 更适合将这类能力用于发现候选组件，
随后由 MolCascade compiler 与 ETALON 检查层判定可执行性，而非让 LLM 决定科学契约是否成立。
BioMedAgent 的 [官方代码](https://github.com/BOBQWERA/BioMedAgent)
和 Robin 的 [公开实现](https://github.com/Future-House/robin) 是工程参考，不是可直接移植的 CADD 效果证明。

[STELLA 的 2025 原始预印本](https://arxiv.org/html/2507.02004v1)
已有 Manager/Dev/Critic/Tool Creation、成功流程 Template Library 和动态 Tool Ocean。
[作者提供的扩展版](https://zaixizhang.github.io/ZaixiZhang_files/STELLA_latest.pdf)
增加了多模态与实验内容；不能把不同版本的任务、数字和验证强度混写。
截至本次核实，未确认其同行评审正式版，因此这里按预印本讨论。
其意义是明确排除“critic + 记忆 + 自动扩展工具 = ETALON 首创”这种说法。

### 2.3 通用 agent 也已经在优化工作流

[AFlow，ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/5492ecbce4439401798dcd2c90be94cd-Paper-Conference.pdf)
用代码表示工作流，结合执行反馈、树形经验与 MCTS 搜索组合。
[ADAS，ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/36b7acf6f6010652b3f2a433774a66fe-Abstract-Conference.html)
通过 meta-agent 编写候选 agent，再利用历史设计档案进行迭代搜索。
可吸收的是“提案 → 独立验证 → 归档 → 晋升”，不是让在线 agent 任意改生产代码。
它们在问答或代码任务上的收益，不能直接推断为更好的亲和力、有效计算或实验命中率。

## 3. 更接近创新核心的先例：不能只比较 LLM agents

### 3.1 多保真药物发现已达到真实实验闭环

[MF-LAL，ICML 2025](https://proceedings.mlr.press/v267/eckmann25a.html)
已经将多保真主动学习与潜空间分子生成结合；“AL + 多成本 oracle + 生成模型”不是 ETALON 新点。
[REINVENT + ESMACS，JCTC 2024](https://pubs.acs.org/doi/10.1021/acs.jctc.4c00576)
已有生成式主动学习与 ensemble 物理评分反馈；单轨迹统计不能冒充独立重复精度。

更直接的对照是 [McDonald 等，ACS Central Science 2025](https://pubs.acs.org/doi/10.1021/acscentsci.4c01991)：
将 docking、单点抑制和 IC50 等不同实验保真层次纳入 BO，考虑成本并进行自动化实验发现。
因此，不能声称首次打破固定实验漏斗，或首次让算法选择分子和实验层次。
真正要追加检验的是化学状态/协议证据契约、失败与修复、组件依赖及终端确认这些约束的作用。

[AdaptiveFlow，Nature Biotechnology 2026](https://www.nature.com/articles/s41587-026-03217-x)
于 2026-09-01 正式在线发表：自适应化学子空间筛选、可选 AL、多种 docking 协议及真实命中验证。
这不是与 ETALON 完全相同的 LLM agent，但比“聊天功能更多”更值得作为 CADD 效果参照。
其 [出版记录及摘要](https://pubmed.ncbi.nlm.nih.gov/42680826/) 支持上述定位。

### 3.2 可靠性、全局信息价值和组件调度也有数学先例

| 已发表方法 | 已覆盖的机制 | ETALON 不能声称什么 |
|---|---|---|
| [misoKG，NeurIPS 2017](https://proceedings.neurips.cc/paper_files/paper/2017/file/df1f1d20ee86704251795841e6a9405a-Paper.pdf) | 有偏、有噪多信息源和成本感知 knowledge gradient | 不能把全候选决策 KG/cost 当新 acquisition |
| [rMFBO，AISTATS 2023](https://proceedings.mlr.press/v206/mikkola23a.html) | 处理不可靠低保真源，给出相对单保真方案的保护性质 | 不能声称首次防止低保真负迁移；现有 ETALON 启发式没有继承该理论保证 |
| [iMFBO，UAI 2024](https://proceedings.mlr.press/v244/fan24a.html) | fidelity 随输入变化，而非每个信息源一个全局质量等级 | 分子条件化 reliability 不是首创；准入概率和代理误差还不是同一个量 |
| [p-KGFN，ICML 2024](https://proceedings.mlr.press/v235/buathong24a.html) | 函数网络的部分评估，成本感知地选择节点与输入 | 组件单独运行、图中选节点、跳过完整流程本身均已有先例 |
| [Fast p-KGFN，AutoML 2025](https://proceedings.mlr.press/v293/buathong25a.html) | 降低部分网络 KG 的 acquisition 计算开销 | 不能只报告 oracle 省钱而忽略规划器本身的巨大耗时 |
| [Budgeted Multi-Step BO，NeurIPS 2021](https://proceedings.neurips.cc/paper/2021/hash/a8ecbabae151abacba7dbde04f761c37-Abstract.html) | 未知异质成本、总预算约束和非短视前瞻；EI/cost 可严重次优 | 预留一次确认报价是工程启发式，不是解决了有限预算最优决策 |

这里尤其要区分：iMFBO 的输入依赖保真度，针对的是信息源与目标之间的关系；
ETALON 当前新增的局部准入概率，针对的是计算结果能否通过明确质量规则。
“数值合法但对目标无帮助”和“运行结果根本不能作证据”应由不同模型处理。

由这些先例推导的结论是：合理贡献不能仅是将现成 KG、准入概率和预算保护相乘。
若未来仅在单一 toy 上获胜，应将其视作机制演示，而非原创算法或普遍药物发现优势。

## 4. 深入当前代码：优势、瓶颈与本轮改造边界

### 4.1 应保留的研究底座

| 代码位置 | 已有价值 | 必须保留的约束 |
|---|---|---|
| `active/schema.py`、`active/store.py` | 端点身份、原始结果、准入、预算预留、事务与恢复 | 失败也入账；不能把旧 protocol 的标签偷偷解释为新 protocol |
| `campaign/design.py`、`active/cascade.py` | 从 MolCascade 组件构建单组件或自定义 cascade，并冻结配置与资源 | default funnel 不是必经路径；重组仍须满足输入输出契约 |
| `active/model.py` | 只用已取得的准入标签，跨端点相关性从配对数据学习 | 同单位不等于同一 observable；当前任务相关性仍是全局估计 |
| `active/runner.py` | 选择、授权、实际执行、归档、下一轮训练 | 普通异常和进程中断不同处理，不自动重复昂贵工作 |
| `active/replay.py`、`active/benchmark.py` | 隐藏 oracle、固定来源、相同成本预算与 QC 的回放 | 查询前不能访问答案；换工具或多读标签不是 agent 的算法收益 |

已有 `cost_aware` 把 EI、同分子上的 objective 方差下降、端点全局准入率与成本组合。
它可能高估“便宜但不会改变最终选择”的测量；极大的 bootstrap/calibration 优先级也不表示真实收益。
这比单纯缺少一个更强 LLM 更接近当前的本质问题。

### 4.2 本轮实现目标与清楚的非目标

本轮增加 `mf_kg` 作为可解释的全候选 KG 基线，`decision_aware` 作为证据/预算约束策略。
实现涉及 `active/knowledge.py`、`active/reliability.py`、`active/decision.py` 与推荐输出：

- 对一个候选查询，计算其对整个终端决策池的后验均值排序价值，而不是只看该分子的方差。
- 从已取得的准入/不准入结局，用特征核加权估计分子条件化准入概率；保留 global/none 消融。
- 将未知协议的配对校准花费限制在显式总预算份额内，不伪造极大的 KG。
- 对低保真查询保护最终 objective 确认的报价；接近末轮时购买目标端点证据。
- 输出 provisional、attainable 与 evidence-backed 三种推荐，区分预测、可确认和已有证据。
- 新策略每 round 只派一个 action，取得真实反馈后再决策，不声称做了联合 batch fantasy。

这里的“exact KG”仅指有限决策集、固定 Gaussian 模型参数、单次观测的积分，
数值计算仍有浮点误差；不包括对超参数再拟合、失败机制或未来工作流变化进行精确积分。
kernel Beta 伪计数是工作估计，不是已校准的分类器；blocked 前置检查不作为化学失败样本。
成本保护依据报价，不保证实际不超支，也不保证确认会成功；默认精确 KG 只面向小规模 pilot。

本轮没有实现自动 DAG 搜索、组件前缀复用、主动修复、跨任务技能记忆或生成式分子设计。
注册协议仍被学习器视为黑盒端点；没有在声明“整个 CADD loop 已经被学会”。
新策略的真实测试与 benchmark 数字应以单独验证记录为准，本文不预填运行结果。

## 5. 科学问题的形式化：优化结论，而非最低代理分数

### 5.1 状态、动作和证据身份

令 `D_t` 为已发生的全部结果，包括原始数值、状态、检查、成本与来源；
`D_t^+` 为其中通过准入的科学标签，`B_t` 为扣除已花费与预留后的剩余预算。
策略不能读尚未查询的 oracle 值，失败的数值不能混入目标回归。
但失败结局可以更新运行/准入模型；这与“失败数据全部扔掉”不同。

当前动作可写作 `a = (molecule_state_id, endpoint_id, replicate)`。
研究目标中的扩展动作是 `a = (state, protocol_subgraph, context, replicate_or_repair)`。
后者还须有合法的前置 artifact 和显式授权，不能因算法认为“信息价值高”而绕过检查。

证据身份至少包括：靶点和受体状态、分子化学状态、observable、单位、协议配置、输入资源、
软件/模型版本、随机种子或独立重复标识，以及上下游 artifact 来源。
`CascadeRecipe` 已固定配置、readout、MolCascade commit 和绑定文件；
完整环境版本、共享 artifact 依赖与独立模拟 replica 仍需进一步完善。
尤其不能将 docking score、MM/PBSA、pIC50 当同一种标签，RBFE 的边观测应另建图模型。

### 5.2 三类终端推荐和效用

`provisional`：在当前模型下目标效用后验均值最高的候选，允许尚无目标端点合格观测。
`attainable`：已有合格目标证据，或当前仍可执行并支付目标确认的候选中，后验均值最高者。
`evidence-backed`：只在已有合格 objective 证据的候选中推荐，并列出支持结果及协议身份。
后者仍不是已证实的临床有效性；若 objective 是 docking，它只表示有合格 docking 证据。
若不存在合格目标证据，应明确没有 evidence-backed 推荐，而不是悄悄退回预测值。

研究目标可表示为：在累计成本约束下最大化最终推荐集合 `S(D_T)` 的期望效用，
其中 `S` 的可行性由所需证据、化学约束和可验证终点定义；`T` 由预算及停止条件决定。
真实实验可用确认命中数或受约束的实验性质；回放可用事后隐藏目标上的 regret/recall。
不能将一个更低的代理分数当成所有这些终端效用的通用替身。

### 5.3 当前 KG 近似与尚未解决的部分

对最大化方向，设目标后验均值为 `mu_t(x)`，候选查询为 `a`，则
`KG_t(a) = E[max_x mu_{t+1}(x) | D_t, a] - max_x mu_t(x)`。
最小化通过效用符号变换处理。固定 Gaussian 后验下，单次结果更新可写为
`mu_{t+1}(x) = mu_t(x) + b_t(x,a) Z`，其中 `Z ~ N(0,1)`，
`b_t` 由目标—查询的后验协方差除以查询的预测观测标准差给出。
实现通过有限个仿射函数的上包络积分，计算全池决策价值。

`mf_kg` 使用 KG/报价；`decision_aware` 增加准入概率折扣和显式校准/确认规则。
这不是对完整预算约束终端效用的 Bellman 最优解。
`decision_aware` 将当前决策池限制为已有证据或当前可确认的分子；`mf_kg` 基线保留全池。
这仍不是对每一种未来证据准入状态下可确认集合的精确积分。
最终确认 guard 是对这一差距的可测试补丁，不能称为完整非短视决策规划。

`p(admit) × KG` 近似地将失败计为零目标信息，没有计入失败对准入模型及可行决策集合的学习价值，
也未联合积分成功与性质值的相关性。因此 exact KG kernel 不会让整个控制器自动成为 Bayes-optimal。

还有三个重要限制：失败不一定与潜在性质独立；自适应采样可能造成局部准入估计偏差；
所有候选的一样大的共同后验平移不改变排序，因此有不确定性并不一定有 KG。
质量准入模型不等于目标模型误差校准，也不提供 conformal coverage 保证。

## 6. 三阶段路线：每阶段都有可以否证的主张

### 阶段 A：建立真实决策对照，本轮推进

问题：全池决策价值和明确的最终确认约束，能否减少“购买很多廉价值却没有最终有效推荐”？
交付：全局 KG、局部/全局/无准入折扣、校准限额、确认报价 guard、分层推荐及逐 action 反馈。
机制测试覆盖公共平移零价值、方向反转、零相关协议、重复数据、失败隔离、预算末轮与重启。
效果测试必须同时统计 provisional 和 evidence-backed 推荐，不能只选更好看的那一个。
否证条件：等信息、等预算下相对 `mf_kg` 无稳定增益，或收益只来自更大的高保真花费。

### 阶段 B：让组件图成为真正的学习对象，控制与证据底座已实现

问题：共享中间证据、合法部分评估和有约束修复，是否改善相同预算下的最终选择？
交付：显式 artifact DAG、阶段成本、输入契约、共享计算去重、失败类型和允许的 repair operators。
上层设计器只提出有限、可审查的图修改；编译器拒绝不满足契约的图；旧证据不重命名。
候选图需要离线验证与 promotion 记录，不能让即时聊天判断直接晋升生产协议。
对照包括固定 cascade、黑盒端点选择、p-KGFN 类部分评估、仅缓存，以及相同修复动作的规则策略。
否证条件：收益仅来自缓存/更好工具，计入图搜索成本后消失，或新协议标签无法严格追溯。

本次后续迭代已实现：组件输入绑定和 artifact 依赖记录、精确编辑白名单、结构化 runtime 失败来源、
可恢复的提案与编译证书、共享 campaign 预算内的试用限额、实际动作证据准入、预先固定门槛的显式晋升/退役。
案例使用真实 CPU 组件检验生命周期，未用 MW 示例冒充亲和力收益。
详见[协议学习](protocol-learning.md)与[验证记录](validation-protocol-2026-09-17.md)。
后续又加入[有限编辑组合的学习型选择器](protocol-search.md)：相同审计面板上的留一预测帮助和失败惩罚，
驱动共享线性模型选择下一个预授权组合；配方目录、报价、评分版本和授权额度固定，观测后不能偷偷换口径。
真实 CPU 多轮与独立合成消融见[搜索验证记录](validation-search-2026-09-17.md)。
再一轮加入[协议审计经济停止](protocol-stopping.md)：操作者冻结 panel skill 与成本单位的换算，
按完整面板的有界预测 EI 减机会成本决定是否继续；没有自动推广协议、自动截断面板或改写历史 journal。
这补足了“预算未耗尽也可以不继续改协议”的决策分支，但仍是面板选择基线，
没有把外层收益直接等同于最终候选分子的科学价值。
这仍不是完整阶段 B：尚无任意图生成、部分节点 acquisition、共享前缀复用、逐阶段实测成本，
也没有证明修复动作的因果收益、跨靶点迁移或最终 CADD 决策增益。有限空间内的学习不等于自主科研。

### 阶段 C：跨 campaign 自我改进与真实 CADD 验证，尚未实现

问题：协议/失败经验能否迁移到未见靶点与 scaffold，而不是记住一个已反复调参的测试集？
交付：冻结训练/验证/测试 campaign，协议经验的适用上下文及版本，真实资源计量，独立重复验证。
将 LLM 记忆用于提出和解释候选动作；实际预算、证据准入和决策收益由受控模块判定。
跨任务比较冻结策略、只迁移分子模型、只迁移协议经验、完整系统；记录训练和检索费用。
否证条件：跨靶点负迁移、收益依赖测试集泄漏、需要人工挑选成功案例，或实验验证无法复现。

## 7. 公平实验：避免把工具优势误算成 agent 优势

基础对照至少包含 objective-only random/greedy/UCB、历史 `cost_aware`、标准 `mf_kg`，
以及条件允许时的 rMFBO/iMFBO/MF-LAL 或 p-KGFN；接口/任务不兼容时解释适配范围，不冒称复现。
所有方法使用同一候选池、同一 oracle、同一初始标签、同一强制 QC 和同一失败成本处理。
生成方法额外得到的候选和预训练信息必须记账；不同信息权限的结果不能直接说明方法更强。

核心消融应逐项移除：局部准入模型、确认 guard、校准预算、组件重组、修复、跨任务记忆。
校准与初始化开销纳入同一总预算；比较成本模型时报告实际与报价偏差。
除 oracle 花费外还单独报告 GP 拟合、acquisition、图搜索、LLM、CPU/GPU 时间和峰值内存。
失败尝试、未完成作业及超支不得从分母删除；批量吞吐也不能与串行策略的响应速度混写。

预先冻结多个预算档、随机种子、靶点和 scaffold 切分；报告配对差值与不确定性，而非只列平均最佳分数。
真实终点未测满时不能计算“全库真实最优 regret”；应使用实验确认率等可识别指标，并说明缺失范围。
终端实验读出不是用于调参的同一个代理分数，昂贵模拟也不自动等于实验真值。

## 8. 保留负结果与当前结论

此前 `runs/active-validation/benchmark.json` 中的五 seed synthetic 回放已经得到负结果：
平均 simple regret 为 random 0.4506、greedy/UCB 0.3230、cost_only 0.3300、cost_aware 0.4932；
每种方法平均花费均为 120 个 oracle 单位。此处只复述历史验证记录，不代表新策略结果。
旧 [验证记录](validation-2026-09-17.md) 及运行产物必须保留，不能被新一轮结果覆盖。

它说明旧质量/成本启发式没有在该测试中带来稳定收益；
不说明质量检查无用，也不说明所有多保真学习无效。
当前 synthetic 特征不是从其 SMILES 得到，不能据此作任何 CADD 疗效或命中声明。

本轮合理结论是：ETALON 正从可审计执行底座推进到可检验的证据决策控制器。
方法层吸收 published 优势，但“发表级创新”仍取决于新问题约束是否必要、
模块贡献能否被公平消融识别、以及真实多靶点闭环能否稳定改善最终科学决策。

本轮实现后的 [独立验证记录](validation-decision-2026-09-17.md) 已补充 459 项回归和八组消融。
新控制器在原 synthetic-v1 上与 UCB/MF-KG 的最终推荐 regret 持平；
条件准入和确认 guard 的独立收益尚未得到证明，不能将工程升级直接写成科学创新成立。

## 9. 下一步真实数据入口：已核实来源，尚未完成导入审查

McDonald 等公开了 [Zenodo 13983042](https://zenodo.org/records/13983042)，
[完整 metadata API](https://zenodo.org/api/records/13983042) 标注开放访问、CC-BY-4.0。
本轮只核实目录并在内存读取三个小包，未执行下载源码，也未将其接入 ETALON 的 oracle。
完整目录有 15 个文件，包括六个小靶点包、约 492 KB 的 `MF-BO_training_data.zip`、
约 18 KB 的算法包，以及约 1.386 GB 的 HPLC 文件；后者未下载。

训练包实际含 AChE、CXCR4、Factor-D、HIF-PH、NR1A2、PARP1 和 HDAC 两轮共八个 CSV，
有 SMILES、目标值、中间代理和 docking 列。训练包 metadata MD5 为 `a466cd507d58d5156b75bf90d1c36882`。
这些是可追溯入口，但**不意味着每行已经满足 ETALON 的端点契约**：

- 回顾性研究的 single-point 是根据 IC50/Hill 方程模拟，不是第三套独立湿实验测量；
  ChEMBL IC50 与 DiffDock 计算结果也必须分别声明来源。
- 部分目标列名为 `log10_IC50`，示例值却约在 0–1，代码使用 `MAX`；应先查清归一化/反转链，
  不能依据列名直接赋予 nM/pIC50 单位和最小化方向。这不构成原研究出错的证据。
- `DiffDock score` 的具体读出定义尚未核实，不能擅自标成 kcal/mol。
- CSV 表头不足以证明 assay 协议一致，缺失/重复/化学状态对齐未审查；
  已查表头没有完整失败、QC 和真实成本列，不能直接支持“失败感知策略有效”的实验结论。

下一阶段先完成来源、变换与行级协议映射，再冻结训练/测试靶点和 scaffold 切分；
没有这一步，给当前 agent 接入一张表并不等于建立了可信 CADD benchmark。
