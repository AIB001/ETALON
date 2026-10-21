# 并行工作线与新 chat 任务说明

2026-10-21 新接续的 I4/A4/B4/C4/V4 文件归属以 [EVOLUTION_WORKSTREAMS](EVOLUTION_WORKSTREAMS.md) 为准；下文保留历史批次职责与验收，不据此再次启动旧工作线。

## 当前接续 I2：全量能力覆盖（2026-10-21）

本轮已交付：A/B/C与V仓库写入完成，I集成新增23测试通过（含V8）；固定安装完成两个原生任务。两项语义通过，首项单次input超限预算失败、第二项通过，原件保留无补跑。15项产品完整库已实现；内置方法原生采用和独立收益未建立。最终原件/封口沿本轮i/v目录，结果向R一次通知；后文旧158项等数字是历史轮次。

实际启动 A `/root/a_data_operations`、B `/root/b_research`、V `/root/v_acceptance`；均已读取并开始核查实际 SDK/判据。I2 另接管旧 `tests/test_mcp.py` 打包来源假设与 `tests/test_thin_harness_efficiency.py` 固定六项数量假设，按实际内容更新兼容检查；V 已获通知，新 V 判据不由 I2 修改。

V 已在实现结果前冻结本轮判据 SHA256 `da100aeb4ba05280acc8330730dd00883e7eb49f19650ab3772c51596bc5bdd3`，随后 C `/root/c_usage_improvement` 实际启动。A 已运行零网络catalog/design/局部规划例；B 已运行transfer/visible-query/pose合成例；C 已运行内容/使用策略版本、部分使用与单页hook例。新原生限定两个任务，先绑定非editable安装和输入，V稍后回场独立复核；不以计划或派发代替这些执行记录。

本节优先于下方历史归属。用户明确授权 I2 接手、subagents 并行实施；R 继续审查，REQUEST/COORDINATION 仅 R 写。原 I 已收尾。共同 main checkout 不切换、不暂存；1196 文件完整基线位于 workspace `integration/thin-harness-skills-20261021/i/baseline.zip`，SHA256 `781095cb26f291feda07ccf27c3938f3bed8f576a0d1fbe63ec40699d00c922d`，暂存差异为空。旧科学 dirty/untracked、原件和 R4b 暂停保留。

| 当前唯一写入者 | 本轮拥有路径（相对 ETALON） |
| --- | --- |
| I2 `/root` | `src/etalon/skills.py`、`host.py`、所有 `mcp/` 共享封装、`pyproject.toml`；本目录 README/WORKSTREAMS/ACCEPTANCE/三个契约/DELIVERY_I；注册导出测试 `tests/test_skill_library_integration.py`；本地 `.agents/skills/life-healthy-contract-refactor/`。公共包装、安装及最终来源由 I2 单写 |
| A `/root/a_data_operations` | `.claude/skills/etalon-data-design/`（新增）；现有 screening-sweep/gate-calibration/generation-planning/campaign-monitoring/install 五目录；`asset/molquarry/skills/` 保留原内容，非必要不改；`examples/data_design_methods.py`、`tests/test_data_design_methods.py`；`src/etalon/data/design.py` 仅复现实际接缝后修复；DELIVERY_A |
| B `/root/b_research` | `.claude/skills/etalon-campaign/`、新增 `.claude/skills/etalon-research/`；`src/etalon/research/` 必要接缝（不重写算法）；`examples/research_methods.py`、`tests/test_research_skill_methods.py`；DELIVERY_B |
| C `/root/c_usage_improvement` | `src/etalon/HOST.md`、`HOST_SETUP.md`；新增 `.claude/skills/etalon-skill-improvement/`；`examples/skill_improvement.py`、`tests/test_skill_improvement.py`；DELIVERY_C。不得写 skills.py/host.py/MCP |
| V `/root/v_acceptance` | `tests/test_thin_harness_skill_library.py`、`tests/fixtures/thin_harness/skill_library_acceptance_20261021.json`；DELIVERY_V；不改被测实现 |

各线原件只写 workspace `integration/thin-harness-skills-20261021/{i,a,b,c,v}/` 对应目录。新路径先与 I2 登记；A/B/C 分别提供能力族映射和真实可运行例，由 I2 汇入既有 README，不建第二套 registry。工程 skill 和产品方法库分别统计。无科学评分、封存标签读取、旧恢复、共享升级、commit/push。原生新增验证先固定少量区分性案例，最多 6 次，每次 300 秒/24 次 shell 或 MCP/400000 input/8000 output，累计 1800000 input/36000 output；失败保留，不补跑到通过。

日期：2026-10-21。状态：**A/B/C/I 实现与 V 独立工程验收已交付；最终组合 158 passed，未运行与平台失败见 DELIVERY。**

本文是文件责任的唯一清单。三个契约负责语义，本文负责谁写什么；[ACCEPTANCE](ACCEPTANCE.md) 负责用例定义。本次用户授权取代历史文档阶段与交回 R 的安排；I 协调实现和公共契约，V 独立验收，不向原 chat 回传。

