# 薄 harness：契约与并行实施入口

日期：2026-10-21。状态：**第一阶段本地工程已交付；本轮已执行原生 Codex、隔离 Linux 进程与依赖回归验收，并修复实际发现的费用结算问题。分项证据与独立判定见 DELIVERY_I/V；Claude 原生调用认证阻塞，科学独立收益未运行，R4b 仍暂停。**

ETALON 是宿主 agent 的科学能力扩展。宿主拥有问题选择、下一步行动、注意力分配以及 skill 的选择、组合、修改和停止；ETALON 提供 MCP、科学 SDK、可靠执行、成本与证据内核。SDK 首先是稳定调用口，不要求把现有代码搬进新的目录层次。

当前 I2 全量库接续以本文末尾“全量能力族覆盖”及各 DELIVERY 最新追加节为准。旧表的“仍为六项”和待补项描述对应当时固定来源；不删除其历史验收边界。主注册现在同时纳入 `.claude/skills/` 与 `asset/molquarry/skills/` 原内容。

## 阅读入口

2026-10-21 新接续：专项约定、元 skill 自身改进和跨 chat 证据复用进入实施；当前方案/验收/文件唯一负责人见 [EVOLUTION_CONTRACT](EVOLUTION_CONTRACT.md)、[EVOLUTION_ACCEPTANCE](EVOLUTION_ACCEPTANCE.md)、[EVOLUTION_WORKSTREAMS](EVOLUTION_WORKSTREAMS.md)。这是已有动作/结果/方法契约的增量，不是已通过报告或新运行框架。旧 I2/I3/E2 的正负结论继续保留。

| 文档 | 唯一负责的内容 |
| --- | --- |
| [ACTION_CONTRACT.md](ACTION_CONTRACT.md) | 当前动作、执行绑定、幂等、资源与恢复边界 |
| [RESULT_CONTRACT.md](RESULT_CONTRACT.md) | 执行、验证、准入、费用、事件与 hook 边界 |
| [METHOD_CONTRACT.md](METHOD_CONTRACT.md) | 方法版本、使用记录、评价及 S／φ／G 改进 |
| [WORKSTREAMS.md](WORKSTREAMS.md) | 文件唯一归属、接口依赖、交付物、可复制的新 chat 任务说明 |
| [ACCEPTANCE.md](ACCEPTANCE.md) | 有区分力的验收用例和证据要求 |

新 chat 的本机交接入口为 [integration/ETALON_THIN_HARNESS_HANDOFF.md](../../../integration/ETALON_THIN_HARNESS_HANDOFF.md)。本目录是可进入产品版本控制的契约文档；本机路径、chat 启动与交接上下文留在 integration。不要把这组长文档全文注入每轮运行时提示词。

仓库默认忽略本地研究 docs；本次仅通过 `.gitignore` 放行 `docs/thin-harness/`，其他研究笔记保持原忽略范围。本文档尚未提交或推送。

单独 clone ETALON 时，仓库外 integration 链接可能不可用；本目录的契约、任务说明和验收定义自包含。integration 是交接上下文与历史来源，不是产品调用或实现的运行依赖。

## 本次核对的实现来源

| 来源 | 核对值 | 解释 |
| --- | --- | --- |
| 上游 main | `0af4297fb7de435d858d54195561883a21526d5e` | 接口补足及预算、终态、校准、池／账本、错误分类等修复 |
| 薄分支 `codex/refactor-thin-harness` | `d3889e6c0600605f018fb81ce3df1b420d6dcbf9` | 宿主能力发现、内容版本、可选 skills、导出与校验 |
| 本地 ETALON HEAD | `5927aceb346475a6f97550f561e7eb3b291611da` | 不包含全部本地能力，必须同时保留 dirty 和 untracked 源码 |
| 本地增量 | `research/`、`active/prism.py`、`boundary/prism_v1.py`、`data/design.py`、相关执行／测试文件 | 两个远端分支均不能单独代表这些科学实现 |

以上是查阅基线，不是已合并版本、运行源码快照或部署证明。实施前重新核对实际工作树和负责人。单独 clone 本地 HEAD 不足以恢复基线。

旧的 [2026-10-19 设计交接包](../../../integration/etalon-thin-harness-design-20261019/README.md) 保留为历史设计依据；其哈希清单不修改。本组文档替代其中与本次冲突的阶段顺序、接口草图和实施分工；不改变旧科学协议、历史证据和暂停状态。

## 不允许在实现中丢失的约束

- **F01 宿主主导：** 无需完整研究 DAG，也无需 ETALON 内部 LLM 再做通用规划或批准。
- **F02 可不用 skill：** 合法动作允许 `method_use_refs=[]`；查询、阅读和普通宿主操作无需全部包装成 ETALON 动作。
- **F03 自由组合：** 可只用部分 skill，可组合多个，可按新证据追加动作、改变问题或停止。
- **F04 开放方法空间：** skill 目录是发现入口，不是允许研究的白名单。允许本地新建、修改和试用候选方法。
- **F05 版本可追溯：** 已提交执行、方法内容与历史证据不因后续修改而改变；后续动作可选新版本。
- **F06 执行事实可靠：** 身份、预算、准入、原始失败和未知结果由可核验实现约束，skill 文字不能绕过。
- **F07 改进可归因：** 区分知识／模型 M、技能 S、使用方法 φ、改进方法 G、宿主及基础模型条件。版本变化不等于收益。
- **F08 工程保持小：** 复用 CampaignStore、RuntimeStore、ResearchJournal、操作注册和产物机制；不新增通用 agent 主循环、消息总线、路由器、workflow DSL 或第二科学账本。
- **F09 探索不等于晋升：** 未证实优越的方法可以在已有授权范围内试用；宣称优越、调整正式默认方法需要相应证据和可追溯记录，不增加常规人工审批。
- **F10 并行有边界：** 开发按文件唯一负责人并行；科学运行的并发能力以真实实现与测试为准，不能因多 chat 开发就放松预算、重复或恢复约束。

