# 契约验收用例

2026-10-21 接续验收：专项约定、证据复用及元 skill 自身改进见 [EVOLUTION_ACCEPTANCE](EVOLUTION_ACCEPTANCE.md)。旧用例仍为兼容基础，不因新增文档自动变成已运行或已通过。

定义日期：2026-10-20；工程启动：2026-10-21。**本文件保留验收标准，不是测试报告；实际已运行、失败、未运行与限制分别见 DELIVERY_A/B/C/I/V。**

用例可以由已有测试覆盖，不要求一条用例对应一个新测试文件。实现线维护自身模块测试，V 负责有区分力的跨模块验收，I 负责最终源码组合。失败应回到唯一文件负责人修复。

## 1. 动作

| ID | 输入／干预 | 必须观察到的结果 | 实现负责人 |
| --- | --- | --- | --- |
| ACT-01 | 支持的候选／端点，`method_use_refs=[]` | 合法执行无需 skill 或 GP 规划；身份与原执行路径一致 | A |
| ACT-02 | 宿主读第一步结果后提出未在原 DAG 中声明的第二动作 | 可以提交或明确因真实能力／资源拒绝；不能仅因“未在 workflow 中”拒绝 | A、C |
| ACT-03 | 同一 workspace/request/plan 连续及断线后重放，再以同 ID 改参数 | 前者同一执行且只计一次；后者 conflict，无第二派发 | A |
| ACT-04 | prepare 后追加方法备注；另一次改变受体输入或执行器版本 | 备注不导致无效；相关变化 stale，未错误派发 | A、B |
| ACT-05 | 两个提交竞争最后预算／重复名额，或已有 pending | 原子预留；只接受合法组合或 busy；不清除 pending，不重复占用 | A |
| ACT-06 | 工具已执行、响应丢失，随后 status/reconcile | 沿原执行核对；不自动提交新计算，实际成本保留 | A |
| ACT-07 | 超支持域分子、历史-only 端点、缺输入或预算不足 | 分别明确拒绝，不能通过 shell 逃逸或假结果冒充能力 | A |
| ACT-08 | 同一可执行动作分别来自旧数值策略和宿主选择 | 通用科学身份、收费、准入一致；旧数值选择／预算保留含义 | A |
| ACT-09 | action 完成后余额减少，再提交同一请求；并检查方法级预算约束 | 能定位原执行重放；不重新报价制造冲突，不静默解除 campaign 承诺 | A |

可复用测试入口包括 `test_active.py`、`test_runtime_service.py`、`test_runtime_recovery_audit.py`、`test_runtime_prism_bridge.py`、`test_design_import.py`。有 fixture 执行证据只支持工程语义，不支持真实分子或实际 PRISM 贯通。

## 2. 结果与成本

| ID | 输入／干预 | 必须观察到的结果 | 实现负责人 |
| --- | --- | --- | --- |
| RES-01 | 执行完成但产物哈希、单位或候选身份错误 | 执行与验证／准入分别表达；原费用不丢失 | A |
| RES-02 | 原生任务明确失败或外部结果未知 | failed 与 unresolved 区分；缺失值不是零；未知预留不当退款 | A |
| RES-03 | 合法终态零命中 | 空结果合法；不会因最小标签数的旧 active 默认而误判所有操作失败 | A |
| RES-04 | 准备 NOT_APPLICABLE，存在真实准备成本但未评分 | 保留不适用及成本，评分／损失为空；显式回退另列 | A、B |
| RES-05 | 同执行收据重复入账；原生与认证为两次实际执行；runtime 又汇总 | 同收据一次、两执行各记；汇总引用不再加一次 | A |
| RES-06 | 报价、测量值和未知 overhead 同时存在 | 分别标 basis／coverage；不同单位不混加，不把报价称实测 | A |
| RES-07 | 取消请求已发但子进程仍活动／状态未知 | 不提前称 cancelled，不释放未知消耗；核对原执行 | A |
| RES-08 | 计算分数、Astra 判断、实验结果、独立评价混合输入 | 按端点／类型／角色消费；假设不当标签；已见反馈不再当未见测试 | B |