## 1. 角色与启动登记

| 角色 | 职责 | 当前承接者 |
| --- | --- | --- |
| R | 历史讨论角色；本次不作为审批或回传依赖 | 未参与本次工程 |
| A | 动作执行、结果视图、预算／恢复、PRISM 消费接缝、事件读取 | /root/a_execution_resume |
| B | 科学 SDK、证据及三类方法记录、评价绑定 | /root/b_methods_resume |
| C | skill 内容、开放 bundle 引用、宿主指引、使用与 hook 消费 | /root/c_host |
| I | 共同来源基线、共享 MCP／打包文件、接口衔接与集成 | /root |
| V | 独立验收用例、跨入口检查、真实宿主行为及改进评价审查 | /root/v_final（接续 v_acceptance 早期协议） |

可由一个新 chat 承担不冲突的多个角色，但要在交付中分清事实与判定。独立科学评价的数据访问边界不能仅靠角色名称建立。

I 记录实际 chat／分支／工作树和归属接续；用户已指定的工程范围无需再次逐项请示。若原组件 chat 仍在写同一文件，先协调交接或处理不冲突文件，不直接覆盖。本文不自动创建聊天、派发子 agent 或解除科学暂停。

## 2. 文件唯一归属

路径相对 ETALON 仓库。明确列出的例外优先于目录规则；未列入的文件不因“顺手修复”取得所有权，应由 I 先登记唯一归属。各线可读全部相关文件，写入限于归属范围。

| 唯一写入方 | 文件／范围 | 边界 |
| --- | --- | --- |
| A | `src/etalon/active/runner.py`、`store.py`、`schema.py`、`prism.py`、`budget.py`、`protocols.py`、`setup.py` | `runner.py` 必须合并 main 预算修复与本地 pending；不改数值方法含义 |
| A | `src/etalon/runtime/api.py`、`schema.py`、`operations.py`、`service.py`、`store.py`、`executors.py`、`cli.py`、`README.md` | action/result/events 后端；不得修改 I 所有的 MCP 封装 |
| A | `src/etalon/boundary/prism_v1.py`、`screen.py`；`src/etalon/data/design.py`、`data/contracts/`；`src/etalon/faults/preflight.py`；`src/etalon/screening.py` | 消费端契约和科学输入边界；PRISM 生产端另有唯一负责人 |
| A | `src/etalon/campaign/generation.py`、`supervisor.py`、`sweep.py`、`drivers.py` | 吸收上游可靠执行修复；调度策略可供 skill 选择，不重建主循环 |
| A | 五个已有修改的 `asset/molcascade/src/molcascade/{cli.py,io/molecules.py,io/parquet.py,runtime/cache.py,runtime/runner.py}` | 优先原样保留；不借本次任务重构资产目录 |
| A | `tests/test_active.py`、`test_runtime_*.py`（**排除 `test_runtime_mcp.py`**）、`test_prism_v1.py`、`test_design_import.py`、`test_local_windows_cpu_io.py`、`test_pose_driver_interpreter.py`、`test_screening_service.py`、`test_sweep.py`、`test_campaign.py`、`test_campaign_integrity.py`；已有 PRISM design fixture | B/C 的模块需求由 A 修改这些测试；新增执行用例也归 A |
| B | `src/etalon/research/` 全部模块，及 `src/etalon/campaign/calibrate.py` | journal/schema 只由 B 写；历史 R3/R4/R4b 文档和冻结意义保持不变 |
| B | `tests/test_research_*.py`；必要的科学／方法记录单元用例 | 不修改 V 的独立评价 fixture；不通过更改评价结果使候选通过 |
| C | `.claude/skills/` 产品 skill 源及 references/scripts；后续新增方法改进 skill | 正确监控目录名为 `etalon-campaign-monitoring`；不另存一套平行源 |
| C | 薄分支中的 `src/etalon/skills.py`、`host.py`、`HOST.md`、`HOST_SETUP.md`、`mcp/host.py` | 可选使用、版本、导出、开放 bundle 与宿主适配；调用 B 的记录接口 |
| C | `tests/test_host_capabilities.py` 及必要的 bundle／使用／hook 消费用例 | 真实宿主端到端结论由 V 核验；不用配置存在代替调用 |
| I | `src/etalon/mcp/server.py`、`runtime.py`、`_common.py`、`active.py`、`execution.py`、`governance.py`、`sweep.py` 及其他共享 MCP 注册文件，**排除 C 的 `mcp/host.py`** | 一个工具注册源、统一错误和费用分类；仅调用 A/B/C 后端 |
| I | `pyproject.toml`、根 `README.md`、`.gitattributes`、`.gitignore`、必要时一个公共 SDK 导出文件 | 同时保留本地科学依赖、薄分支 skill 资源和公共契约的跟踪例外；不新建 SDK 框架 |
| I | main 中本轮只需保留的 `active/decision.py`、`authority/__init__.py`、`council/{__init__,ballot,convene}.py`、`doctor.py`、`learn/admissible.py` | 合入已有修复；额外功能改动需先明确归属与目的 |
| I | `tests/test_mcp.py`、`test_runtime_mcp.py`、`test_council.py`；共享接口集成用例 | 旧测试中强制 campaign 路由的假设需改为契约行为，不能删掉正确性断言 |
| V | 拟新增 `tests/test_thin_harness_contracts.py`、`test_thin_harness_host.py`、`tests/fixtures/thin_harness/` | 这些名称是独立验收的预留位置，不要求机械创建全部文件 |
| I | 本目录的 README、三个契约、WORKSTREAMS、ACCEPTANCE；integration 的新交接入口 | 用户授权 I 接管公共契约维护；V 独立检查，不等待 R |
| 各线自身 | 本目录未来的 `DELIVERY_A.md`、`DELIVERY_B.md`、`DELIVERY_C.md`、`DELIVERY_I.md`、`DELIVERY_V.md` | 一线一份简短交付；本轮不创建虚假的实施报告 |