## 兼容与变更约定

1. `/1` 是接口语义版本，不是大版本实施阶段。接口实现与已运行范围见各 DELIVERY；契约定义本身不是通过证明。
2. 旧 `Action`、`Evaluation`、`active.run`、workflow、PRISM wire contract 保留含义；新接口通过适配复用。
3. 仅实际实现并验证了执行、验证、成本和恢复的操作才可被 advertised 为可执行。未知操作返回明确不支持；未知方法不能仅因未在目录中就被禁止探索。
4. 字段、签名和错误语义的跨线变更由集成负责人协调，审查方检查是否违背 F01—F10。常规局部实现不需要审查方逐次批准。
5. 不要求新的框架、数据库、守护进程、目录搬迁或依赖升级。必要的小文件可新增，但须在工作线归属中登记。
6. 用户已明确授权本次本地工程实现、并行和必要工程测试；不恢复 R4b、部署共享环境、执行新增付费科学任务或发送 CRO 订单。

## 本次文件角色

本次 **I** 接管公共契约与 WORKSTREAMS，协调共享集成；A/B/C 已实际承担各自实现，V 独立设计反例并核验最终组合。R 是历史讨论角色，不是本次审批或回传依赖。角色仅表示工程文件责任。

实际交付入口：[I 集成与最终包](DELIVERY_I.md)、[V 独立验收及未运行边界](DELIVERY_V.md)、[A 执行](DELIVERY_A.md)、[B 方法与科学 SDK](DELIVERY_B.md)、[C 宿主与 bundle](DELIVERY_C.md)。工作树未提交；通过数字仅限报告中的工程范围。

<a id="function-migration"></a>

## 原功能到内核、SDK、skill 的对应与完成程度

2026-10-21 根据用户要求补充。本文是功能去向与工程待办的索引，沿用三个契约、[文件归属](WORKSTREAMS.md) 和 [验收 ID](ACCEPTANCE.md)，不增加运行框架、路由表或新的验收体系。**表中各行不是科研步骤，不要求宿主按行、按顺序或全部执行；也不要求一行新建一个 skill。** 本次补表由原讨论 chat 编辑本 README，交付后仍由 I 唯一维护；A/B/C/V 按既有文件归属推进。

这里分别判断代码结构、方法内容和实际验证，不能用一个“完成”互相替代：

- **文档迁移**：缩短 SKILL 入口、把原说明放入 references；不据此认定代码中的选择与执行已经分开。
- **原能力复用**：原算法、函数或工具已可调用，本次保留或吸收上游修复；不将已有能力包装成新完成的解耦。
- **已解耦（限定行为）**：代码允许宿主独立选择/调用/停止，并有下表所指的工程用例。只对已检查的边界成立，不能外推为全部流程、所有宿主或科学有效性。
- **未完成/待证**：仍缺具体操作接缝、可组合的方法内容或相应行为证据；应明确缺哪一项，不要求为“变薄”重写正确算法。

证据入口：[A](DELIVERY_A.md)、[B](DELIVERY_B.md)、[C](DELIVERY_C.md)、[I](DELIVERY_I.md)、[V](DELIVERY_V.md)。第一阶段的 158 项包含 V 的 16 项，不能相加。本次补表时 V 已独立确认所观测 Codex 开放使用行为、隔离 Linux 可靠性案例及新组合包通过，Claude 实际调用为 401 blocked；不同运行绑定不同源码/环境，按 V 对应记录限定结论，不能用一个宿主结果替代另一个，也不能把原生调用等同于 S/φ/G 收益。