可复用 `test_prism_v1.py`、`test_research_preparation.py`、`test_research_journal.py`、`test_research_transfer_feedback.py`、`test_research_validation.py`。测试存在不等于已运行。

## 3. 事件与 hook

| ID | 输入／干预 | 必须观察到的结果 | 实现负责人 |
| --- | --- | --- | --- |
| EVT-01 | 两种日志各超过100条事件，分页、空页后追加、跨日志同序号 | 不漏已提交事件；游标包含日志身份；不混淆序号 | A |
| EVT-02 | 相同事件重放；hook 动作已提交但游标未保存时崩溃 | 同 handler 版本不重复产生付费执行；稳定请求 ID 可核对 | C、A |
| EVT-03 | 结果准入前、提交后及 hook 异常分别注入失败 | 恢复收据仍可靠；普通 hook 不在事务内、不回滚事实 | A、C |
| EVT-04 | evaluation 记录触发改进；改进自己又写研究记录 | 类型过滤防无限自触发；可停止或跳过；遵守已有预算 | C、B |

## 4. 方法版本与使用自由

| ID | 输入／干预 | 必须观察到的结果 | 实现负责人 |
| --- | --- | --- | --- |
| MET-01 | 使用内置六项之外的本地新 bundle | 可显式引用、使用并记录来源；目录不是方法白名单 | C、B |
| MET-02 | 修改或删除 reference/script，旧执行仍在运行 | 新内容版本不同；旧执行仍能定位原快照；不能只记录 SKILL.md | C |
| MET-03 | 只知道目录可见／服务端返回／宿主声明／脚本运行四种情况 | 分开记录证据强度，缺失遥测保持未知，不虚构完整使用 | C、B |
| MET-04 | 不选、部分采用、组合两个、遇反证停止或换方法 | 都可表示且可工作；不要求完整前瞻 DAG 或固定顺序 | C、A |
| MET-05 | 同一方法记录重复写入、修改历史内容、晚到归因 | 相同写入幂等，历史不能改写，晚到信息追加且不伪装提前冻结 | B |
| MET-06 | 固定 S 改 φ，再固定 φ 改 S；方法依赖也改变 | 版本可独立变化；实际联合改变明确标出，依赖不漏记 | C、B |
| MET-07 | 未证明优越的候选 skill／元 skill 在授权范围内试用 | 可以探索；不能自我宣称独立通过或回写评价标准 | C、B |

## 5. 宿主与集成

| ID | 输入／干预 | 必须观察到的结果 | 主验收 |
| --- | --- | --- | --- |
| HOST-01 | 实际宿主发现工具，按需读取方法，直接调用一次，再根据结果改变方法／步骤 | 有真实宿主可观测记录；没有默认第二规划器／全 skill 注入；无原生 hook 时可显式读取事件继续 | V；C/I 配合 |
| INT-01 | 三来源组合、工具注册与费用表 | 本地科学实现保留，main 修复未丢，host 工具可用；唯一注册源、无重复或遗漏费用项 | I、V |
| INT-02 | 非 editable 安装，从 checkout 外工作目录读取 skill 与运行 MCP | 包含完整 references/scripts、科学依赖及版本；实际 import/source 正确 | I、V |
| INT-03 | SDK/MCP/CLI（已有或本次公开入口）调用同语义；旧入口／恢复案例回归 | 关键验证、错误、幂等、准入一致；旧协议与历史可解释；没有第二实现 | I、V |

每种宿主分别报告。只有 Codex 或只有 Claude Code 的真实验证时，不宣称两者均通过；另一宿主不可用不阻塞其他工程交付。一次宿主调用或有限科学 smoke 不证明学习、收敛或实验活性。

## 6. 改进收益：不得用工程测试代替