PRISM 整个仓库、历史科学输入／数据库／原始证据、旧设计包及其 manifest、`.agent-local/`、根本机 AGENTS 不属于这次实现线的默认写入范围。跨 PRISM 的生产端变更须与该组件实际负责人明确生产、消费、兼容和验证，不能单边改协议。

I 维护契约与跨线语义，V 独立检查 F01—F10；常规工程不等待 R，任何语义变化须在项目中记录影响。

## 3. 最小接口依赖

| 接口／资料 | 生产方 | 消费方 | 未就绪时可并行做什么 |
| --- | --- | --- | --- |
| 可恢复共同源码基线、U/T/L 来源映射 | I；各线核对自身文件 | A/B/C/V | 先写契约样例、审查已有方法；不覆盖 dirty/untracked |
| `prepare_action/submit_action/read_action_result` | A | I/C/V | 用明确标注的 fixture 测参数与调用约定，不声称真实执行 |
| `read_events` 和带日志身份的游标 | A | I/C/V；B 使用事件引用 | C 实现幂等处理，B 定义记录类型；不依赖全局总序 |
| ResearchJournal 三类方法记录／读取 | B | C/I/V，A 只需可选引用 | A 可先实现空方法引用的完整路径，避免强制 method gate |
| bundle 快照／显式 bundle 引用／使用指引版本 | C | B/I/V | B 用固定字节夹具核验记录；不依赖正式内置目录 |
| MCP 签名、注册、打包 | I | 宿主/V | A/B/C 先通过 SDK 检查；MCP 不复制后端校验 |
| 有区分力用例与评价范围 | V，契约由 I 维护 | 全部 | 在编码同时给出失败条件，不能等实现完再按输出改标准 |

共同基线必须包含本地未跟踪科学源码；可以是可复原的提交／快照加固定 U/T 来源，不要求 I 先独自完成全部领域合并。A/B/C 各自在拥有文件上合并相关上游行为；I 只负责共享文件和最终组合。

启动时只对齐三个契约的字段／样例、文件归属及来源 pins，随后并行。接口接缝先连通，测试随实际依赖完成；不划分串行大版本，也不借“所有模块还没完成”阻塞无依赖工作。

## 4. 各线交付格式

每份 DELIVERY 只需包含：

1. 实际源码起点、来源合并、当前分支／工作树、改动文件和实际模块导入路径。
2. 公共签名、请求／响应样例、生产端／消费端、错误与兼容说明。
3. 对应 ACT/RES/EVT/MET/HOST/INT/LEARN 用例：已运行命令、环境、产物位置与结论。
4. 明确标记 `passed/failed/not_run/blocked`；fixture、真实工具、历史回放和独立科学结果分开。
5. 原有身份、成本、pending、恢复、数据暴露边界是否保持；回退方法。
6. 剩余限制和跨线需求。不能用“全部测试通过”替代未完成的真实宿主或科学验证。

不要求生成新管理数据库、持续状态广播、通用报告框架或自动发布。代码和测试仍进入正常版本控制范围；不自动提交／推送，不绕过本地保护。

## 5. 可直接交给新 chat 的任务说明

下面任务已由本次工程启动采用；其中历史 R 回传安排由上方当前启动登记取代。启动人需提供实际仓库位置；移机不照搬原机绝对路径。只复制一条工作线也应读取共同 README、对应契约、本文所有权表和验收用例。

### A：动作执行与结果内核

> 请作为 ETALON 薄 harness 的 A 工作线实施动作、结果和事件读取后端。先读仓库指引及 docs/thin-harness/README.md、ACTION_CONTRACT.md、RESULT_CONTRACT.md、WORKSTREAMS.md、ACCEPTANCE.md。核对 I 给出的共同来源基线和当前文件写入者，保留本地科学源码与 main 修复。
>
> 只写所有权表中 A 文件。把宿主已选动作与数值策略选动作分开，复用同一预留、执行、收据、准入和恢复实现。提供 prepare_action、submit_action、read_action_result、read_events 语义；首要新增 active.evaluate。method_use_refs 允许为空，不要求完整 workflow，不伪造模型；保留 ExecutionPending、实际失败费用和原生执行支持范围。结果 observer 仍是恢复收据，普通 hook 只消费提交后的事件。
>
> 与 B 对齐准入和证据解释，与 C 对齐事件读取，与 I 交付后端签名。不要改 MCP 共享文件或 ResearchJournal。完成 ACT-01—09、RES-01—07、EVT-01 的适用用例，写 DELIVERY_A.md。未具备科学环境时继续工程工作并明确未验证边界；不恢复 R4b、不部署共享环境、不自动跑昂贵科学任务。