| 编号／原功能 | 留在内核或可调用 SDK 的内容 | skill 与宿主应承担什么 | 保留的兼容入口 | 当前完成程度 | 验收与未完成边界 |
| --- | --- | --- | --- | --- | --- |
| FM-01 数据获取、候选/测定导入、PRISM design bundle 消费 | [data/service.py](../../src/etalon/data/service.py)、[ingress.py](../../src/etalon/data/ingress.py)、[design.py](../../src/etalon/data/design.py)：获取预算、封存来源、对象/角色/哈希校验、`read_bundle/import_bundle/verify_import`；独立 PRISM 的生产端不搬进 ETALON | 选择来源、查询和纳入条件，判断缺什么证据；可复用 MolQuarry 方法或直接用工具；不把 Astra 假设当实验标签 | [mcp/data.py](../../src/etalon/mcp/data.py) 的 `etalon_data_*`、既有 design Python 入口与 wire schema | **原能力复用**；未证明完整数据工作流已转成可组合 skill，未新增完整 Astra 标注闭环 | RES-08、ACT-07、INT-01；`test_design_import.py`、`test_prism_v1.py` 与原数据测试按改动选测。用同一 bundle 对照身份/角色/费用；额外步骤不能强制经过 campaign skill |
| FM-02 昂贵计算前检查、授权、结果准入与费用 | [active/runner.py](../../src/etalon/active/runner.py)、[active/store.py](../../src/etalon/active/store.py)、[boundary/prism_v1.py](../../src/etalon/boundary/prism_v1.py)、`authority/`、`learn/admissible.py`：准备体系/协议身份、预算、收据、准入和未知状态的确定性约束 | [campaign skill](../../.claude/skills/etalon-campaign/SKILL.md) 指导是否值得计算、如何解释证据或停止；不能用自然语言跳过真实身份/预算约束 | `etalon_check_handoff/authorize_spend/rule_admissible`、既有 executor 与 PRISM 消费协议 | **原能力复用＋说明层文档迁移**；执行路径由 FM-03 复用，并非把安全/科学校验搬进 skill | RES-01/02/04/05/06/08、ACT-07/08；同一动作的新旧入口需保持身份、收费、准入一致。真实 PRISM 新计算未由本轮工程验证覆盖 |
| FM-03 数值策略选择与执行绑定在同一 active 循环 | [runtime/api.py](../../src/etalon/runtime/api.py) 的 `prepare_action/submit_action/read_action_result`；`ActiveCampaign.evaluate` 经同一 `_execute_reserved` 路径执行宿主已选 candidate/endpoint，保留预留/收据/准入 | 宿主自行选动作；skill 可以不选、部分采用、组合或修改；下一动作可根据真实结果临时提出 | `active.run`、`run_round`、`active_execution_plan/active_submit` 及旧 active MCP 保留；旧数值策略成为可选方法 | **已解耦：动作选择与可靠执行**；不限于把旧提示词变短，不要求为宿主伪造 GP/完整 DAG | ACT-01—04/08/09、INT-03；[test_runtime_actions.py](../../tests/test_runtime_actions.py)、[test_thin_harness_integration.py](../../tests/test_thin_harness_integration.py)。C 的 Codex/Linux 两个 MW 动作证明限定 CPU 路径可动态续接，不能据此声称药效/所有旧策略等价 |
| FM-04 durable workflow、长任务、预算竞争、恢复与取消 | [runtime/service.py](../../src/etalon/runtime/service.py)、`store.py/operations.py/executors.py`：任务状态机、OS 锁、预算事务、进程身份、持久收据和 reconcile；单动作内部可复用旧单节点 spec | 宿主决定研究下一步和是否恢复；固定局部操作可以继续复用，ETALON worker 不成为第二个研究规划器 | 旧 `workflow.plan/submit/status/cancel/reconcile`、CLI 和原恢复记录 | **已解耦：开放动作与既有执行后端**；执行状态机有意保留，不以删除它衡量“薄” | ACT-05/06、RES-02/05/07、EVT-03；[test_runtime_posix_acceptance.py](../../tests/test_runtime_posix_acceptance.py) 与恢复审计。A 接续报告包含真实 Linux 竞争/杀进程后恢复/取消，按其固定源码及 V 判定限定；Windows detached worker 仍不支持 |
| FM-05 批量筛选、sweep 账本和批次操作 | [screening.py](../../src/etalon/screening.py)、[campaign/sweep.py](../../src/etalon/campaign/sweep.py)：`plan_screen/submit_screen/screen_status`、claim/emit/record/admit/recover、零命中与费用 | [screening-sweep skill](../../.claude/skills/etalon-screening-sweep/SKILL.md) 帮助选择批次、方法、未来协议和停止条件；不强制“先生成再筛选”或完成全部 tiers | `etalon_screen_*`、`etalon_sweep_*` 及既有批次/ledger 格式 | **原操作复用＋限定接缝修复**：真实 CPU 已有批次可直接筛选、零命中后停止、只改变未来方法；补足 skill 输入/输出/停止说明。不外推全部批次策略已解耦 | RES-03、ACT-03/05/08、HOST-01；`test_screening_service.py`、`test_sweep.py`。本轮 [功能检查](../../tests/test_function_capabilities.py) 保留原 FAILED 收据并返回 exhausted，后续不同 revision 不改旧产物；CPU 费用未计量，不能补零 |
| FM-06 门控阈值、控制面板和校准 | [campaign/calibrate.py](../../src/etalon/campaign/calibrate.py)：`calibrate/separation/enrichment`、方向/NaN/tie 及分子计数语义；revision-bound gate 授权仍由确定性实现核验 | [gate-calibration skill](../../.claude/skills/etalon-gate-calibration/SKILL.md) 选择相关面板、解释保留率与排序、决定修订/停用；没有 gate 的任务无需调用 | `etalon_calibrate_gates/etalon_authorize_gates` 及旧校准结果结构 | **原校准 SDK 复用＋具体方法说明＋只读适配**；算法原文件未改。已有工程面板支持停用 gate，未证明新的自适应方法或收益 | RES-08、MET-04、LEARN-03；[test_research_methods.py](../../tests/test_research_methods.py) 中方向/常量排序/多 pose 反例。[独立函数反例](../../tests/test_research_function_methods.py) 检查丢失 active 后停止、未知值、revision 拒绝；[V 反例](../../tests/test_thin_harness_functions.py) 另用真实 CPU 零命中产物校准。未完成阶段 recall 保持 None |
| FM-07 生成器规划、产率测量、资源分配与 campaign 建议 | [campaign/generation.py](../../src/etalon/campaign/generation.py)、[pipeline.py](../../src/etalon/campaign/pipeline.py)、[propose.py](../../src/etalon/campaign/propose.py)：实际产率/去重/成本测量和可选数值建议；原生成执行器保留 | [generation-planning skill](../../.claude/skills/etalon-generation-planning/SKILL.md) 选择生成器、已有分子或其他路线，按反馈调整/停止；建议不强制转为后续全流程 | `etalon_generation_productivity`、`etalon_plan_campaign/etalon_campaign_plan`、原 generation/campaign API | **独立建议 API 复用＋具体方法说明＋冷依赖接缝修复**；Wilson 同公式使用标准库分位数，合法输入与旧数值对照一致，坏 confidence 明确拒绝。现有产率函数可单独调用，已有库计划可跳过生成。旧 Campaign/Supervisor 仍可选；接续实测独立 judge、非空 propose、单阶段 Pipeline 和不 tick 的状态读取，未宣称所有策略路径均已验证 | ACT-02/08、MET-04、HOST-01；`test_campaign.py`、`test_sweep.py`。A 的功能检查覆盖跳过生成、只选一个生成器、根据三段测量停用枯竭路线；未启动生成或昂贵计算。本轮正式 Codex 直接产率工具复测返回后停止，无 skill/plan；首次 120 秒冷导入超时保留，详见 C/V |
| FM-08 长任务监控、进度解释与研究调度 | 既有 workflow/sweep status、heartbeat、artifact、PID/receipt 查询；[campaign/supervisor.py](../../src/etalon/campaign/supervisor.py) 的局部执行管理继续可选复用 | [campaign-monitoring skill](../../.claude/skills/etalon-campaign-monitoring/SKILL.md) 判断运行/停滞/未知；宿主在有需求时选择调度、恢复或停止，不新增 ETALON 后台研究循环 | `etalon_sweep_status/progress/recover`、workflow status/reconcile 与旧监控记录 | **旧查询复用＋只读接缝修复＋具体方法说明**：状态读取不构造执行器、不迁移旧库，缺路径/坏收据拒绝。FM-04 恢复能力与宿主判断仍分开 | RES-02/07、ACT-06、HOST-01；本轮 A/I/V 检查旧池/live WAL/缺路径/损坏状态与只读产物；unknown 工程账本 reserved=2、spent=0.25 不变。该 fixture 没有活动 worker，真实取消仍引用前轮 POSIX 证据 |
| FM-09 科学知识 M、数值策略、诊断、实验比较及历史 R3/R4/R4b | [research/](../../src/etalon/research/README.md) 的 journal、rows、transfer、diagnostics、validation、feedback 与既有冻结/释放；如 `fit_policy/next_action/evaluate_policies` 保留科学含义 | [campaign factorial reference](../../.claude/skills/etalon-campaign/references/factorial-diagnostic.md) 对齐 `ResearchJournal.factorial`；[finite-diagnostics](../../.claude/skills/etalon-campaign/references/finite-diagnostics.md) 对齐 `bind_outcome/condition/exact_program`，可单独或部分组合，不把历史 schedule 或轮次升为必经路线 | 既有 research Python 入口、`examples/*round4*.py`、原 journal/Artifact/TaskSplit 和历史协议 | **科学实现保留＋多个有用方法对齐真实 SDK**；factorial、离散绑定/支持检查/有限预算建议及配对经验模型有工程反例；记录后编译/反馈接缝已修复，仍为六个注册 skill。其余 research 方法未全面迁移；不是 M/φ 改善或 RSI | RES-08、LEARN-01—03；原 research 回归及数据暴露反例。本轮 B 的真实持久 Journal 反例与产品 reference 对齐，V 另查失败 cell 保持未知；只为描述性 contrast，非因果/独立收益证明。未重跑暂停批次 |
| FM-10 方法 S、使用方法 φ、改进方法 G 的版本、使用与评价 | [research/schema.py](../../src/etalon/research/schema.py)、[journal.py](../../src/etalon/research/journal.py)：`MethodVersion/MethodUse/MethodEvaluation` 与引用、角色、快照/冻结校验；复用同一本 journal | 宿主/skill 决定选择、部分采用、组合、换法、试用与提出改版；可记录未选择，不能要求先有优越性证明才能探索 | 原 ResearchJournal/Artifact/科学记录不改义；新增 `etalon_method_record/read` 为薄封装 | **已解耦：方法记录与科学执行/观测**；自动产生更好 skill、使用策略或改进器及独立收益均**未完成/待证** | MET-01—07、RES-08；[test_research_methods.py](../../tests/test_research_methods.py)。前轮原生改版记录仍保留 `parents=[]`；本轮固定源新追加案例已写入精确结构化父引用，另一个新 root 仍为 `parents=[]`，旧记录/快照/科学状态不变。保存和部分试用不表示收益，不为动作增加 method gate |
| FM-11 事件通知、恢复 observer 与改进 hook | [runtime/api.py](../../src/etalon/runtime/api.py) 的 `read_events`；[host.py](../../src/etalon/host.py) 的 `consume_events/hook_request_id`；原 `result_observer` 继续负责准入前恢复收据 | 宿主/skill 可选择哪些已提交事件值得处理，显式读一页、跳过或停止；handler 使用稳定 request ID，回调不回滚科学事实 | 原两种日志与恢复 observer 不改义；无原生 hook 时显式读事件也可继续 | **已解耦：持久执行事实与可选事件消费**；现有 hook 是有限函数，不是自动 RSI 服务 | EVT-01—04、ACT-06；[test_thin_harness_host.py](../../tests/test_thin_harness_host.py)。组件已覆盖崩溃重放/过滤/不自触发；宿主真实 hook 的新增范围仍需独立证据，不能凭 callback 单测声明全宿主自动触发 |
| FM-12 skill 发现、内容版本、开放引用与使用指引 | [skills.py](../../src/etalon/skills.py) 的 `snapshot/read_member/persist_snapshot/verify_snapshot`；完整 references/scripts 内容版本；[HOST.md](../../src/etalon/HOST.md) 的使用指引独立版本 | 宿主按需发现/读取；内置目录只作发现入口，允许外部 bundle、局部使用、组合、修改和试用；内容校验不认证完整内部使用 | `etalon://skills/*`、旧 catalog/workflows shape、`etalon_capabilities/etalon_skill_read` | **已解耦：方法目录与允许的行动空间**；六个原 SKILL 的历史内容迁移是其说明层工作，不是这个接口解耦的全部证据 | MET-01—07、HOST-01；[test_host_bundles_hooks.py](../../tests/test_host_bundles_hooks.py)、C 原生 methods/revision/discovery 日志。Codex 的限定行为证据不替代 Claude，也不证明选择策略更优 |
| FM-13 安装、宿主导出、MCP 注册与科学资源打包 | [mcp/server.py](../../src/etalon/mcp/server.py) 单一注册/费用来源、[host.py](../../src/etalon/host.py) 导出校验、[pyproject.toml](../../pyproject.toml) 完整包资源；科学依赖及本地修复保留 | [install skill](../../.claude/skills/etalon-install/SKILL.md) 按任务选择环境/依赖，宿主配置从同源导出；安装不是科研流程的强制起点 | `python -m etalon.mcp`、旧注册函数/资源 URI、原科学入口；Codex/Claude 分别导出 | **已解耦：资源读取与 checkout 布局**；install 方法说明为文档迁移。安装、真实宿主调用和科学验证仍是三个范围 | INT-01—03、HOST-01；[test_host_capabilities.py](../../tests/test_host_capabilities.py)、I/V 非 editable 安装证据。科学协议无需为新目录重写；跨机器还须带必要环境说明和独立 PRISM 来源 |
| FM-14 开发者执行本次重构 | 本地 [life-healthy-contract-refactor](../../.agents/skills/life-healthy-contract-refactor/SKILL.md) 与 `merge_preview.py`，仅辅助来源保全、逐文件预览、接口衔接和工程验收 | 这是 **I 维护的工程 skill**，A/B/C/V 按需使用；不是 C 的科学方法库，不参与产品 S/φ/G 收益评价 | 不新增产品 MCP 工具，不改科学入口；遵守本机 exclude/hook，独立迁移本地辅助文件 | **开发工程工具已制作并用于真实合并**；不能计为科学 workflow 的 skill 化或 RSI | I 的两次真实合并预览/保全检查；产品 registry、wheel、两种产品宿主导出不得自动包含该工程 skill。该链接仅本机存在；clone/产品源码 ZIP 不携带本地辅助内容 |