| ID | 比较设计 | 判定依据 | 责任 |
| --- | --- | --- | --- |
| LEARN-01 | 固定初始 M、宿主模型、工具与预算，S×φ 四组；增加直接工具／便宜静态对照 | 后续独立任务的损失／有效产出、停止适用性、失败和总成本；报告不确定性与联合变化 | V 定评价与独立判定，B/C 提供候选与接口，I 记录范围 |
| LEARN-02 | 相同起点和改进／评价预算，旧 G 与新 G 各自产生后代，再独立评价 | 后代质量、成功率与全部修改／评价成本；不能只看候选数量或自评分 | V、B、C |
| LEARN-03 | 正式学习消费与评价数据暴露演练 | 开发/测试界限真实；未知反事实不补值；使用过的独立反馈只能成为后续已见材料 | V、B |

缺独立任务、真实宿主或实验材料时，可完成评价协议和工程消费检查，结果标 `not_run/blocked` 并具体说明缺项。不把已见 R4/R4b 数据改名为新测试。新科学计算依赖届时实际授权；本文件不解除暂停。

指标、费用范围和判定规则应在看独立结果前确定；没有预先确定的阈值时不得事后挑选使候选胜出的标准。结果可以是无改善或证据不足。RSI、亲和力预测和 CRO 效果分别需要相应范围证据。

## 7. 报告要求

每项结果记录：ID、实际实现组合／模块路径、环境、命令、输入与产物引用、状态、失败／未知及费用边界。覆盖矩阵由各 DELIVERY 引用本用例，不复制出第二套标准。

本次 I 接管公共契约并检查组合一致性，V 独立检查 F01—F10 与用例证据，不等待 R 或回传原 chat。通过结论必须引用实际交付证据，本文定义不能替代产品／科学验收。

## 2026-10-21 接续证据索引

本轮沿用上述 ID 和判据。V 在结果之前另固定原生宿主/真实进程的可观察失败条件于 `tests/fixtures/thin_harness/native_acceptance_20261021.json`；其 SHA256 为 `5525fa623104559ee44ca968844e2ca15070b771cc8c408658f77c2107d09f9a`，不是新的科学评价协议。

| 原 ID | 本轮实际证据入口 | 报告边界 |
| --- | --- | --- |
| HOST-01、ACT-01/02、MET-01/03/04 | DELIVERY_C/V；workspace `integration/thin-harness-native-20261021/c_host/` | 原生 Codex 日志、MCP 返回和原账本对应；Claude 实际 401 单列 blocked |
| MET-02/07 | DELIVERY_C/V 的原生本地 bundle 改版案例 | 保存版本、实际试用与收益分别报告，不从新 hash 推断收益 |
| ACT-05/06、RES-02/05/07 | DELIVERY_A/V；本轮 `a/native-a-evidence-final.tar.gz` | 真实 Linux 进程/锁、原执行收据、取消未知及最终失败费用；原失败保留 |
| INT-01/02/03 | DELIVERY_I/V；本轮 `wheel-source.json`、`installed.json`、`shared-contracts.xml` | 新组合两项生产修改、非 editable 安装、实际导入/stdio，和原生宿主是不同证据 |
| 历史 A32/B5 回归归因 | DELIVERY_A/B/V；本轮 A 逐 ID 对照、`b/ATTRIBUTION.json` | 固定源和适用环境的复现；不删旧失败，不把依赖缺失或平台不支持算作通过 |
| LEARN-01/02 | DELIVERY_V | 仍 not_run；缺新独立任务及等预算对照，本轮原生使用不替代 S/φ/G 的独立收益 |

所有新证据独立保存，第一阶段统计与未运行状态保留为历史记录。最终子项状态以 DELIVERY_V 的独立判定为准；工程授权不恢复 R4b。

## 2026-10-21 功能接缝接续

README 的 FM 编号仅定位原功能，不新增标准或强制研究顺序。当前结果前协议在 `tests/fixtures/thin_harness/function_acceptance_20261021.json`（SHA256 `9ff39fe7c2471c8ba6ec6dc6b9d272f988fa39d3f3e2503eecee0ed3b31106f2`），只读补充另存 `v-functions-readonly-addendum.json`（SHA256 `58f07e174debec05d90d4a17cbe0c4f7d102d491695fcc5b08206714c87e6502`）。