### B：科学 SDK、证据与方法记录

> 请作为 B 工作线实施 docs/thin-harness 中的科学 SDK 和方法契约。先读当前仓库指引、README.md、METHOD_CONTRACT.md、RESULT_CONTRACT.md、WORKSTREAMS.md 与 ACCEPTANCE.md，核对来源和写入责任。
>
> 只写 B 文件。保留研究算法和历史协议的含义，开放可组合函数；在既有 ResearchJournal 上支持 MethodVersion、MethodUse、MethodEvaluation，复用 Artifact、TaskSplit、不可变记录和冻结／释放机制。不要另建账本，不把 skill 修改塞进科学 Change，不把记录保存当成 M/φ 已更新或学习有效。
>
> A 的科学执行可不依赖方法记录；C 使用你的校验／写入入口；I 负责 MCP 封装。提供清楚的记录／引用样例，验证元数据不冒充观测或独立评价、失败和未知不补值。覆盖 RES-08、MET 中记录与角色用例及 LEARN 的工程支撑；科学收益未运行则明确 not_run。交付 DELIVERY_B.md，不修改独立验收数据或暂停批次。

### C：Skill、宿主和选用方法

> 请作为 C 工作线实施可选择、可组合、可修改的 skill 与宿主接入。读 docs/thin-harness 的 README、METHOD_CONTRACT、RESULT_CONTRACT、WORKSTREAMS、ACCEPTANCE，并核对薄分支和 main 的 skill 差异。
>
> 只写 C 文件。保留短入口、按需 references、内容快照和使用指引独立版本；开放内置六项之外的显式 bundle 引用，保留路径与内容校验。宿主可不选 skill、部分采用、动态组合、停止、创建或试用新版本；不要求正式晋升后才能探索，不新建全局 router。
>
> 复用 B 的记录接口、A 的动作与事件接口。hook 消费采用日志身份和游标、处理版本及幂等请求；没有原生 hook 时允许显式读取继续，不新建后台 agent 主循环。产品 HOST/宿主导出保持同源，不改 I 的打包与共享 MCP 文件。覆盖 MET-01—07、EVT-02—04 和 HOST-01 的可执行部分，交付 DELIVERY_C.md；真实宿主行为未验证时明确报告。

### I：共同基线与集成

> 请作为 I 工作线负责 ETALON 薄 harness 的来源整合与共享接口。先读 docs/thin-harness 全部入口及本地协作指引。核对 main、薄分支、本地 dirty/untracked 三来源，形成可恢复共同起点和实际 chat／分支／文件归属，保留旧源码及科学证据。
>
> 你独占 WORKSTREAMS 表中的 MCP 注册／封装、pyproject、共享测试和公共导出；A/B/C 自己合并拥有文件内的领域行为。连接 action/result/events 和方法记录接口，保持一个工具注册源、完整费用分类及一致错误语义；不要在 MCP 再实现后端验证。
>
> 汇合本地科学依赖与薄分支资源打包，验证脱离 checkout 后的安装、资源和实际模块路径。按 ACT/RES/EVT/MET 交付检查接缝，并完成 INT-01—03。本次由 I 协调跨线契约，V 独立核验，不依赖 R 审批或回传。写 DELIVERY_I.md；不覆盖其他线文件、不自动提交推送、不部署或恢复暂停科学批次。

### V：独立验收

> 请作为 V 工作线独立验收 ETALON 薄 harness 契约。先读 docs/thin-harness/README、三个契约、WORKSTREAMS 和 ACCEPTANCE。只写 V 的验收文件与 DELIVERY_V.md，不修改被测实现或其他线已有测试来让它通过。
>
> 在实现推进时准备反例与固定夹具：无 skill 合法动作、动态新步骤、skill 原地改动后的旧版本绑定、未知执行、重复派发、无关备注与相关 stale、零命中、不适用、费用同源去重、hook 重放。真实宿主验证须实际加载／调用并保留可观测证据，SDK fixture 不替代它。
>
> 分别审查工程能力、真实宿主、指定科学贯通和独立改进收益。S×φ 四组及 G 等预算评价若缺独立材料，应交付可执行评价协议并标 not_run，不能用旧已见任务改名过关。问题反馈给对应文件所有者与 I，涉及理念偏移由 I 协调、V 独立核验并记录。不得自行恢复 R4b、读封存标签或新增昂贵科学运行。

## 6. 契约与理念检查

I 维护契约原意，V 独立检查 F01—F10，主要审查：