### 六个旧 skill 文档到底迁了什么

下列保留第一阶段说明迁移的历史行数；本轮具体方法补充后的入口长度不由该表代表。各领域代码是否可独立调用，要看上表对应行。与重构前 `baseline.zip` 比对，六份 `references/legacy-workflow.md` 均在增加 8 行历史说明后保留原 SKILL 的逐行文本。历史文件中的顺序、机器条件和阈值不因此成为新方法的通用要求。

| 产品 skill | 原 SKILL 行数 → 新入口行数 | 保留旧文本的 reference 行数 | 说明层判定／关联功能 |
| --- | ---: | ---: | --- |
| etalon-campaign | 547 → 37 | 555 | 文档迁移完成；FM-02/03 的执行解耦另按代码和用例判断 |
| etalon-screening-sweep | 353 → 29 | 361 | 文档迁移完成；FM-05 的领域组合行为待补证 |
| etalon-gate-calibration | 143 → 27 | 151 | 文档迁移完成；FM-06 保留原校准算法 |
| etalon-generation-planning | 164 → 26 | 172 | 文档迁移完成；FM-07 的旧规划/调度策略逐项核验 |
| etalon-campaign-monitoring | 202 → 27 | 210 | 文档迁移完成；FM-08 的判断与 FM-04 的执行事实分开 |
| etalon-install | 240 → 32 | 248 | 文档迁移完成；FM-13 的真实安装/宿主证据另列 |