| 原 ID | 本次具体检查与材料 | 不能据此推断 |
| --- | --- | --- |
| ACT-03/08、RES-03、MET-04 | A 真实便宜 CPU 批次、零命中停止及未来 revision；`test_function_capabilities.py` | CPU 未计量成本不是零；返回 exhausted 不抹去原 FAILED 收据 |
| RES-08、MET-04、LEARN-03 | B `test_research_function_methods.py`：既有校准和 `ResearchJournal.factorial`，V 另读真实零命中产物及 failed cell | synthetic contrast/面板不成为新科学结论、因果效应或独立收益 |
| RES-02/07、ACT-06、INT-03 | A/I/V 只读 sweep/Screen/校准接缝，旧 schema、live WAL、坏收据、缺路径、reservation/partial cost | running/unknown fixture 不是本轮真实活动子进程；真实 POSIX 仍引用前轮原件 |
| HOST-01、MET-01—05/07 | C 的独立原生 productivity/monitor/lineage 案例与 V 日志复核，workspace `integration/thin-harness-functions-20261021/c/` | 宿主声明不等于完整读取；父引用和试用不证明方法优越；Claude 仍 blocked |
| INT-01/02 | I 新 wheel/完整包资源、非 editable 安装与 V source/manifest 检查 | 不用包 smoke 代替原生决策；molcascade 三个有意只读改动须逐项解释，不能称受保护文件全部未变 |
| LEARN-01/02 | 维持 not_run | 无新独立任务与等预算对照，不能宣称 S/φ/G 收益 |

本轮具体通过/失败与源快照以各 DELIVERY 和 V 结论为准。旧第一阶段/native 原件不覆盖，新增 README 文本不冒充它们原先的受测源码。

## 2026-10-21 更多 research 方法与旧策略可选性

沿用原 ID；结果前 V 协议为 `tests/fixtures/thin_harness/research_optional_acceptance_20261021.json`，SHA256 `bd346f1093b9d2cff097b885c41b0fb7ac142f19264dd07c72db4b701ef235cb`。新材料独立位于 workspace `integration/thin-harness-research-20261021/`，不替换前轮归档。

| 原 ID | 本次证据入口 | 限定范围 |
| --- | --- | --- |
| ACT-02/08、MET-04、INT-03 | A `test_optional_campaign_methods.py`、DELIVERY_A | 独立建议、单阶段规划、跳过旧 round/tick；真实数值计算与合法校准/支出约束仍保留。人工面板不证明科学收益 |
| RES-08、MET-04、LEARN-03 | B `test_research_optional_methods.py`、V `test_thin_harness_research_optional.py` | 派生 outcome 追加和重放、失败/未知保全、联合支持外停止、共享资源报价/费用；不派发科学任务 |
| HOST-01、MET-03/04 | C 固定源 `native-diagnostic` 原件、V 日志复核 | 正式宿主读取真实 MCP 内容与 shell 内实际 SDK 调用分别记录；不把 SDK 包装成 MCP 科学执行，不推断完整 skill 内化 |
| INT-01/02 | I `wheel-source.json`、`installed.json`、V 包核查 | 新完整 reference 随包/宿主导出可读；与原生宿主行为分别验收 |
| LEARN-01/02 | DELIVERY_V | 仍 not_run；缺新独立任务和等预算对照。Claude 仍为前轮实际 401 blocked，本轮不循环重试 |

固定模型产生的局部数值建议树不是新的宿主 workflow。具体通过与未运行以各 DELIVERY 和最终收据为准；R4b 保持暂停。

## 2026-10-21 research 实际使用接缝

新独立工程材料在 `integration/thin-harness-research-use-20261021/`。V 结果前协议为 `tests/fixtures/thin_harness/research_use_acceptance_20261021.json`，SHA256 `96c254e16ad8ae414fb1bcc71bdd58865b044ccbdcf9b830b1833447a074d2e8`；沿用已有 ID，不新增研究阶段或评价体系。