- 宿主是否真的能绕开固定 workflow／skill，动态提出新步骤并改变方法。
- 新接口是否复用可靠执行；身份、成本、pending、准入、历史恢复是否退化。
- 所谓学习是否有持久版本、实际使用、独立后续评价和总成本，S、φ、G、M 变化能否区分。
- 是否把本可用几个函数完成的事情变成新框架，或把长指引固定注入每轮上下文。
- 并行交付的源码组合、生产消费契约、实际检查与结论等级是否对应。

V 可读代码与证据、提出具体问题；公共文档由 I 维护，被测实现归原文件所有者。评审意见给出受影响契约、证据与最低修正，不凭抽象偏好要求重写整个实现。

## 本次现场启动与新增归属（2026-10-21）

共同 checkout 为本地 main（未切分支），HEAD `5927aceb346475a6f97550f561e7eb3b291611da`；本机 origin/main `0af4297fb7de435d858d54195561883a21526d5e`、origin/codex/refactor-thin-harness `d3889e6c0600605f018fb81ce3df1b420d6dcbf9`。这些是现场本机 refs，未声称查询了远端最新值。起始暂存为空；未发现 ETALON 相关活动进程。

可恢复基线在 workspace integration/thin-harness-engineering-20261020：baseline.zip 包含 1121 个 tracked/nonignored-untracked 文件（39016246 bytes），逐项 SHA 验证；sources.bundle 保存三个来源；working.patch、staged.patch、refs.txt、status.txt 保存 Git 状态。ZIP SHA256 `0149945f75ff9b6c9e0ed8f388b1e343c0a3f4c399d1a2e1af45540dfa51dcc0`。既有 ignored 科学证据、环境与本地指引原地保留，不属这次改动范围。

首轮真实启动 A/B/V；中断后旧 agents 不再活动，A/B 已按当前文件恢复，C 已实际启动。V 的早期反例与评价边界见 DELIVERY_V.md；并发容量为主线加三子线，最终 V 在实现槽释放后运行。

新增唯一归属：

- A：`tests/test_runtime_actions.py`。
- B：`tests/test_research_methods.py`。
- C：`tests/test_host_bundles_hooks.py`。
- I：`src/etalon/mcp/methods.py`、`tests/test_thin_harness_integration.py`；本地受保护 `.agents/skills/life-healthy-contract-refactor/` 与工程记录目录。该工程 skill 不进入产品注册。

本机 Python 为 `.venv/Scripts/python.exe`（3.13.12），etalon 实际导入 checkout/src/etalon。R4b 继续遵循 PAUSED，未部署、未恢复科学计算、未提交或推送。

最终接续：A/B/C 完成各自 DELIVERY，V 由 `/root/v_final` 完成 16 项独立反例和最终非 editable 包复核；I 完成 158 项冻结组合与材料收尾。所有实现文件保持未提交，后续改动继续按本表唯一所有者或显式登记交接。

## 接续：原生宿主与执行可靠性（2026-10-21）

用户已授权继续在同一 I chat 完成 HOST-01 原生 Codex/可用 Claude、ACT-05/06 与 RES-07 POSIX 进程边界，以及 A32/B5 广泛回归失败归因。不是新大版本，不建立管理框架，不等待 R 或回传原 chat。第一阶段 1153 文件与最终 manifest 现场逐项一致，无新增非 ignored 路径，暂存区为空；记录在 workspace `integration/thin-harness-native-20261021/entry-state.json`。原基线与最终包均保留。

实际接续者：I `/root`；A `/root/a_execution_resume`；C `/root/c_native`；V `/root/v_final` 先固定判据再复核日志；B `/root/b_methods_resume` 在槽位释放后仅处理实际依赖/方法接缝。沿用上方唯一文件归属，公共契约由 I 维护。

新增归属：V `tests/fixtures/thin_harness/native_acceptance_20261021.json`。各线的新运行材料分别写 workspace 工程证据目录的 A/B/C/V 子目录，既有 DELIVERY 只追加本轮事实。工具脚本和可复用测试需要另行登记，不把研究实现藏入本地忽略目录。

原生 CLI 登录可用性不等于完成 HOST-01；Python MCP client 不冒充宿主。每次运行隔离配置/导出/数据，不能改全局 MCP。Linux 仅在已有授权资源上新隔离工程目录/环境运行真实进程 fixture，先核验身份和活动任务，不覆盖旧源码/依赖/科学账本，不自行新增云资源或安装 WSL。R4b、真实新 PRISM评分、CRO、旧计算恢复仍不在本轮范围。

本轮新增登记：A `tests/test_runtime_posix_acceptance.py` 与 workspace `integration/thin-harness-native-20261021/a/remote_probe.sh`、`remote_driver.py` 及该目录的脱敏运行证据；C `integration/thin-harness-native-20261021/c_host/` 的独立宿主准备/运行日志和一次性工程 fixture 小脚本。它们不成为产品框架；凭证不写入证据。

B 已实际接续 `/root/b_methods_resume`，本轮拥有 workspace `integration/thin-harness-native-20261021/b/` 的依赖归因证据和必要隔离工程环境；暂不扩展产品文件归属。V 预先判据已冻结为 `native_acceptance_20261021.json`，SHA256 `5525fa623104559ee44ca968844e2ca15070b771cc8c408658f77c2107d09f9a`，之后只追加结果不据输出改判据。