行数只是第一阶段来源盘点值，不是优化目标；本轮六份 legacy reference 仍逐字节不变。后续允许按需求修改、拆分或合并 skill；不要为了保持短入口而丢失适用条件、失败处理和工具调用说明，也不要把全部历史 reference 再固定注入每轮上下文。

### 更多 research 方法与旧策略可选性接续

2026-10-21 用户再次授权继续剩余重构。本次在 FM-09 的 factorial 之外补充 [finite-diagnostics reference](../../.claude/skills/etalon-campaign/references/finite-diagnostics.md)，对齐现有 `calibration.bind_outcome`、`diagnostics.condition` 和 `diagnostics.exact_program`。三者可分别使用，也可根据真实返回部分组合；没有新增 skill 注册、MCP 科学工具或研究执行框架。绑定会向原 Journal 追加派生记录，后两者为纯函数；有限模型的建议树不会自行执行，也不是宿主必须遵循的流程。研究生产算法保持原样。

FM-07/08 进一步核对 `ParameterChange.judge`、`Acquisition.propose`、显式单阶段 `Pipeline` 与 `Supervisor` 状态/生产率读取。旧 `Campaign.round` 和 `Supervisor.tick` 是调用者选用的整体编排；普通 SDK 方法、合法 MCP 动作没有新增对它们的前置依赖。构造旧 Screen/Supervisor 仍可能创建状态，不等于只读查询入口；需要只读时使用前轮已有适配。有效的科学输入、probe/resource 依赖、gate 和预算约束保留，不把它们误判成应删除的流程耦合。