| 原 ID | 本次检查入口 | 判据与证据边界 |
| --- | --- | --- |
| RES-08、MET-04、LEARN-03 | B `test_research_use_methods.py` | 完整 paired rows、显式独立组、group-balanced 权重、disjoint heldout、缺失 outcome/loss 拒绝；纯模型构造不冒充 Journal 证据准入或独立泛化 |
| ACT-02/08、RES-02/08、INT-03 | A `test_research_execution_seams.py`、B research/runtime.py | 仅编译已选首段、不自动执行未来分支；实际 runtime.plan 输入预检独立于编译。冻结条件、原证据/资源哈希在后续使用仍有效；拒绝不改变费用、pending 或旧记录 |
| RES-08、MET-04 | V `test_thin_harness_research_use.py` | 从独立反例检查条件/资源变化、失败标签、必要与无关依赖、append 与只读边界，不修改被测实现使其通过 |
| HOST-01、MET-03/04、INT-02 | C 原生案例和产品 reference；I/V 新包 | 真实内容读取和实际 SDK 数值返回分开记；编译不是派发、声明不是完整使用，原生与包检查分别给状态 |
| LEARN-01/02 | DELIVERY_V | 仍 not_run；工程 fixture 不成为新独立科学任务，R4b 暂停 |

错误记录、测试失败及前轮原生/POSIX 原件保留。具体运行和判定以各 DELIVERY 及本轮最终收据为准。

## 2026-10-21 按需发现、SDK 说明与局部批读

沿用 HOST-01、INT-01/02/03、MET-04、RES-02/07/08。V 结果前协议为 `tests/fixtures/thin_harness/efficiency_acceptance_20261021.json`，SHA256 `8e1b17e87f2f332c16fcf1c5cc852c9826e699b7a1a4088c5d544cdb1159d8a1`；独立材料位于 workspace `integration/thin-harness-efficiency-20261021/v/`。

- 范围查询与零参数兼容、已知接口无需发现、开放外部改版 bundle；不强制 skill 或方法前置登记。
- 从实际产品 reference 提取并执行 SDK 示例；费用/未知、失败批项与重复执行身份不因摘要丢失，完整原件可核对。
- G0/G1 固定完整源码、wheel、非 editable 安装和两种宿主导出分别核验；真实 MCP 组件调用与原生宿主判断分开记录。
- E 的 G0/G1/G2 结果前协议和输入由 V 另行检查，实际采用/未采用/回退、质量、调用、token、冷启动及费用据原件判定。工程历史任务的重放不是新独立科学测试；LEARN-01/02 仍不能由本轮推定通过。

`i/READY.json` 固定 E 的受测 G1 来源。之后追加的报告、README 与 V 测试只属于收尾快照，不能冒称原 G1 已包含的正文。R 本轮恢复审查，日常实现无需逐项等待 R；R4b 继续暂停。

## 2026-10-21 I2 全量能力库

沿用 INT-01/02/03、HOST-01、MET-01/02/03/04/06/07、RES-08、ACT-03 和 EVT 的语义。V 结果前协议 `tests/fixtures/thin_harness/skill_library_acceptance_20261021.json` SHA256 `da100aeb4ba05280acc8330730dd00883e7eb49f19650ab3772c51596bc5bdd3`；原件在 workspace `integration/thin-harness-skills-20261021/v/`。

- 所有实际存在的产品bundle必须与目录/版本/安装/导出一致，包括六项MolQuarry原源；不以固定数量代替遍历实际内容。旧URI与零参数字段形状保留。
- 空范围不读bundle；混合指定范围只读选中项；summary和按需member仍绑定完整支持文件版本。外部改版/删除支持文件改变新版本，旧快照仍可验证。
- SDK例来自实际实现和受测安装，数据角色、失败、未知、回放幂等、费用和原件不丢失；不强制skill、全流程或方法记录。
- 两种宿主导出分别核对全部成员；Python MCP协议调用不冒称原生宿主。最多两个新原生任务各一次，沿V事前预算，保留失败，不补跑到通过。
- 方法内容S、使用指引、实际采用、任务质量、使用成本与独立改进证据分别报告。已有skip/partial/combine/revise/stop原生证据只在其旧固定范围复用；本轮新证据单列。

具体结果见 DELIVERY_I/V 最新I2节。LEARN-01/02未由本轮执行；未建立生产高频分布或一般效率收益。Claude原生401、Windows符号链接权限及可选依赖未验证项按其实际原因分别记录，不靠另一成功路径替代。