C 调用 A 的既有远端传输 helper 时新生成的 `integration/thin-harness-native-20261021/a/c-native-*` 记录归 C；保留原位置和原始日志，不改 A 已有证据。远端只使用新建隔离工程目录，每个宿主案例使用独立账本；SSH helper 仅透传 MCP 字节，不替代原生宿主决策。

V 本轮独立复核输出 `tests/fixtures/thin_harness/v-native-*.json` 归 V。I 仍维护公共文档与共享 MCP；A 修复真实进程验收发现的 `runtime/service.py` 部分费用结算问题，C 的宿主反馈由 I 修正 `mcp/runtime.py` 工具参数说明。各原失败记录与旧源快照保留。

接续收尾：A/B/C 已各自完成追加 DELIVERY，V 独立核验真实原件与最终包；I 汇总于 DELIVERY_I、生成新的全文件快照和 FINAL_RECEIPT_V2。首次封口后 README 并行补表的状态文字发生变化，保留当前内容及首档失败记录后重新封口，未覆盖其他写入者。源码仍为未提交工作树，未改 PRISM 仓库或暂停科学批次。本轮待补范围为 Claude 的有效原生认证；独立科学/方法收益评价及 README 功能去向表中的其他待办并未被本轮验收覆盖。没有 R 审批或旧 chat 回传步骤。

## 接续：功能接缝与可组合方法（2026-10-21）

用户通过本 I 队列正式交回 README 唯一写入权，并授权按 function-migration 清单推进 FM-05/06/07/08 的最小功能场景、FM-09 的一个 research 方法和 FM-10 的结构化父版本追加案例。沿用既有验收 ID，不要求每行建立 skill，不将功能表变成宿主工作流。原 native 阶段证据不重写、不重复运行充数。

现场 HEAD/main 仍为 `5927aceb346475a6f97550f561e7eb3b291611da`，本地两个 origin refs 与前轮一致，未 fetch 或称为最新远端。1157 个当前文件与 native V2 manifest 全部相同，没有新增路径，index 为空，未发现相关活动进程。共同起点在 workspace `integration/thin-harness-functions-20261021/baseline.zip`，SHA256 `24b76668c45a72bd5c3517ffa6ef5bf9776f90e07a7f37b707bb36d32419cdc8`；含 dirty/untracked 的 manifest、patch 和状态另存，第一阶段/native 归档保持不变。

实际启动 A `/root/a_execution_resume`、C `/root/c_native`、V `/root/v_final` 并行。V 先固定场景反例后释放槽供 B `/root/b_methods_resume`；B/C 的研究方法对齐不阻塞 A 独立能力检查。各线的原文件归属不变，尤其 `campaign/calibrate.py` 只归 B，共享 MCP/打包/公共文档只归 I。

新增归属：A `tests/test_function_capabilities.py`；V `tests/test_thin_harness_functions.py`、`tests/fixtures/thin_harness/function_acceptance_20261021.json`、`tests/fixtures/thin_harness/v-functions-*.json`。workspace 新证据目录的 `a/`、`b/`、`c/`、`v/` 分别归对应工作线；各线 DELIVERY 只追加。新产品 skill 仍由 C 维护唯一源，具体与已有科学函数对齐后登记；未分配源文件先交 I 登记。

B 已实际接续，新 `tests/test_research_function_methods.py` 归 B；I 新 `tests/test_function_mcp.py` 负责真实 MCP 封装的只读接缝，shared `mcp/planning.py` 仍归 I。V 的结果前协议 SHA256 `9ff39fe7c2471c8ba6ec6dc6b9d272f988fa39d3f3e2503eecee0ed3b31106f2`，只读副作用补充 `v-functions-readonly-addendum.json` SHA256 `58f07e174debec05d90d4a17cbe0c4f7d102d491695fcc5b08206714c87e6502`。原协议冻结后不按结果改写，新增反例单独保留。

只在实际发现强制依赖或缺陷时修改领域实现；已有接口已满足则补具体方法内容和证据。普通查询、合法动作不增加方法门槛，旧数值策略仍可选。原 parents=[] 方法记录不得回写；新案例可追加真实父引用，但无父的新方法仍合法。工程重构 skill 只由 I 维护于本地保护路径，不进入产品库。R4b 继续暂停，不新增真实 PRISM 评分/CRO、旧计算恢复、共享部署覆盖或自动提交/推送。

只读接缝补充归属：`asset/molcascade/src/molcascade/artifacts/store.py` 本轮归 A，限复用现有校验的 `create=False` 构造支持；与 A 原持有的 runner/cache/Screen 组成读取已有工作区能力。I 将校准/授权 MCP 接到该读取入口，校准算法仍归 B 且保持原实现。该有意变更必须在最终受保护来源差异表列出，不能沿用上一轮“全部字节不变”的结论。