本次具体测试范围、原生宿主与 SDK 的不同证据强度、失败和未运行项见 [I](DELIVERY_I.md)、[V](DELIVERY_V.md) 及各线追加节。前轮原生/POSIX 原件继续有效于其固定来源，不重复运行充数；新 reference 和后续文档不能冒称属于前轮受测源码。其余 research 方法仍未全面迁移，单次使用不证明 S、φ、G 或科学知识 M 的独立收益。

### 配对经验模型与记录后使用接缝

后续按实际使用补充 [paired-models-and-segments](../../.claude/skills/etalon-campaign/references/paired-models-and-segments.md)：现有 `empirical_model` 从完整配对开发记录构造有限经验模型，可显式选择组均衡假设；不把相关行当独立样本、缺失对照补零或模型构造当证据准入。算法未重写。`record_program`、`compile_first_segment`、`observed_branch` 的可选记录/首段交接/反馈读取分别说明，不要求串行采用。

本次在 `research/runtime.py` 修复两个真实接缝：同 probe 的错误已声明条件不能再进入原分支；编译和反馈读取重新核验模型及复用资源证据，不能沿用已经变字节的路径。保留旧合法签名、直接离散标签语义和既有运行内核。连续量仍使用显式 OutcomeRule 的已有入口，不替宿主猜阈值或新增控制器。

编译只返回 `compiled_not_executed`；有效工程 bundle 另经真实 runtime.plan 预检并绑定输入哈希，假 bundle 仍被预检拒绝。这些检查未执行科学任务。各次源码和原始失败分开保存于 `integration/thin-harness-research-use-20261021/`，实际独立判定见 DELIVERY_V。本段不把前轮“research 未改”覆盖成新事实：本轮明确有一个实现文件及其领域说明的增量，其余保护范围以最终 manifest 为准。

### I 接续清单与本轮交付范围

前轮功能接缝清单的限定工程范围已由 A/B/C/I 实际完成，独立判定见 [DELIVERY_V](DELIVERY_V.md)，来源及失败原件见 [DELIVERY_I](DELIVERY_I.md) 的功能接续节。完成范围为 FM-05—08 的最小有用场景、FM-09 的一个现有 research 方法、FM-10 的结构化父版本新案例及必要打包；不表示整个功能表全部 skill 化。Claude 仍 blocked，独立 S/φ/G 收益仍 not_run。

本清单是并行工程分工，不是研究 agent 的固定 workflow；各线无需等待全表完成才能推进独立项。沿用 [WORKSTREAMS](WORKSTREAMS.md) 的唯一文件负责人；未列入的源码/测试先由 I 登记归属，V 不改被测实现来制造通过。

| 责任与范围 | 现在继续完成什么 | 交付与完成判据 |
| --- | --- | --- |
| I：全表、公共接口、打包和工程 skill | 核对最新来源/活动写入者；把接续宿主/POSIX 证据与对应行关联，保留旧失败和旧快照；公共 SDK 只补实际缺少的调用口 | 在现有 DELIVERY_I 逐项写“保留/复用/已解耦/缺口”及原因，给出源码/环境/测试绑定；每次交付保存含 dirty/untracked 的新源码清单，不覆盖既有 ZIP。不要新建迁移框架或仅为目录整齐移动实现 |
| A＋C：FM-05/06/07/08；B 负责原校准文件 | 各选真实有用的最小场景，检查“只用一个能力”“反馈后换方法/停止”“跳过原流程的其他阶段”能否成立。审查旧 Campaign/Supervisor/propose 的真实强制依赖 | 原接口已满足时，补对应 skill 的具体适用/输入输出/停止说明与有区分力证据即可，**不强制修改代码**；确有绑定则仅提取现有函数/参数并复用执行内核。相同动作的新旧身份、费用、准入、恢复须一致，不要求研究动作顺序一致 |
| B＋C：FM-09/10 | 从已实现 research 算法中选一个当前有用的方法，明确算法函数与宿主判断的边界；补原生改版例的结构化父版本引用证据，保留既有无父记录 | skill 指向真实可调用函数；任务不涉及该方法时可以完全不用。通过新追加案例保留旧/新完整快照、旧绑定和 `parents` 关系；不得回写旧记录、要求所有新方法都有 parent，或把记录成功称为 M/φ 已改善 |
| C：FM-11/12/13 | 保留无 skill、开放 bundle、按需/部分/组合/改版/停止的实际行为；需要时补显式事件读取或可选 hook 的原生场景 | 真实宿主请求/返回/内容版本/下一动作相互对应，观测强度如实标记；不提供完整强制工具顺序。Claude 401 精确保留 blocked，不更改全局认证、不循环重试、不阻塞其他工程 |
| V：全表独立检查 | 在实施前给具体场景确定失败条件；核验哪些只是说明迁移、哪些已有操作自由、哪些确有新解耦；检查原生记录与最终安装来源差异 | 复用 ACT/RES/EVT/MET/HOST/INT/LEARN ID，状态带证据范围。覆盖“跳过/部分采用/组合/修改后试用/停止”，含拒绝反例；不以文件变短、源码仍在、工具退出 0 或模型自报替代验收 |