真实原生产率调用首次 120 秒超时及 I 冷 MCP 诊断复现后，B 以独立进程核查数值依赖接缝并释放槽给 A。确认 `src/etalon/generate/audit.py`、`tests/test_generation_audit.py` 由 A 唯一维护，限 Wilson 同公式的标准库替代及明确 confidence 定义域；不升级共享依赖或改其他生成策略。V 新增 `v-functions-cold-contract.json` 只细化原验收 ID，首个协议、原生失败、固定源和候选包均保留，修复另封新 snapshot 后由 C 原生复测。

本轮已完成：A/B/C 各自 DELIVERY 稳定，I 集成接缝/文档/新包，V 7 个独立反例及最终包冷 MCP、三种成功原生行为独立核验。C 首次产率超时保留，成功复测绑定新 source-v2；监控和 lineage 仍绑定原 source-v1。所有仓库内写入结束后 I 生成新的最终全文件 snapshot/manifest/receipt，V 仅向 workspace 工程证据目录写最终一致性检查，不为检查再改被封源码。README 继续归 I 唯一维护；未提交或推送。

## 接续：更多 research 方法与旧策略可选性（2026-10-21）

用户明确授权继续 README 剩余重构，优先补有用 research 方法及核查 Campaign/Supervisor/propose 的真实强制依赖。入口核验 1170 文件与前轮最终 manifest 全部相同、无新非 ignored 路径、index 为空，local refs 未变且未 fetch。新完整基线 `integration/thin-harness-research-20261021/baseline.zip` SHA256 `aab89b2d5a82571569a87e3e4ddae518f2d74f070d1e119084ec2f95232d31f2`；旧归档不覆盖。相关活动 Python 属于 PRISM_review，未重启或终止，未发现 ETALON 当前写入者。

实际并行启动 A `/root/a_policy`、B `/root/b_research`、V `/root/v_final`；V 先冻结反例后释放槽给 C，随后 V 再独立核最终组合。原唯一文件表继续有效。本轮 A 明确增加 `campaign/loop.py`、`propose.py`、`pipeline.py` 和 `tests/test_optional_campaign_methods.py`；B 增加 `tests/test_research_optional_methods.py`，仅 B 可改 research 后端；C 维护产品 skill/reference、host 相关文件；I 独占共享 MCP、打包、公共文档和本地工程 skill。V 增加 `tests/test_thin_harness_research_optional.py`、`tests/fixtures/thin_harness/research_optional_acceptance_20261021.json`、`v-research-*.json`。新外部证据目录 a/b/c/v 分别归各线，各自 DELIVERY 只追加。

优先用现有函数及可选方法说明，修复实际阻塞局部使用的接缝；旧默认策略和合法身份/预算/准入约束保留。没有要求每个函数一个 skill、没有新增统一 router/后台研究循环/第二账本。fixture 与 SDK/原生宿主/科学收益分开报告；不重跑已完成案例填表，R4b 继续暂停，无新真实评分/CRO、共享升级部署、提交或推送。

V 早期协议已冻结，SHA256 `bd346f1093b9d2cff097b885c41b0fb7ac142f19264dd07c72db4b701ef235cb`；随后 C `/root/c_research_skills` 实际启动，与 A/B 并行对齐产品方法。B 选定已有 `bind_outcome`、`condition`、`exact_program`：记录离散结果、判断支持、给局部有限预算建议是可分别采用的方法，不是宿主必须串行遵守的研究流程。C 后续原生使用绑定 I 新封存的完整快照，SDK/shell 与真实 MCP 调用分开计证。

本轮收尾：A/B/C 和 V 仓库内材料已稳定。A 独立参数建议/单阶段/不 tick 状态检查及后续真实非空 Acquisition 已完成；B 三个现有方法对齐，研究生产算法未变；C 正式 Codex 在固定 1175 文件来源中按需读 reference、实际 SDK 组合后停止；V 独立 5 反例、原生原件、包和来源已核。I 修共享计划文字、更新功能映射并打包，最终全源码及 manifest 另存 `integration/thin-harness-research-20261021/combined-source-final.*`，不覆盖 pre-native 或旧轮档案。V 仅向仓库外写封口一致性检查。独立科学收益未运行、Claude 401 仍 blocked、R4b 暂停；无自动提交或推送。

## 接续：research 实际使用接缝（2026-10-21）

用户授权继续有用方法，优先真实使用缺口，不为凑齐 skill 重写算法。新证据目录 `integration/thin-harness-research-use-20261021/`；完整 baseline.zip SHA256 `089c4589259232649bc6659e296441efb3578c4bcff2c93da7754eb15e9459e2`，1178 文件与前轮 final 全部相同，index 为空，local refs 未变/未 fetch。现存 34 个 PRISM_review Python 进程未动，没有发现 ETALON 路径的活动执行者。旧 raw、archive、失败和 local 科学实现全保留。

A `/root/a_policy`、B `/root/b_research_use`、V `/root/v_final` 实际并行开始；V 先独立固定反例再交槽给 C，后续回场验收。文件表继续有效：B 独占 research 源码（含 runtime.py/calibration.py）及新增 `tests/test_research_use_methods.py`；A 从执行消费端审查/验证，仅新增 `tests/test_research_execution_seams.py`，发现 research 缺陷交 B 修；C 独占产品 skill/reference 与原 host 文件；I 独占共享 MCP/打包/公共文档与本地工程 skill；V 新增 `tests/test_thin_harness_research_use.py`、`tests/fixtures/thin_harness/research_use_acceptance_20261021.json`、`v-research-use-*.json`，不改实现。各线各自维护本轮外部 a/b/c/v 及 DELIVERY 追加节。

候选范围为 `empirical_model` 的完整配对开发记录与独立组/留出边界，以及既有 record/compile/observed-branch 到可靠执行的使用接缝。只按复现修真实缺陷；不新增全局路由器、第二账本、强制研究 DAG 或 method gate。A/B 不同时改 research/runtime.py；尚未分配的源码先登记唯一负责人。工程 skill 沿用本地 contract-refactor，不进入产品。必要测试仅用隔离合成/便宜 CPU 工程任务；R4b 暂停，无 PRISM 新评分/CRO/旧恢复、共享升级部署、提交或推送。

V 协议先冻结为 `96c254e16ad8ae414fb1bcc71bdd58865b044ccbdcf9b830b1833447a074d2e8`，随后 C `/root/c_research_skills` 实际并行补产品 reference 与原生使用；A 完成后 V 回场。B 唯一修复 research/runtime.py 的冻结 bindings 和记录后 artifact 重验，算法未重写；A 实际编译/预检到 planned 为止，没有 worker。B 的组合 27 项包含 A 的 9 项，不累加。V 5 个独立源反例及安装包复核、C 原生原件已核。

原生固定源为 cd99711db238f5c57c1927cc40ff520da7f8a25cd6f798c3002a7a2059c30d5c（1183文件），宿主显式比较默认/组均衡构造后停止；两调用处于同一预写 shell，不冒称跨轮动态换法。全部生产/测试/DELIVERY 已由各唯一负责人冻结；I 最终封存完整源码、manifest/receipt，V 此后仅写本轮目录的外部一致性复核。209 保护文件中207未变，research/runtime.py及其README两项有意修改逐项保留；旧原件不覆盖，未提交推送。

## 接续：按需使用与局部判断效率（2026-10-21）

本轮用户授权和具体任务以 workspace `integration/thin-harness-efficiency-20261021/REQUEST.md`、`COORDINATION.json` 为准。**R 恢复审查职责，本轮成果/实质阻塞向 R 通知一次，覆盖此前不回传约定；常规实现不等 R 逐项审批。** I 已收到并写 i/ACCEPTED.md，已主动 queue E。E 由 R 创建的 chat `01a15c44-6619-7f13-a7e5-8f5e90a095f1`，不重复创建。

共同起点 i/baseline.zip SHA256 `087729eabbff1c89157e58634af393c6428d65f052585fa099e0e971edd03a2c`，1186 文件，与前轮最终源逐字节比对在 i/ENTRY.json；index 为空、local refs 未变。保留全部 dirty/untracked。活动写入者明确为 I 团队及 R/E 的不同路径，初查没有匹配到 life_healthy Python 执行进程。R4b/旧科学证据与共享环境继续暂停边界。

实际先启动 C `/root/c_research_skills`、A `/root/a_policy`、V `/root/v_final` 并行；V 先冻结反例及审 E 评价条件，之后交槽 B 再回场独立验收。唯一归属沿旧表：C `skills.py/host.py/mcp/host.py/HOST.md`、host 指引和产品 bundles，以及新增 `tests/test_discovery_scope.py`；A 新 `examples/local_sdk_batch.py`、`tests/test_local_sdk_batch.py`（仅实际需要时建），原执行/预算结果文件仍归 A；B 原 research/科学 SDK，新增 `tests/test_sdk_usage_examples.py`（需要时建）；I 公共契约/共享注册/打包、i/READY.json 和本地工程 skill；V 新 `tests/test_thin_harness_efficiency.py`、`tests/fixtures/thin_harness/efficiency_acceptance_20261021.json`、`v-efficiency-*.json` 及本轮 v/。

**E 独占** `examples/decision_model_pilot.py`、`tests/test_decision_model_pilot.py`、`docs/thin-harness/DELIVERY_E.md`（按需建）及本轮 e/；I 团队不修改这些文件，E 不修改共享实现/公共文档。REQUEST/COORDINATION 归 R。i/a、i/b、i/c 工程原件分别交 A/B/C 写，公共固定来源和 READY 由 I 单写。

G0/G1/G2 是工程对照组，不是工作线或科学阶段；由 E 先用实际频率证据选场景并冻结评价、V 独立审查。I 实施稳定后交付固定 G1 源/wheel/安装/导入路径和接口并主动通知 E，不以其等待依赖作为完成。局部宿主脚本可组合、分支或不用；没有强制发现/skill/method gate、全局router或任意代码执行 MCP。不重写正确算法以凑 skill；工程 skill 不入产品，未知和失败/费用完整保留，不提交或推送。