**共同完成标准：** 同一科学动作不丢身份、协议、实际费用、失败/未知、准入和恢复；宿主又能自由改变是否做、做哪一步、采用什么方法。保留的旧数值策略/局部 workflow 可以继续作为可选方法，不强制删掉，也不能升级为所有研究的前置条件。普通查询和探索不必经过统一 router、method gate、全流程 DAG 或另一个 ETALON 规划器。

2026-10-21 按需使用增量（FM-09/10/11/14）：`etalon_capabilities` 和 Python `skills.catalog` 增加可选的 summary/精确范围查询，零参数仍为旧完整返回；每类 `None` 表示全部、`[]` 表示不查询。已知接口可直接调用。完整版本引用覆盖所选 bundle 的全部内容，summary 不等于仅读取入口字节。现有 finite-diagnostics、paired-models-and-segments reference 已补可直接执行的最小 SDK 输入与失败/停止说明，算法不重写。[局部批读示例](../../examples/local_sdk_batch.py) 可由宿主修改组合，逐项保存完整原件、保留失败费用和未知状态；它是源码示例，不是 wheel 内资源、后台服务或必经阶段。具体兼容、包与原生三组对照分别见 [I](DELIVERY_I.md) 和 [V](DELIVERY_V.md)，组件负载缩小不能替代端到端收益。

本轮 [E 三组实测](DELIVERY_E.md) 已完成：两项已见工程任务的六次原生运行均通过语义与保全检查，但 G1 未显示端到端提速，两次 G2 均跳过 Laya。独立人工模型案例保留两项语义错误，不默认启用模型。这个结果支持所观测的宿主选择自由，不证明生产高频、自然采用收益或科学 S/φ/G 改善。

R4b 及历史科学证据继续保持原边界；此清单授权范围沿用已批准的工程工作，不新增真实 PRISM 评分/CRO 或把旧已见数据当未见测试。S、φ、G 的独立收益仍按 LEARN-01/02 的新任务与等预算对照判定；方法改版、原生试用和执行可靠性都不能替代收益证明。

### I2 全量能力族覆盖（2026-10-21）

本表是现有功能映射的增量，不是第二套运行 registry 或必经顺序。唯一可发现目录仍是 `skills.WORKFLOWS`，工具以实际 MCP 注册为准。旧六项和六项 MolQuarry 加三个按能力族组织的新入口，均可跳过、局部使用或与外部方法组合。下表的已补内容与调用口不自动等于宿主已采用；实际工程、安装、原生采用和未运行项目在 DELIVERY_I/V 分开列出。

| 原功能／公开能力族 | 方法内容位置 | 真实入口与示例／验证 | 处置与边界 |
| --- | --- | --- | --- |
| FM-01 来源获取、冻结、候选、测定、handoff | `data-design` 的 data-operations reference | 九个 `etalon_data_*`；data.service/artifacts/library/ingress；`examples/data_design_methods.py`、`test_data_design_methods.py` | 补齐源/角色/副作用/拒绝；零网络合成导入与旧原测试，不新增外部采集；完整 Astra 标注学习闭环未建立 |
| FM-01 design bundle | `data-design` 的 design-bundles reference | data.design `read_bundle/import_bundle/verify_import/design_index`；同一示例可选 bundle | 补齐 pool/index-only、幂等、raw/pose身份、失败槽和Astra假设；不生成科学标签或评分 |
| FM-01 MolQuarry 六方法 | 原 `molquarry-analogue-search`、`assay-literature`、`compound-sourcing`、`selectivity-evidence`、`structure-templates`、`target-modulators` bundles；data-design/MolQuarry适配说明 | `etalon_skill_read("molquarry-…")`、旧 `etalon://skills/molquarry[/…]`；原 SDK 与 data adapter | 六源原样复用，首次统一发现/完整版本/导出/安装；不通过外部查询制造验收 |
| FM-02 治理、准入、稳定性、council、FEP网络 | campaign 对应 evidence/governance reference 与保留 legacy | `etalon_check_handoff/check_stability/rule_admissible/authorize_spend`、`etalon_council_reliability/adjudicate`、`etalon_recommend_waiver/design_fep_network` | 复用独立工具与算法；建议不授予豁免，网络设计不执行FEP，计算读数不冒充实验 |
| FM-03 active策略与宿主选择动作 | campaign；research evidence-panels、既有 runtime contract | `etalon_active_create/status/plan/replay`、`etalon_action_prepare/submit/result`、`etalon_executor_prepare/register/list`；既有 action/host 验收 | 已有可选策略/可靠执行复用；离线replay不等独立科学运行，真实动作预算身份准入继续由内核约束 |
| FM-04 durable局部操作、恢复、取消、artifact | monitoring、campaign、`etalon://runtime` | `etalon_workflow_capabilities/plan/submit/status/observe/advance/cancel/reconcile/artifact`、`etalon_active_execution_plan/submit` | 保留旧兼容入口、pending/费用/恢复证据；仅已实现平台可执行，Windows不冒充POSIX worker验证 |
| FM-05 screen/sweep | sweep 的局部操作 reference 与 legacy | `etalon_screen_configure/plan/submit/status/export`、`etalon_sweep_admit/emit/claim/record/recover/status/progress` | 补具体独立用法，旧CPU/零命中/只读/失败费用证据复用；不重跑生成或昂贵科学计算 |
| FM-06 gate校准 | gate-calibration 局部SDK reference | campaign.calibrate `calibrate/separation/enrichment`、`etalon_calibrate_gates/authorize_gates` | 复用已有算法，补可单独计算的输入/停止边界；建议不自动修订未来协议 |
| FM-07 生成产率、漏斗与参数建议 | generation-planning 局部SDK reference、sweep | `etalon_generation_productivity/campaign_plan/plan_campaign/tune_screen/stages`；generate.productivity、campaign.Pipeline/ParameterChange/Acquisition | 补组合与跳过方法；数值建议不启动生成，不重写finite/paired/factorial |
| FM-08 监控/局部批读 | monitoring 的已有批读与局部操作说明 | workflow/sweep只读接口、`examples/local_sdk_batch.py` | 复用真实只读/失效收据反例；不新建轮询研究循环，未知状态与费用保留 |
| FM-09 迁移拟合/校准/下一动作/比较 | research `references/transfer.md` | transfer `fit_policy/calibrate_policy/next_action/evaluate_policies/probability_assessment`、transfer_checks/support；`scripts/research_methods.py` | 补已实现有限模型的独立调用；synthetic与实测角色分开，完整面板回放不当独立执行 |
| FM-09 validation/feedback | research `references/validation-feedback.md` | validation、feedback、baseline_feedback 的实际冻结/释放/附加/读取入口 | 补说明与例；原评价角色不回写，已消费反馈不能再次称未见，无新独立收益验收 |
| FM-09 pose/preparation | research `references/pose-preparation.md` | pose/pose_selection/preparation；已有真实源契约+隔离SDK检查 | 补图/状态/姿势/受体/准备/执行身份；NOT_APPLICABLE与失败/未知不补零；不做原生准备或评分 |
| FM-09 fixed_pool/online/retained baseline | research `references/fixed-pool-online.md` | fixed_pool/online/pool/rows/producer/workflows/retained_baseline；合成可见查询例 | 补可分别使用的受测方法，公开成本与未知反事实分离；历史R3/R4/R4b脚本只作为兼容实例 |
| FM-09 evidence/panel/有限诊断 | research `references/evidence-panels.md`；原campaign factorial/finite/paired references | ResearchJournal/schema、panel/seen/controller；`bind_outcome/condition/exact_program/empirical_model/record_program/compile_first_segment/observed_branch` | 复用原算法与已有验收，补入口定位和能力关系；无全局router、固定研究DAG或method gate |
| FM-10/11 方法版本/使用/评价、事件与改进 | skill-improvement revision reference；HOST使用指引 | MethodVersion/Use/Evaluation、`etalon_method_record/read`、`etalon_events_read`、host.consume_events；`examples/skill_improvement.py` | 新增可选具体改进方法，分别保全S与使用策略、部分试用和停止；独立收益仍not_run |
| FM-12/13/14 发现、版本、宿主、安装与基础设施 | install、HOST_SETUP；原各bundle内容 | `etalon_capabilities/skill_read`、skills.snapshot/read_member/persist_snapshot、host export/verify、`etalon_infrastructure/doctor/devices` | 原零参字段与旧URI兼容，完整引用随wheel/双宿主导出；SDK/协议与原生采用分开，Claude旧401仍独立blocked |

工程重构 `life-healthy-contract-refactor` 只在本地 `.agents/skills/`，不进入这张产品库、wheel或宿主导出。任何外部阻塞（宿主认证、科学依赖、未获授权的新计算或未建立的独立评价）都不阻塞无依赖的发现、按需读取、SDK示例和打包工作。

本轮工程交付：15项原内容/新方法完整发现与双宿主导出，945个源/wheel/install条目一致，新增组合23测试通过（包含V8项）。两个原生新任务分别完成结构导入/坏哈希拒绝与外部方法修订审阅，但第一任务443159 input超过单次400000，预算失败保留；第二任务通过。两次均未观察到内置skill读取，不宣称新增库已获自然采用或效率收益。完整质量、成本、旧Windows权限/可选依赖/Claude阻塞和科学未运行边界见DELIVERY_I/V。
