# V 独立验收交付

2026-10-20，早期协议已确定；最终组合验收尚未运行。V 只修改独立验收文件及本报告，不修改被测实现。

## 起点与边界

使用 I 指定的同一 `F:/life_healthy/ETALON` checkout；共同来源 pins 由 I 的完整基线归档负责。V 已现场读取 dirty/untracked 状态，未暂存、提交或改写原科学代码。当前导入实测：

- Python：`F:\life_healthy\ETALON\.venv\Scripts\python.exe`，3.13.12。
- `etalon.runtime.api`：`F:\life_healthy\ETALON\src\etalon\runtime\api.py`。
- `etalon.research.journal`：`F:\life_healthy\ETALON\src\etalon\research\journal.py`。

命令：`.venv/Scripts/python.exe -c "import sys, etalon, etalon.runtime.api, etalon.research.journal; print(sys.executable); print(sys.version); print(etalon.__file__); print(etalon.runtime.api.__file__); print(etalon.research.journal.__file__)"`，退出码 0。仅确认解释器及当前源码位置，不代表功能通过。

旧 R4/R4b 标签已释放；科学批次保持暂停。本轮不部署、不调用 PRISM、不创建付费科学计算。

## 预先确定的反例

固定输入及判断见 [independent_cases.json](../../tests/fixtures/thin_harness/independent_cases.json)，适用 [ACCEPTANCE](ACCEPTANCE.md) 的原有判定，不另造验收标准。重点：

1. 已完成请求在余额减少后仍能重放；同 ID 改 candidate 或 limit 必须冲突且无新增费用。
2. 方法备注不使准备失效；真实 endpoint／executor 绑定变化必须在派发前失效。
3. 错单位但已执行的结果必须保留执行完成、准入拒绝与实际费用三种事实；未知结果保留预留。
4. 两个日志各 137 事件、17 条分页、空页后追加，以及错误日志游标；不得截断历史或混淆序号。
5. hook 提交后保存游标前崩溃应复用稳定 request ID；异常不回滚既有事实，普通研究备注不无限触发。
6. 非内置 bundle 只修改 reference 或删除 script 都改变版本，原快照可读；内容校验、宿主声明和实际使用分别表达。
7. 自报 `content_verified` 或 `independent_validation` 不足以通过校验；探索不要求先晋升。

接口需求：A 提供准备／提交／读取结果及日志引用的具体 Python 签名、支持的 fixture executor 注册方式；B 提供三类记录构造与字典解析入口；C 提供显式 bundle 快照读取及事件消费／处理身份入口。V 将直接调用公开后端与 MCP，不在验收适配器中补上缺失的校验逻辑。

## 评价协议与当前判定

| 范围 | 当前状态 | 所需证据 |
| --- | --- | --- |
| ACT/RES/EVT/MET 独立工程验收 | not_run | 最终组合接口确定后运行上述固定反例；记录失败而不弱化标准 |
| INT-01/03 | not_run | I 的最终共享注册、来源保全与多入口证据，V 独立复核 |
| INT-02 | not_run | 非 editable 安装，checkout 外 cwd，实际模块路径、资源清单及 MCP 调用 |
| HOST-01 Codex | not_run | 真实宿主发现／读取／调用后改变下一步骤的可观测记录；Python MCP client 不冒充 Codex |
| HOST-01 Claude Code | not_run | 单独的真实 Claude Code 记录；一种宿主不替代另一种 |
| LEARN-01/02 | not_run | 未授权新独立科学任务；无新未暴露任务与等起点比较 |
| LEARN-03 科学独立收益 | not_run | 仅可先做合成数据暴露／角色拒绝工程检查 |

LEARN-01 固定初始 M、宿主／模型／工具和成本范围，比较 S×φ 四组及直接工具／便宜静态对照；主要结果为预定任务损失、有效产出、恰当停止与全部失败／未知／成本，不按“选对 skill”计胜。LEARN-02 固定起点及修改／评价预算比较 G 的后代质量，计入所有候选和失败成本。真实结果出现前必须冻结具体任务、指标、阈值与不确定性计算；当前协议只定义结构，不谎称已完成科学预注册或收益证明。

后续发现失败应发回唯一实现负责人修复，并在此保留原失败、修复组合及复测结果；V 不修改实现或实现方测试来制造通过。

## 2026-10-21 最终组合独立工程核验

接续者 `/root/v_final`。以上早期协议原样保留；下方是本次实际执行结果。V 仅新增两个独立测试文件、本目录 fixtures 和本报告，没有修改实现、实现线测试、PRISM 或历史科学证据。

独立执行命令：

```text
.venv/Scripts/python.exe -m pytest tests/test_thin_harness_contracts.py tests/test_thin_harness_host.py -q --basetemp=F:/life_healthy/ETALON/.agent-local/v-check-01 --junitxml=tests/fixtures/thin_harness/v-independent-first.xml
# 13 passed, 6.08 s

.venv/Scripts/python.exe -m pytest tests/test_thin_harness_contracts.py tests/test_thin_harness_host.py -q --basetemp=F:/life_healthy/ETALON/.agent-local/v-check-02 --junitxml=tests/fixtures/thin_harness/v-independent-final.xml
# 15 passed, 7.39 s

.venv/Scripts/python.exe -m pytest tests/test_thin_harness_contracts.py tests/test_thin_harness_host.py -q --basetemp=F:/life_healthy/ETALON/.agent-local/v-check-04 --junitxml=tests/fixtures/thin_harness/v-independent-resource-final.xml
# 16 passed, 7.30 s
```

第二轮增加了 MCP 实际薄封装和准入前恢复收据两个有区分力案例。第三轮核验 A 收尾发现并修复的既有非标量研究费用兼容问题：先单项 1 passed/1.36 s（`v-resource-cost-first.xml`），再加入后续费用和未知预留干预运行全 16 项。V 所有这些实际运行均没有失败，未删弱断言或改被测实现；A 的原发现与修复证据由 A 保留。解释器为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12；实际五个模块路径均位于本 checkout `src/etalon/`，详见 [v-provenance.json](../../tests/fixtures/thin_harness/v-provenance.json)。

V 复用了 A 的 `fixture_action` 来注入合成 executor、in-process launch 和空 resource lock，复用了 B 的文件/冻结收据 builder。干预与断言由 V 独立编写。真实 SQLite CampaignStore、RuntimeStore、ResearchJournal、事件读取、submit 幂等、费用结算、bundle 文件字节均未 mock。测试因此支持工程语义，不证明跨进程并发锁、Linux detached worker、原生科学工具或宿主选择能力。

| 适用条目 | V 实际检查 | 判定与边界 |
| --- | --- | --- |
| ACT-01/02/03/09 | 无方法执行，临时追加候选，支出后重放，同 ID 改候选/限额拒绝 | passed；两个真实账本 action，原 job 重用且费用不增 |
| ACT-04 | prepare 后追加真实 MethodVersion；分别修改 executor ID、configuration | passed；无关记录不 stale，真实绑定变更在预留前 stale |
| ACT-06、EVT-03 | executor 已返回，准入提交前抛异常；通过原恢复收据 reconcile | passed；先 unresolved/reserved，后同 action 准入，执行次数仍为 1 |
| RES-01/02/05/06 | 错单位、明确失败、未知结果、重放、runtime 费用派生 | passed；执行/验证/准入分开，失败 1.5 fixture-cost 保留，未知保留 2 预留，overhead null，汇总 non-additive |
| RES-05/ACT-09 同账本科研费用兼容 | 既有失败 preparation 费用 0.5→action 费用 1.5→后续独立 resource 费用 0.25；另加未知预留 0.5 | passed；总 spent=2.25，原 action 始终只归因 1.5，读取和重放不重计；未知预留保留且阻止新派发 |
| EVT-01 | 两个真实 campaign 日志各 137 事件，17 分页，空页追加、跨日志同序号 | passed；全部顺序读取，日志身份不同，错误游标拒绝；runtime 日志的组件覆盖由 A 单列 |
| EVT-02/03 | 真 submit 成功后 `_save_cursor` 抛错，游标文件保持旧字节，再次读取/提交 | passed；稳定 request ID 对应同 job，真实 executor 只调用一次、账本费用不增；另测 handler 异常不回滚已提交事实 |
| EVT-04 | 真实 ResearchJournal 的 MethodEvaluation 触发候选 MethodVersion | passed；后续页不会将候选记录再次当评价触发，处理器版本改变请求身份 |
| MET-01/02/03/05/07 | 非内置完整 bundle；仅改 reference、仅删 script；保存旧快照；内容校验→部分使用→active.evaluate | passed；三个内容摘要不同，旧快照仍可核验，未晋升方法可试用，重复记录幂等，篡改保存字节拒绝；实际采用声明仍为 host_reported |
| RES-08、LEARN-03 工程部分 | 自报独立角色、已见同字节改角色；诊断记录/失败费用 | passed；错误独立声明拒绝且账本不变，假设不变成分子标签；不证明宿主外部未接触数据 |
| INT-03 新入口接缝 | 真实 MCP 包装器调用同 A 后端；SDK 重放同 MCP job；错误一致 | passed；MCP 包装器本地调用，非原生宿主；I 另有实际 stdio 和 CLI 证据 |
| INT-01 来源保全 | 独立读 baseline.zip，全 1121 文件逐项 size/SHA；当前缺失检查与重点原科学源码比对 | passed（保全范围）；无基线文件丢失，43 个重点文件与原基线字节相同；功能覆盖须同时看 A/B/I 报告 |
| INT-02 最终非 editable 包 | V 独立三方逐字节比较 checkout/wheel/install 的 325 文件；checkout 外重新导入、读取6 bundle、核验2导出、启动真实 MCP | passed；65 工具注册/费用表相同，MCP 实际读取 reference；仍非原生宿主 |
| HOST-01 Codex / Claude Code | 没有真实宿主发现、选择、调用后改变下一步记录 | not_run，分别保留；stdio 协议与导出不代替任一种宿主 |
| LEARN-01/02、LEARN-03 科学独立收益 | 没有新未见任务或新的科学计算授权 | not_run；早期评价结构保留，旧 R4/R4b 标签不作为新独立材料 |

ACT-05 跨进程竞争、ACT-07 全部支持域、ACT-08 全部旧策略等价、RES-03 零命中、RES-04 不适用以及 RES-07 活动子进程取消并未由 V 的 16 项单独完全覆盖；不能从本表推断整张 ACCEPTANCE 无条件通过。对应既有回归、适用性和未运行平台由 A/I 报告逐项限定。真实原生 PRISM 贯通未运行，R4b 继续暂停。

V 的来源复核中，43 个受保护文件包括 `active/prism.py`、`boundary/prism_v1.py`、`data/design.py`、5 个 molcascade Windows 修复文件，以及 research 中除 B 本轮 `__init__.py/schema.py/journal.py` 外的既有文件。可恢复归档 ZIP 的 SHA256 为 `0149945f75ff9b6c9e0ed8f388b1e343c0a3f4c399d1a2e1af45540dfa51dcc0`。全部输入/输出为工程夹具；没有新增真实付费科学执行，测试 wall time 不是全部研发成本。

F01—F10 的独立检查未发现本轮新增固定科学 workflow、强制 skill 选择、全局 router、后台 agent 主循环、消息总线或第二科学账本。动作仍内部复用旧 durable workflow 的单节点执行；方法/事件接缝均为有限函数调用。这个检查支持当前代码结构和所测行为，不升级为宿主自主研究、科学 RSI 或效果结论。

### 不能合并成“全仓通过”的结果

V 已审读 [DELIVERY_A](DELIVERY_A.md)、[DELIVERY_B](DELIVERY_B.md)、[DELIVERY_C](DELIVERY_C.md) 的原始范围，以下为实现线报告的真实失败/未运行，不计入 V 16 项独立通过：

- A 扩展组为 **404 passed / 32 failed**：30 项缺 POSIX flock，1 项依赖 Unix `/tmp` 绝对路径，1 项创建 symlink 缺 Windows 权限。旧恢复审计中另有 4 项在 POSIX 锁前拒绝。本机不能据此宣称 Linux detached worker、真实并发恢复或活动子进程取消通过；平台验证为 blocked/not_run，实际 pytest 失败记录仍是 failed。
- A 初始真实 CPU 用例遭 Windows 长路径限制，保持实现不变改短临时路径后通过。另一个新测试的多余导入由 A 修复。A 最终非标量费用修复后针对性回归为 **72 passed**。不将这些定位后复测结果改写成“从未失败”。
- B 的精确 research 回归为 **84 passed / 5 skipped**；5 项需要显式 PRISM producer/source snapshot。另一个扩大筛选组有 5 项现有 `learn/conformal.py` 在校验前 import 缺失 sklearn 而失败。没有为本次重构安装依赖或放宽测试。
- C 组件/stdio 最终 **25 passed**（wheel 检查由 I 单列）。真实 Codex/Claude 行为均未运行。

因此本轮可以交付有证据的本地工程功能及受限定的组合验收；不能写“所有契约、所有平台、所有宿主或完整科学 RSI 已通过”。

### 最终安装包及冻结组合

最终包为 `integration/thin-harness-engineering-20261020/dist-final-v2/etalon-0.1.0-py3-none-any.whl`，SHA256 **`6b71961494f11ff0fdbdea692adea1f05f4d2c74712e389c663597656807c9f2`**。V 独立重新计算该 SHA，并读取 I 的 `wheel-source-final.json` 每项映射，检查当前源字节、wheel member 与 `install-final/` 已安装字节三者相等，共 **325 文件**。没有把早期包或收尾修复前候选包当作最终版本。

V 随后从 checkout 外 `F:/life_healthy/integration/thin-harness-engineering-20261020` 工作目录使用原 Python，显式优先导入非 editable `install-final/`，实测五个关键模块均来自该安装目录。实际读取六个完整 bundle，重新核验 I 两个 host 导出的当前源/运行路径/文件清单；另起真实 `python -m etalon.mcp` stdio 进程，完成 initialize、list_tools（65项、唯一且与 costs 集合相同）和 `etalon_skill_read` 读取实际 `references/legacy-workflow.md`。证据为 [v-installed-final.json](../../tests/fixtures/thin_harness/v-installed-final.json)。这是 V 独立实际调用，不只是查看 I 的 passed 字段；Codex/Claude 原生决策仍 not_run。

I 最终冻结源码组合运行结果为 **158 passed / 2 deselected**，73.67 秒。V 独立解析 `integration/thin-harness-engineering-20261020/final-contracts.xml`，确认 tests=158、errors=0、failures=0、skipped=0，含 V 的全部16项；2 deselected 为 I 显式排除的 wheel 命名测试，最终 wheel 采用上方实际非 editable 验证。该158是最终组合的一组结果，不能再把其中 V16 或 A/B/C/I 子组相加后称为新增独立测试数量。

最终判定：**本次薄 harness 本地工程组合在上述范围 passed；真实原生宿主、POSIX 长期执行/取消/恢复的本机验证以及独立科学收益仍有明确 not_run/blocked。** 历史失败、缺依赖与平台限制原样保留。工程交付不解除 R4b 暂停。

## 2026-10-21 原生宿主与 POSIX 继续验收：结果前协议

用户已继续授权原生 HOST-01、既有已授权 Linux 资源上的隔离工程验证，以及 A32/B5 回归归因。本节启动新的证据范围，不回写上一轮 `not_run/blocked` 为历史通过。I 的 `integration/thin-harness-native-20261021/entry-state.json` 显示第一阶段最终归档 1153 文件无变化/缺失、无新增非 ignored 路径、暂存为空；本轮实际版本仍须随各运行留证。

V 在看到本轮原生结果前固定 [native_acceptance_20261021.json](../../tests/fixtures/thin_harness/native_acceptance_20261021.json)，13786 bytes，SHA256 **`5525fa623104559ee44ca968844e2ca15070b771cc8c408658f77c2107d09f9a`**。I 已登记该文件由 V 唯一维护。已用现有 `.venv/Scripts/python.exe` 实际解析 JSON 并核验两宿主、四个 HOST 子项和三个 POSIX 用例，退出码0；这是协议格式检查，不是用例通过。

协议直接发送给 `/root/c_native` 和 `/root/a_execution_resume`，要点如下：

- Codex、Claude Code 分别使用正式原生 CLI。必须保留可见工具请求/实际响应/产物版本；配置、目录导出、Python MCP client、退出0及自报总结不代替真实宿主。仅给任务目标、输入和范围约束，不提供完整调用顺序或后继 DAG。
- 分别判定不用 skill 的实质合法调用、按需/部分/组合方法、本地新 bundle/改版与旧快照，以及根据真实反馈追加步骤、换方法或停止。支持的非付费读取/验证可证明 direct-tool 自由，但不冒充 ACT-01 的实际科学执行。Windows 不支持的执行仍单列 blocked。
- 看到目录、工具返回内容、声明采用、直接观察到脚本/动作是不同证据；没有遥测的实际采用保留 `host_reported`。不要求或伪造宿主完整私有推理，不把行为能力宣称为 φ/S/G 收益。
- ACT-05 用实际独立进程和原 OS 锁/SQLite 事务竞争最后预算或名额；ACT-06 在真实 fixture 执行后丢失控制端响应，以持久收据核对同一次执行；RES-07 在 child 活动/未知时不提前 cancelled 或释放预留，确认退出后才作对应裁决。禁止用空锁、in-process launcher 或假进程状态替代 POSIX 证据。
- Linux 先只读核验既有授权资源/相关活动作业，再用隔离快照和新 fixture workspace。信号仅发送给绑定 PID/start identity/process group 的 fixture 子进程；不动共享环境、旧任务、PRISM 或 R4b。
- A32/B5 按原 test ID/error 逐项区分平台、依赖、已有缺陷或新增回归；保留原失败和修复后复测。skip/deselect 不是修复，未测费用仍未知，重叠测试不累加为独立覆盖。

此时本轮四个 HOST 子项（每种宿主分别）、三个 POSIX 用例及回归最终归因均为 **not_run（V 尚未审查本轮原始结果）**。V 将释放并发槽供 B 处理依赖归因，再由 I 唤醒核验原始 host/process 日志及实际账本。R4b 仍暂停，科学新计算和独立改进收益不在本协议授权范围。


## 2026-10-21 续轮独立最终判定

V 未修改被测实现或 A/B/C 测试，保留事前协议 SHA `5525fa623104559ee44ca968844e2ca15070b771cc8c408658f77c2107d09f9a`。本节结论来自实际原生 prompt/JSONL、Linux 原进程日志/SQLite/收据及包验证，详见 [v-native-review.json](../../tests/fixtures/thin_harness/v-native-review.json) 和 [v-native-package.json](../../tests/fixtures/thin_harness/v-native-package.json)。它补充前一阶段，不把历史 not_run 改成曾经通过。

| 条目 | 判定 | 独立核验与边界 |
| --- | --- | --- |
| HOST-01 / Codex 不用 skill | passed | 正式 Codex CLI 原生 MCP 调用；Windows 合成回放保留被拒准入与 0.5 fixture-cost。另在真实 Linux CPU 完成两次分子量动作，均 method_use_refs=[]，前次完成/准入及余额反馈后追加下一次动作，无预设完整顺序。 |
| HOST-01 / Codex 开放方法与适应 | passed，限所观测行为 | 两个外部 bundle 按需读取短入口，QC 反馈后加入费用审查；本地 receipt-review 改版保留完整旧/新快照，MCP 实际保存版本及部分使用声明。读取、声明、调用分别可见，MethodUse 仍为 host_reported。 |
| HOST-01 / Claude Code | blocked；实际调用 failed | 正式原生 CLI 返回 401 Invalid API key，没有实质 ETALON 调用。可定位或登录状态不等于可用；未读取/迁移凭证，不用 Codex 或 Python 客户端替代 Claude 验收。 |
| ACT-05 / ACT-06 / RES-07 | passed，限隔离 Linux 工程 fixture | 三种真实双进程竞争、SIGKILL 后持久收据恢复、活动 child 取消/未知/退出后显式失败结算；最终组合 33 passed。V 读原 OS 锁/PID 身份/执行计数及 SQLite，未自行重启科学计算。 |
| A 原 32 项归因 | completed；Windows 仍 failed | V 逐 test ID 对照当前 Windows 32 failed 和固定原源码 Linux 250 passed 中的同 32 项：30 POSIX flock、1 Unix /tmp、1 symlink 权限。没有删除断言或宣称 Windows 支持已修复。 |
| B 原 5 项归因 | completed；隔离依赖后 passed | 三固定来源源码一致；V 核查 27 文件 manifest 与原 JUnit。原 5 failed 保留，独立 overlay 后同 5 passed，相关两模块 116 passed（包含该5项）。默认共享 .venv 未补装 sklearn。 |
| 新包 / 共享组合 | passed，限定范围 | V 独立实际安装路径导入、真实 MCP stdio 和 325 文件逐字节核验；I 原 JUnit 84 passed、1 deselected。排除项用真实包检验替代，不计作 pytest 通过。 |
| LEARN-01/02 与科学改进收益 | not_run | 没有新未见任务的独立方法收益、φ/S/G 改进或科学 RSI 证据；R4b 仍暂停。 |

Codex 6 次运行及 Claude 1 次尝试分别保留。原 `codex-discovery` 只读旧 methods 数据库，因此不支持“每次独立数据”主张；后续 `codex-discovery-isolated` 使用真实 SQLite backup 新副本，两个 MCP 查询成功。V 重新计算副本 SHA `0d5b8b9bb9477b4372bf156f26a0797a4620a09c48d61fe9faefe9a8d0a52a66` 及来源 SHA `e41e173e16e94864336ae82f804da09b01c69d12ebaa5e8b027b8e3e72f47c8f`，均与运行前一致。新副本是已见工程输入，不是未见科学数据。

revision 的原快照 `7de1125e8e4e3c206f2e759ee61927a7748108d59988fc08f4a9bdee58fa006b` 与新快照 `0d8090c65f60cad7970b9c51f6e6a0adfe13950f5aa2cbafd47410164a032880` 均独立 verify_snapshot 通过。V 比较原/副本账本，候选、动作、观测、余额及前 13 事件不变，新增两条方法记录。成功新 MethodVersion 的 parents=[]，旧版本只在 change_summary 文本引用；本例不证明结构化父版本绑定，也不证明修订提高任务效果。缺文件读取、错误 evidence_refs 类型、错误事件 kind 和伪造 runtime handle 等初始错误均保留。

Linux Codex 原始归档 `c_host/c-native-fixture.tar.gz` SHA `02be71654fcbb624760918406e4d18a2e688f7c97bb4bc97412bb165ac68f768`，V 核查全部 50 文件摘要，并直接反序列化原 SQLite：两个 runtime job succeeded、两条已准入观测、八条收据。分子量为 46.069 与 78.11399999999999 Da，总计 2 declared_cost，reserved=0；报价不是测量耗时。这组运行绑定第一阶段 Linux 快照；Windows 原生运行绑定当时 checkout，不冒充最终 wheel 上重新运行。CLI 默认实际模型日志未给出，model=null/unknown；token 与夹具费用分开记录，总研发及宿主金额未知。

A 原始归档 `a/native-a-evidence-final.tar.gz` SHA `fc4223c2cee158fde5f1b4c7b329488dafbb2724d0659864831113a49209627c` 独立复核。V 审读 29 个最终 runtime 原 SQLite，核对 receipt.body 摘要、真实竞争者 PID/start identity、唯一执行计数及活动 child 证据。恢复同一 job/action，执行计数为 1；取消后预留保持，child 活着时拒绝 reconcile，退出而无终态收据仍 unknown，明确失败结算后实际费 `0.4234823156148195` 保留且没有观测补零。最终审计工程相关进程为空。

必须保留的失败链为：POSIX v1 **3 passed / 1 failed** 暴露生产 partial 费用双计，A 最小修复；v2 **32 passed**；v3 **32 passed / 1 failed** 暴露“进程已死但 flock 尚未释放”的同步间隙，生产安全拒绝符合预期；v4 **33 passed**。V 审查 v3→v4 差异为有界真实 flock 探测、仅捕获预期 live-owner 拒绝并记录身份，恢复前增加收据/计数不变断言，无空锁或弱化生产检查。最终 checkout 测试与 tar 中 `test_runtime_posix_acceptance_v4.py` 字节一致，SHA `72793173ce7f8c1d42d2ab674e9b14356e657121f7ee64cd364cd8ebd6286fbf`；tar 的无 v4 后缀旧测试另行保留。

另保留原源码 Linux campaign/sweep 初始 **246 passed / 4 failed**（归档缺真实 Git revision），补入原 sources.bundle 元数据后 **250 passed**，不伪造 HEAD。source-v2 ZIP 曾在关闭前误报摘要，旧 manifest 不删；封口摘要实际 `dd5d939594d0698386464994ceb2118a7848fcce06ab83f779891e6cd9f2691d`，V 重新核实。B overlay 首次仍缺传递依赖失败与 C collector 首次误用 facade 状态断言失败也原样留存，均未改写为产品通过。

新 wheel SHA `194f6c97aff8c6ea7ffda19e142a41ab1e77652a36a1ba04fd33a810cdc70f3c`。V 核对 checkout/wheel/install 325 文件完全一致，并从 checkout 外实际导入五个关键模块、读六个完整 bundle、检查两种宿主导出及另起真实 stdio 进程验证 65 个唯一工具和实际 reference 读取。新 service SHA `ffff910947feeb153f535dc8b1e15eb4c8fd07ac54b779ce515dadb2623f50fa`，新 MCP docstring SHA `d2b66f0d78d941b22aba06d36b190a0a8e1409f5cbf2a4803afbe412fecc41d6`。stdlib/包协议验证不替代 Claude 原生调用；不同来源运行不合并为同一快照实验。

本续轮独立结论为：**Codex 所观测开放使用行为、真实 POSIX 可靠性案例及新组合包通过；Claude 认证阻塞，Windows 旧平台限制仍在，独立科学收益未运行。** V 审读最终 A/C/I 报告，未发现需新增生产修复的剩余证据缺口。各组重叠不累加，完整保全/最终源码归档由 I 在文件稳定后封口并另记 receipt；不提交、不推送、不恢复科学批次。


## 2026-10-21 功能去向续轮：结果前独立协议

用户授权沿 README function-migration 推进 FM-05/06/07/08 最小独立能力、FM-09 一个现成 research 方法及 FM-10 原生结构化 parents 追加。V 读取功能表/WORKSTREAMS 及本地指引，核验本轮 baseline.zip SHA `24b76668c45a72bd5c3517ffa6ef5bf9776f90e07a7f37b707bb36d32419cdc8`，1157 文件；本轮证据位于 workspace `integration/thin-harness-functions-20261021/`，R4b 继续暂停。

结果前固定 [function_acceptance_20261021.json](../../tests/fixtures/thin_harness/function_acceptance_20261021.json)，11188 bytes，SHA `9ff39fe7c2471c8ba6ec6dc6b9d272f988fa39d3f3e2503eecee0ed3b31106f2`，直接发 A/C/I。仅沿现有 ACT/RES/MET/HOST/INT/LEARN ID，不新建执行框架。反例覆盖：只筛已有批次/零命中停止不丢费；校准独立调用保留方向/支持/版本约束；生成建议不强制派发全流程；查询 pending 不变更预算/记录；一个真实 research 函数而非方法元数据替代；原生新记录的 structured parents 正确引用旧版本、旧 parents=[] 不改且新 root 仍合法。原生目标不规定完整工具顺序，声明/读取/调用与独立收益分开。

I/A 随后在实现审查发现 status/progress 构造器可能创建目录或迁移旧 pool。V 在看到任何本轮运行结果前追加 [v-functions-readonly-addendum.json](../../tests/fixtures/thin_harness/v-functions-readonly-addendum.json)，明确缺路径查询不得创建目录/DB，旧 pool 查询不得 schema/index 迁移，普通查询不派发/写账/结算。这是具体化只读约束，不改已冻结基础协议或削弱失败条件。

本节所有功能结果仍为 not_run（V 尚未执行或核验本轮结果）。V 先释放槽供 B 开展校准/research 线，后续独立核验原始调用、反例和旧账本保全；不改被测实现或实现线测试来通过。


## 功能续轮独立检查：函数、只读边界与首轮原生证据

V 新增 `tests/test_thin_harness_functions.py`，未改任何被测实现或 A/B/C/I 测试。实际命令为 `.venv/Scripts/python.exe -m pytest tests/test_thin_harness_functions.py -q --basetemp=F:/life_healthy/ETALON/.agent-local/v-fn3 --junitxml=F:/life_healthy/integration/thin-harness-functions-20261021/v/independent-final.xml --tb=short`，**5 passed / 6.22 s**。五项独立干预为：

- 旧 pool 中重复 SMILES、已完成 strict/exhausted 与 relaxed/committed 及未决批次共存；通过真实 MCP 查询保留重复计数、跨 revision 不可比、缺失 etalon 来源和全部数据库字节，不补迁移/索引。
- 损坏 run 文件经真实 progress/calibration MCP 明确拒绝，不伪装 missing，也不创建执行目录或修复数据。
- 真实小 CPU 筛选产生零命中，使用真实 `Screen.open_existing` 读取产物交给真实 calibrate；第一 gate 生存数为 0，但后一失败阶段没有 parent population，因此总体 recall=None，拒绝授权且不派发新计算。不是 B 的 SyntheticPanelReader，也不是科学效果试验。
- 真实 ResearchJournal 中把一个 factorial 格改为有理由的 failed Observation；计算仍 unresolved、interaction/uncertainty=None，原费用/事件及分子观测不改变。
- MCP 对 Artifact 冒充 MethodVersion parent、未知 parent 均原子拒绝，原账本不变；正确 parent 追加成功、旧父记录原样且无关新 root 的 parents=[] 仍合法。

原始失败没有覆盖：首轮 3 passed/2 failed，第二轮 4 passed/1 failed。它们是 V 新测试自身的契约假设错误：误把整体未知 recall 期待为 0、持久 JSON list 与 Python dataclass tuple 直接比较，以及假定 export 有 balance 键。V 按实际契约和原件分别加强“gate 已知零但总体未知”断言、比较原持久记录、独立读取 balance；未改生产实现。XML 三轮均在本轮 v/。只读审计脚本另有缩进/冷导入和 event_cutoff 比较假设错误，修正为显式加载 MolCascade 以及验证三条追加导致 cutoff+3；不算产品失败或产品通过。

来源复核 [v-functions-source-review.json](../../tests/fixtures/thin_harness/v-functions-source-review.json) 记录 Python 3.13.12、解释器及八个实际模块路径/摘要。按本轮 baseline.zip 比较，209 项既有保护范围中 **206 不变，3 项有意修改**：MolCascade runner/cache/artifact-store 增加默认兼容 create 参数、提取原规范 JSON/大小/身份校验读取函数。V 审查的是 baseline→当前差异，原 Windows checkpoint/cache 修复保留；不能写本轮209项全部字节不变。B 的 research/calibrate 数学实现没有变，SDK/reference 的因果、单位、缺格及科学收益限制一致。

安装候选包 `4b366ce39bb27fa8d4120bc6f78b7a3b99a5b19a63f8955e8aff6249b8f5eae0` 已由 V 独立核 checkout/wheel/install **326 文件**，从 checkout 外实际导入五模块，六 bundle/两导出，另起真实 stdio MCP 读新增 factorial reference；证据 [v-functions-package.json](../../tests/fixtures/thin_harness/v-functions-package.json)。这是候选包所测能力，不覆盖下方冷产率调用故障，若后续生产修复则最终包另验。I 首次无隔离构建缺 hatchling 失败保留，临时 build isolation 成功不等于共享环境升级。

固定原生来源 `pre-native-source.zip` SHA `9fbcdc8cae6868a47b39236cfff79a9d5504df29824fa68e8372ea80652bdffd`，V 核对 C 的全部1164源文件摘要。原 prompt 只规定目标、边界和数据，不规定完整工具序列。首三次原生证据独立解析于 [v-functions-native-review.json](../../tests/fixtures/thin_harness/v-functions-native-review.json)：

| 原生案例 | V 判定与原件 |
| --- | --- |
| monitor | passed。四个实际 MCP 查询，无 skill 内容读取；明确 running 只是记录，不是活跃 worker。未知 fixture reserved=2/spent=.25/remaining3.75，0观测；完整数据库 SHA 和 export 前后相同。fixture receipt 的 wall_second 与 fixture-cost 不是已验证单位换算。 |
| lineage | passed。18实际 MCP 调用，恰好新增3方法记录。派生 `methodversion-5f61d7cfe848ef7a3bdfa485702379ae5d9a10a0a0e369a7611575b53298a3fd` 结构化 parent 为旧 `methodversion-8d61881a39f7f5356e556e67d30faf937947a2ba86e698f18d16b9c3b58c3a76`；另有 parents=[] root。V 实际 verify 旧/新完整快照，旧7 metadata body逐字节不变、科学状态和原事件前缀不变，历史原DB SHA不变；使用仍host_reported。 |
| productivity 首次 | failed。真实工具在120秒后超时，没有数值响应。宿主如实停止，不把输入算数当工具结果。CLI退出0和最终文稿不能算数值通过；I使用真实冷MCP另复现timeout，定位在SciPy/NumPy延迟导入，此前直接pytest已载入依赖不能替代该检查。 |

上述3run均由正式 Codex CLI 0.163.0-alpha.10.2 运行，模型默认且未解析，金额未知；fixture数字/报价、token、真实局部walltime不相加冒充总科学成本。C 的初始审计 tuple/list 误报及修正记录保留。Claude既有401仍blocked未重试；FM09真实SDK方法不冒充原生科学调用；LEARN01/02及独立 S/φ/G 收益 not_run。冷调用缺陷收尾与最终包另记，当前不宣称本轮全部通过。


### 冷产率接缝修复及本轮最终判定

原120秒原生超时与20秒协议复现均保留。B 的三个独立进程对照 direct-main、mcp-env-main、mcp-built-main 导入成功，分别检查普通环境、MCP 指定环境及 MCP 组装环境；这些不是 thread/async 对照，因此没有证明 NumPy 普遍损坏或确切底层死锁机制；现有证据只定位实际冷 stdio 路径的导入停滞。A 在 `generate/audit.py` 保持 Wilson 公式及精确零/全成功边界，改用 `-NormalDist().inv_cdf((1-confidence)/2)`，避免为一个标量分位数冷载 SciPy/NumPy；finite `0<confidence<1` 之外明确拒绝。原生第一版与第二版所有生产源码中仅此文件不同，V 独立逐字节核对，不是预热全局科学依赖或重写生成策略。

V 预先新增的 [v-functions-cold-contract.json](../../tests/fixtures/thin_harness/v-functions-cold-contract.json) 未改基础协议。V 另加两个独立反例：多种计数/置信度直到 `nextafter(1,0)` 的合法区间、对称性、宽度单调、精确端点及非法域拒绝；独立子进程完整 allocate 前后都没有载入 numpy/scipy。最终独立测试 **7 passed / 7.02 s**，原5项包含在7项中，不相加。A 的 SciPy 数值对照及旧回归 **69 passed**，V 解析其原 XML；I 的 fresh stdio 用例另证服务器响应后同一会话继续可用。冷路径结果见 [v-functions-cold-final.json](../../tests/fixtures/thin_harness/v-functions-cold-final.json)。

C 新 `native-productivity-v2` 使用独立目录与新固定源；输入字节与旧超时尝试相同，模型仍未解析。V 核验 source-v2 **1168** 文件与归档 SHA `943225fc55c0146c78e4d4ce3e19eb5d86c0433f10a1a31d10c2b08f9fcca24e`。真实原生 run 26.480秒，两个成功 MCP 请求为 capabilities、generation_productivity，无 skill/plan/科学派发。返回零新分子、95% Wilson 区间上界约1.26%、cost_multiplier=null 与 exhausted/retire；宿主据真实反馈停止并选已有库存，明确未来新颖性并非被证明不可能。0.05小时来自合成输入，不是本轮实测科学支出。此例数值能力 **passed**；前一例超时仍 **failed**，不是删除失败重试后称从未出错。监控和lineage两个先前成功案例未无意义重跑，版本适用范围单列。

最终包为 `dist-final/etalon-0.1.0-py3-none-any.whl`，SHA `95ede2d138c18afc6ab4091be9527db31815de48b2074efeea6bee9058409f4c`；之前4b366候选及其报告保留。V 再次核 source/wheel/install-final **326** 文件字节一致，工程 skill 未打包，并在 checkout 外从 install-final 导入五个相关模块。另启动断言未预热 numpy/scipy 的真实 stdio MCP，产率返回正确零新/retire/null，随后 capabilities 成功；安装内非法confidence全部明确拒绝。记录 [v-functions-package-final.json](../../tests/fixtures/thin_harness/v-functions-package-final.json)。I 的10模块/六bundle/65工具/两宿主导出通用包检查是另组工程证据，不替代原生决策或科学效果。

**最终判定：本轮授权的 FM-05—10 最小功能场景通过，限定于实际检查的接口与行为。** FM05实际CPU筛单已有批次/零命中停止/新版本后续批次，FM06实际校准与拒绝gate，FM07独立产率建议及原生反馈停止，FM08只读不创建/迁移/派发及原生未知状态审查，FM09一个真实factorial SDK与按需reference，FM10原生结构化parent追加/旧记录不变/root[]可用。没有据此声称全部旧 Campaign/Supervisor/propose 或每个科学工作流彻底解耦。Claude仍因已有401 **blocked**；完整跨平台行为、真实新PRISM/CRO、LEARN01/02及S/φ/G独立收益 **not_run**。R4b仍暂停，未知/失败/费用来源均保留。

报告和V仓库内证据至此稳定，I随后封口完整源码；最终归档一致性只在workspace本轮外部证据目录追加，以免再次改变已封源码。没有提交、推送或覆盖原归档。


## 2026-10-21 可选 research 接续：结果前协议

用户授权增加2—3个互补现成研究方法及审查 Campaign/Supervisor/propose 实际无关阶段前置。I 的本轮1170文件baseline SHA `aab89b2d5a82571569a87e3e4ddae518f2d74f070d1e119084ec2f95232d31f2` 已由V核ZIP摘要和条目数量；ENTRY记录与上轮final无漂移/index空，并记录现存PRISM_review进程，V不操作这些进程。证据根为workspace `integration/thin-harness-research-20261021/`。

V 先读当前调用口及合法约束，B确认选定 `calibration.bind_outcome`、`diagnostics.condition`、`diagnostics.exact_program`。结果前冻结 [research_optional_acceptance_20261021.json](../../tests/fixtures/thin_harness/research_optional_acceptance_20261021.json)，11625 bytes，SHA `bd346f1093b9d2cff097b885c41b0fb7ac142f19264dd07c72db4b701ef235cb`；只沿原ACT/RES/MET/HOST/INT/LEARN，已直接发A/B/I供C接续。

具体失败条件包括：只看签名就宣称可组合；用假的测量/方法记录满足无关前置；查询构造器创建/迁移/回收/派发；吞错误把非法mutation称成功；删除真正科学gate/revision/handoff/权限/依赖约束。bind_outcome明确是追加派生metadata/event而非只读，重复幂等、拒绝原子且不改观测/费用。condition必须接收实际绑定结果，联合支持外不以边际支持替代。exact_program保留硬预算、共享资源去重、报价与实测差别，返回建议/stop不自动执行。单能力和部分组合均需实际调用，至少一个前输出驱动后输入或反馈停止，不规定完整宿主工具序列。

本轮所有结果目前 **not_run（V尚未见本轮结果）**；方法记录、真实使用与独立科学收益分开。R4b暂停，旧已见标签不改角色；既有PRISM_review任务不重启/终止，不升级共享环境、不评分/CRO或提交推送。V先释放槽供C产品方法与原生任务，后续独立核原始接口/账本/日志/安装包，只改V自有测试与报告。

### 可选 research 接续最终独立判定

上述为结果前状态；本节更新本轮实际验收。冻结协议 SHA `bd346f1093b9d2cff097b885c41b0fb7ac142f19264dd07c72db4b701ef235cb` 未随结果修改。V 自有 `tests/test_thin_harness_research_optional.py` 首次运行 **5 passed / 2.74 s**，无初始失败：两个实际绑定结果各自有边际支持、联合支持为零时返回 model_challenge；共享资源已取得后报价与逐世界费用的计算不扣账；等阈值使用新规则身份且重放仍核原文件哈希；直接数值建议跳过 Campaign/Supervisor/fit/submit 且保留 probe 依赖；错误身份原子拒绝，unsupported 不变零、不凭空产生方法 gate 或标量观测。这些是合成工程反例，不是新科学试验。

V 独立对比 baseline 与固定源 `e88a7ba697bdb0f61e9f7fe9e9bd00415f5dba889e72b76c81d4e3b1ad5604ea`（1175文件）：本轮209定义保护项全部字节不变，含38 research文件；这是相对本轮baseline，保留上轮有意修复的三asset文件。Campaign.loop/propose/Pipeline/Supervisor 去docstring后的可执行 AST 一致，Supervisor 全字节一致。可选性澄清没有删真实权限/校准/依赖约束，也没有改变旧整轮 recipe。Supervisor 显式构造可创建可变存储；只证明既有对象查询不调度，未将其冒称为只读构造器。见 [v-research-source-review.json](../../tests/fixtures/thin_harness/v-research-source-review.json)。

A 原4新+3旧精确节点 **7 passed**，初次错误预期异常及宽选择误入旧 Windows flock 失败原件保留，不当本轮产品失败修复。随后实际非空 Acquisition 使用先前独立 sklearn overlay，36合成行/6 scaffold、真实拟合校准、4候选返回2个建议，另 **1 passed**；Campaign/Supervisor/Screen/Ledger 构造禁用、无文件写入，宿主未执行建议。该测试晚于原生源码封口，属于单独工程证据；不把它说成原生运行或真实独立泛化。B 三函数持久 SDK **7 passed** 与 V 5项各有独立范围，不将其覆盖与集成回归重复累加。whole-panel ranker 改进仍是合成开发选择，不是独立收益。

C 唯一新增原生 Codex 案例实际71.054秒。V 从原 stdout逐行核验第11/16行两次MCP成功请求：capabilities与仅 `campaign/references/finite-diagnostics.md`；第24行真实Python执行宿主自写 helper。它实际调用 bind_outcome，把返回的 `failed` 送入 condition，得到 model_challenge/空后验后跳过 exact_program 并停止。SDK计算属于原生宿主 shell→SDK，不是新增科学MCP工具。该case体现部分读取、实际组合、反馈停止；exact_program 的能力来自本轮独立SDK证据，未在此原生case执行。7个shell命令中1次通配路径搜索 exit1 保留。

V只读核真实SQLite：恰好追加1个probeoutcome metadata及1个event，全部旧record body与event前缀不变；state/candidates/actions/observations不变，预算4、spent=.25、reserved=2、remaining=1.75仍为合成 fixture_credit，不是实测科学费用。failed values={}、likelihood/causal_hypothesis=null未被填零。1175固定源码及导出bundle全部未变，prompt保留原hash。prompt中“A required derived outcome provenance append ... is permitted”对binder选择有轻微引导歧义；虽然同时写明无固定顺序/强制skill，该案例不能作为完全无提示自由选择binder的对照。模型未解析、货币费用未知；原usage为input394435/cached329216/output1921，不能据此推费用。见 [v-research-native-review.json](../../tests/fixtures/thin_harness/v-research-native-review.json)。

新wheel SHA `70a64fa17fa08e9262183354a954bf532ea0462822c9420bd29c400ce5f131ba`，V逐字节核source/wheel/installed **327** 文件相同。在checkout外，用指定 `.venv/Scripts/python.exe` 从本轮 installed 路径导入skills/calibration/diagnostics，并真实调用 `skills.read_member` 读取新finite-diagnostics全文及三函数引用；工程skill未打包。I的10模块/6bundle/65工具/两host导出及真实stdio是补充工程检查，不替代原生证据。见 [v-research-package.json](../../tests/fixtures/thin_harness/v-research-package.json)。

**本轮限定的可选方法组合与旧策略独立能力验收 passed**，没有要求新router、DAG或方法gate。Claude沿用先前真实401的 **blocked** 状态，本轮未重试；新科学评分、旧R4b恢复、LEARN01/02及S/φ/G独立收益、完整旧工作流跨平台重跑均 **not_run**。所有结果只支持上述实际能力与保全边界，不宣称全部科学系统彻底解耦。V仓库内报告与证据至此稳定；I最终封口后，只在workspace外部证据目录追加只读一致性检查。

## 2026-10-21 research 实际使用续轮：结果前协议

新证据根为workspace `integration/thin-harness-research-use-20261021/`。V核验baseline ZIP SHA `089c4589259232649bc6659e296441efb3578c4bcff2c93da7754eb15e9459e2`，1178条目全部与上轮final `7d7777c382dd6ed59197b2391f8075fdccd318c6c9d5cb403c0db242bcf22740` 逐字节一致。ENTRY记录index空、34个PRISM_review Python进程保持不动、无匹配ETALON执行器；这不是恢复科学任务的授权。

已先读实际 `calibration.empirical_model` 与 `runtime.record_program/compile_first_segment/observed_branch`，冻结 [research_use_acceptance_20261021.json](../../tests/fixtures/thin_harness/research_use_acceptance_20261021.json)，9324 bytes，SHA `96c254e16ad8ae414fb1bcc71bdd58865b044ccbdcf9b830b1833447a074d2e8`；仓库外 `v/protocol-frozen.json` 保留同字节副本。仍沿既有ACT/RES/MET/HOST/INT/LEARN，不加管理框架。

失败条件覆盖成对完整性、独立group与artifact角色的区别、group-balanced权重、模型/计划身份和原证据原子准入、只编译根query不执行未来分支、stop/支持外明确拒绝、失败/unknown和费用保全。反馈必须匹配模型实际声明的科学bindings；兼容扩展可显式增加task/method/unit校验，不把未声明身份或primitive名字强映射为额外前置。数值readout经字符串化的旧离散入口，与OutcomeRule阈值产生low/high不是同一语义；不得隐式猜阈值、伪造观测或借同probe_id接入不相干条件。已将这些反例直接发A/B/I。

本轮结果目前 **not_run（协议先于实现与原生结果）**。未来只记录实际用到的方法和接口，保存、编译、执行、准入与独立收益分别判定；不重跑旧已过案例填表。R4b暂停、无新评分/订单/共享部署或依赖升级、无提交推送。V先释放槽供C，后续回场独立审实际接口、原生日志及包，仅写V归属文件。

### research 实际使用续轮最终独立判定

结果前协议保持 SHA `96c254e16ad8ae414fb1bcc71bdd58865b044ccbdcf9b830b1833447a074d2e8`，上段not_run为当时状态。V 自有 `test_thin_harness_research_use.py` 首次 **5 passed / 2.48 s**。两个反例将模型、复用资源和Observation证据分成三个不同文件：先成功编译/读取反馈，再分别篡改模型或资源原文件，重编译相同段与反馈读取均拒绝，原观测仍完整且账本不变；其余检查3/1/1组大小得到1/9×3、1/3、1/3及顺序不变，合法模型但复用资源为独立验证角色时原子拒绝，以及真实反馈返回未来z query仍仅为建议、不compile/submit，数字.2不猜成low。

A保留两类真实缺陷证据：基线隔离原runtime接受同probe_id错误condition；`a/evidence-second.log` 的1 failed/7 passed证明模型原文件改字节后仍能编译。B修复后匹配实际声明的bindings，拒绝错类型/缺readout/无query，并于编译追加事务和反馈读取重验模型与复用资源证据。V审源码不强映射primitive到Experiment.kind，也不把未声明task/method/unit假称已验证；旧签名、直接离散字符串语义保留。A最终9项、B组合27项包含A9及其B新8/旧10，不能重复累加。A真 `runtime.plan` 对独立设计夹具绑定27个输入哈希/单节点；另坏输入真实拒绝。仅证明编译和预检规划，**本轮未运行worker或科学任务**。

固定原生源 SHA `cd99711db238f5c57c1927cc40ff520da7f8a25cd6f798c3002a7a2059c30d5c` 共1183文件。V核926生产文件与当前一致；本轮209定义保护范围中207不变，有意变化仅 `research/runtime.py` 和其 README，runtime SHA `bea93bae34be98dc6c83b4f9f957e500bf0c81a4a73c8b5c71d522bff69e8cd9`。empirical_model/calibration/diagnostics算法未改，molcascade已有Windows修复完整保留。见 [v-research-use-source.json](../../tests/fixtures/thin_harness/v-research-use-source.json)。

C原生Codex新案例70.241秒，V直接核stdout第7/11/13行三次真实MCP成功请求：capabilities、campaign SKILL、paired-models-and-segments reference；第18行宿主shell→真实SDK调用empirical_model两次。默认构造实际拒绝相关组复用，显式group_balanced返回三个原行的.25/.25/.5。两个尝试位于同一宿主自写脚本，替代调用在try/except后无条件执行；证据是自选显式比较及读取真实结果后解释/停止，**不是先收到一次工具响应才重新决定换法的跨工具反馈实验**。未调用record_program、compile_first_segment、observed_branch或科学执行；这些接缝的检查来自独立SDK和runtime规划证据。

V核1183固定源码、prompt、输入、原数据和导出bundle未变。SQLite全部原research body、events、science export不变、无新增记录；预算10/spent=.25/reserved=2/remaining=7.75仍为人工credit。模型构造无Journal访问，不能说它核过artifact准入，也不把declared independent groups/split当实际独立性或科学预注册。宿主关于开发集零损失仅是给定合成模型的描述；未建立独立收益。input143835/cached113152/output1173保留，模型标识与货币费用未知。见 [v-research-use-native.json](../../tests/fixtures/thin_harness/v-research-use-native.json)。

新wheel SHA `dcf842dba633232534ce7aee1532e321ee26c33dcfa593569eb794858a897c14`。V独立核source/wheel/installed **328文件**相同，checkout外实际导入skills/calibration/runtime/diagnostics并读取新增reference四函数说明；工程skill未打包。另在安装包runtime上执行上述两个不同文件篡改反例，**2 passed / 1.23 s**，这是同两项安装验证，不加成7个独立反例。I通用10模块/6bundle/65工具/两host导出与真实stdio另列。见 [v-research-use-package.json](../../tests/fixtures/thin_harness/v-research-use-package.json)。

**本轮限定的配对经验建模、持久计划到首段/反馈接缝及原生部分采用 passed**。已发现失败与修复复测均保留；合法未知与费用未被零填。Claude为此前401 **blocked**，本轮未重试；独立S/φ/G/M收益、真实新科学评分/worker执行、旧R4b恢复均 **not_run**。不宣称全部research功能已使用或所有科学语义自动验证。V仓库内文件稳定，最终封口后仅在workspace外部证据目录做只读一致性核验。

## 2026-10-21 按需使用与效率轮：事前协议及 E 初审

本轮用户恢复R审查和必要线程通知；常规实施不等待逐项审批，不沿用旧不回传约定。已读workspace `integration/thin-harness-efficiency-20261021/REQUEST.md` 与 `COORDINATION.json`，E为既有chat `01a15c44-6619-7f13-a7e5-8f5e90a095f1`，V未另建chat或改E/R文件。I本轮baseline SHA `087729eabbff1c89157e58634af393c6428d65f052585fa099e0e971edd03a2c`，V独立核1186条目全部与前轮final逐字节一致。

结果前冻结 [efficiency_acceptance_20261021.json](../../tests/fixtures/thin_harness/efficiency_acceptance_20261021.json)，9342 bytes，SHA `8e1b17e87f2f332c16fcf1c5cc852c9826e699b7a1a4088c5d544cdb1159d8a1`；本轮 `v/protocol-frozen.json` 同字节。实现反例覆盖默认兼容、scope不触碰其他bundle、摘要保留失败/unknown/预留费用及原件引用、外部改版、无skill直接合法调用、独立SDK例和局部batch的单项失败/实际依赖/幂等。一个脚本内真实条件分支有效，不人为要求多次LLM往返；无条件连续比较不能冒称第二选择由第一次反馈触发。

E当前材料初审为 **partial_pre_review**，见本轮 `v/e-pre-review-initial.json`：V核12来源文件hash、16个去重原始completed调用的逐行内容/哈希，属于2个历史真实native工程task，不是生产频率调查。原extract的source-lookup复合命令也含目录/config读取，不能仅凭类别数归因延迟。E已明确production_high_frequency_established=false；目前材料支持有限pilot动机，不支持普遍高频或独立收益主张。

尚未收到完整E三组协议，不能写预审通过。正式比较前需固定任务损失/语义成功、source-group隔离、有限预算/停止、相同宿主及实际模型设置、输入可见性、执行顺序/缓存与冷暖口径、阈值/拒答/回退及长输入策略。旧unknown-model原生日志不当本轮匹配G0；G1−G0和G2−G1分开，相关变体不当独立样本，所有失败/未采用/准备成本保留。模型实际架构/权重/运行支持待E核验，不由V预写增益结论。当前实现和G0/G1/G2结果 **not_run**；完整协议到齐后V补事前审查，再独立检查原件。R4b仍暂停，无新科学评分/订单、共享升级、提交或推送。V先释放并发槽供B继续实现。

### 效率轮草案预审与首批独立工程反例

随后已审E `e/PROTOCOL_DRAFT.md`，结论为 **conditionally_acceptable_design_pending_freeze**，不是正式比较已通过。新6次匹配运行、同requested host model、两task反向顺序、可选G2和极小样本原值比较的设计可用。冻结前5类具体项已经I交E：协议/六prompt/源与输入身份绑定READY；task特定成功/可接受停止rubric；重定位三组语义等价；预算实际观测和缺失组处理；模型生命周期/冷载/开发调用成本/长输入拒绝。详见本轮 `v/e-protocol-pre-review-v1.json`。设计修订需留存草案，不得用正式结果回调标准。

V新增 `tests/test_thin_harness_efficiency.py` **4 passed / 1.49 s**：scope只解析选中campaign、空scope不读bundle；默认full旧shape与显式全选内容一致；外部新版本直接读且旧snapshot不变；两workspace同job名不混计、单项读取失败与unknown执行分开、原失败费/预留保留且无skill直接调用。首轮3 passed/1 failed是V夹具Windows `write_text`产生CRLF而断言LF的错误，非产品失败；改为明确 `write_bytes` 输入后复测，未改产品。`v/independent-first.xml`及`independent-second.xml`均保留。源侧通过不代替安装后行为或E三组效率结果；后两者此时仍待验。

V随后独立核I READY SHA `1edb4cb89a7d3d7015fe9641878a0930e70db23a06c0c324a3ad56535f821106`：G0完整1186、G1完整1191归档/manifest/固定目录均一致，各自328个源码/wheel/install文件逐字节相同、10模块路径哈希相符。G1 wheel SHA `2d5d314049bee845b7fca1a5e2844146e8e6577cf680cf96b375eb27e21d82b5`。V在checkout外从G1-installed实际调用summary和read_member并核同bundle版本；另外执行安装包两段新reference原字节SDK例，失败支持外停止和经验模型conditioned→stop均成功，费用unknown、evidence_admitted=false等边界保留，见workspace `v/installed-examples.json`。独立包报告为 [v-efficiency-package.json](../../tests/fixtures/thin_harness/v-efficiency-package.json)。

I的两次新stdio进程/六条真实response原件已核size/hash及scope/full展开版本一致；它们不等于原生宿主效率。G1摘要1458 bytes与全量10489 bytes、实际分段耗时仅描述该调用；完整过程初始化另计、文件缓存不可控，不能据此替E宣布端到端收益。A局部batch仍是源码示例，不虚称安装包命令。E正式启动明确使用固定源码PYTHONPATH，不能把I/V安装包组件检查写成E原生使用了安装包。

E第一版FREEZE SHA `bf27a167410d495619e9ddb0d5b37c9774f8864d00089d38607a3834d4540d37` 事前核验见workspace `v/e-freeze-review-initial.json`。25协议材料、56模型文件（含权重）、两固定源码、6run逐项hash和host二进制通过；同任务三组起始DB字节一致、路径差异外inputs相同，历史研究record/event关系在明确重定位后相同。此时6run均未开始，task-specific rubric、角色/费用及输入可见性规定可用。仍待E明确全局Laya40call/600秒预算执行或串行限额办法，不能只靠单次120秒watchdog；依赖下载wire bytes未知，不能认证3GB硬上限。若因此修runner，应保留v1另封v2，不回写冻结文件。此时正式比较许可仍待这一具体运行条件，不阻塞无关产品检查。

E随后保留原 `FREEZE_V1.json`，新增实验局部预算wrapper并封v2 SHA `4dd17d485a08270e2157dfa8929e3d4d24c3d41451583f55b1e7378b1d9075e7`。V给出 **passed_for_bounded_formal_runs**，见workspace `v/e-freeze-review-v2.json`：28协议材料、56模型文件、6个尚未启动目录全核；源/host/model不变，仅两个G2 prompt及binding将可选入口改为wrapper。V独立隔离测试零模型调用，验证pending计入40次上限、未知120秒预留阻止超限、异常保留pending、真实另进程持锁时拒绝且不执行。完成后按全请求wall保守扣量，含冷载，严于纯推理600秒；原smoke79.401秒已计入。未知下载wire仍不能认证3GB硬上限，后续不再下载。此许可仅允许已授权有界比较，不预判其结果。

V检查脚本曾将v2报告写到初审文件名，已更正路径并分开保存；初审报告根据先前工具输出重建，文件自身明确不是原始bytes，详见 `v/review-output-path-correction.json`。E原始v1冻结字节完整不变，模型/产品与实验原件未改。正式运行开始前的独立核验与预算反例结果保持可查。

### 效率轮最终独立判定

**限定工程接口、安装组件和六次原生任务质量 passed；模型两次语义错判 failed；端到端效率收益未建立。** 最终结构化判定见 [v-efficiency-final.json](../../tests/fixtures/thin_harness/v-efficiency-final.json)。上文各阶段的 not_run/待冻结是历史状态，原失败与重建说明不撤销。V没有修改被测实现、E协议、prompt、模型权重、阈值或失败原件。

扩展频率独立核验73个来源文件、111个唯一 completed 原行及其hash，计数与E相符：79 MCP、32 shell，能力发现10次、方法内容11次、SDK定位6次。13启动/12有完成调用会话与6个保守任务组仅描述选定历史工程验收，不能推出生产高频。记录在本轮 `v/frequency-independent.json`。

V逐项读取六份原生stdout、实际SDK helper、返回产物、final及SQLite，并在完成后再次核28个冻结协议文件、G0全部1186/G1全部1191源码、各组非数据库输入/guidance和所有原artifact字节。实际顺序与冻结一致，每项300秒/24调用/400000 input/8000 output及全局可见预算均未超限；旧research body、事件前缀、科学状态和费用/预留保全。diagnostic每组仅追加一个合法probeoutcome，真实failed→model_challenge后停止，exact_program分支未进入；paired每组均实际保留默认相关行拒绝，再以明示等组假设返回.25/.25/.5后停止。三项paired的比较在同一预写脚本内，不冒称跨工具反馈换法。按结果前rubric六项loss均为0，不因未采用skill/model扣分。实际读取未见历史答案或其他组结果。

| 组（两任务合计） | 端到端秒 | 完成工具调用 | input token | output token | 实际采用 |
| --- | ---: | ---: | ---: | ---: | --- |
| G0 | 213.634 | 17 | 374802 | 4069 | 两次读取旧MCP能力/方法内容 |
| G1 | 264.462 | 17 | 380185 | 4359 | 两次直接读SDK源码，无skill/capabilities |
| G2 | 224.018 | 13 | 375599 | 3909 | paired读本地skill/reference，两次均跳过Laya |

六次共702.115秒、47个完成工具调用，保留3次shell非零退出及恢复成本。input 1130586，cached 882688是其中子集，output 12337；金额与实际后端revision未知，不从token推算账单。固定requested `gpt-6.1-sol`/medium、Codex `0.163.0-alpha.10.2`；E真实运行导入固定源码，非安装包。两项已见工程目标每条件仅一次，OS/API缓存不受控：G1未显示端到端提速，G2的较少调用也不能归因未使用的Laya。组件摘要缩小仍只是组件结果，不抵消此原生负结果。详见 `v/native-final-accounting.json`、`v/semantic-review.json`；E RESULTS各项与独立核验相符。

人工Laya案例逐条按冻结输入/预期核验，batch/unavailable原始stdout和stderr SHA、invocations与预算记录一致，56个模型文件仍为冻结版本。官方固定ModernBERT-large模型CPU两线程，开发语义 **1/2**、评价语义 **5/6** 正确；评价否定对同属一个组，6条只有5个组，不能当独立准确率估计。`dev_unknown`在身份/结果缺失时选择`compare`（answer_confidence=.9834）；`eval_batch`把成功/错误/不适用混合批次选择`success`（.8225）。这些实质错误保留，不能用自报概率或事后阈值把它们变成通过。两个长度案例均未forward而返回not_applicable/fallback；一个显式缺checkpoint真实FileNotFoundError并回退，属于工程边界通过，不是正确语义预测，也不是实际服务事故。

含公开smoke共12请求、9次实际forward，正式人工case其中8次forward；保守wall预算扣113.926560秒，pending为空，失败6.015804秒仍计入。人工主batch外部32.230秒、缺checkpoint过程8.587秒；首case含冷导入/加载外部24.387秒，后续warm请求约.77–1.01秒。全部forward时间含smoke7.728848秒，模型报告进程峰值RSS 2893242368 bytes；准备945.256秒另列。上述不同层次时间不混加，也不把warm forward当冷启动总成本。电费、宿主金额、依赖下载wire未知；模型字段external_charge=0不代表整体免费，无法认证历史3GB下载硬上限。见 `v/model-final-review.json`。

判定：保留经过验证的可选按需接口、独立SDK例和局部读取batch；**不默认启用Laya，不让其承担结果或科学准入，不宣称本轮产生宿主/科学收益**。原生G2的真实采用为0/2，人工反例不能替代采用后的效果验证。Jev、新科学评分/订单、R4b恢复、Claude本轮重试均not_run；旧Claude401不被覆盖为成功。S/φ/M/G独立收益未建立，无共享环境升级、提交或推送。V仓库内材料至此稳定；I最终封口后V仅在仓库外做归档/manifest/live和receipt一致性检查。

## 2026-10-21 I2 全量 skill 库：V 事前冻结

V `/root/v_acceptance` 已实际读取本轮 REQUEST、本地指引、当前 WORKSTREAMS、三契约、README/ACCEPTANCE 与前轮 R_REVIEW，先检查旧验收接缝，未运行本轮实现或原生结果。旧 `test_thin_harness_efficiency.py` 固定六项断言会与本轮可扩展目录冲突，已向 I 报告，由 I 登记并做最小兼容更新；V 不改被测实现使其通过。

结果前协议 [skill_library_acceptance_20261021.json](../../tests/fixtures/thin_harness/skill_library_acceptance_20261021.json)，SHA256 `da100aeb4ba05280acc8330730dd00883e7eb49f19650ab3772c51596bc5bdd3`。同字节原件与冻结收据在 workspace `integration/thin-harness-skills-20261021/v/`。基线引用 I 保存的 `baseline.zip` SHA `781095cb26f291feda07ccf27c3938f3bed8f576a0d1fbe63ec40699d00c922d`；此处是引用，尚未完成 V 全归档逐字节复核。

判据覆盖六项 MolQuarry 原来源/旧 URI、所有公开能力族映射、零参兼容、按范围不读无关 bundle、完整 supporting-member 版本、开放改版、Codex/Claude 双导出与非 editable 安装。工程检查须核实际字节和真实接口，不能以固定目录数量或文档迁移宣称完成；旧 finite/paired/factorial 算法直接复用。

只预定 **最多 2 次新增原生任务**：隔离 design 双交付中拒绝坏身份而保留合法结构输入；外部局部方法修订并实际审阅缺失/失败/假设三类新合成记录。每次 300 秒/24 次 shell 或 MCP/400000 input/8000 output，两次累计 800000 input/16000 output；失败保留，不补跑到通过。协议给 C 输入准备、任务目标和可观察判据；native 宿主只见目标与输入，不见判据、答案或强制工具顺序。可不用、部分用、组合或改版 skill，不以采用率作为任务成功指标。C 可复用已有 runner，但需独立冻结本轮 prompt/inputs/source/install/host，不能覆盖旧原件。

已核实际解释器 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，`etalon` 与 `skills` 从当前 `F:/life_healthy/ETALON/src/etalon/` 导入；此项只是环境定位，不是实现通过。当前工程/新原生结果 **not_run**，S/使用方法独立收益 **not_run**，Claude 历史 401 仍独立 blocked，R4b 继续暂停。V 先释放槽给 C，待固定新来源和实际原件后回归独立复核。

### I2 全量库独立结果

**限定工程覆盖、按需读取、版本、安装与导出通过；两个原生任务语义通过，其中 design 单次 token 预算失败。** 原件见 workspace `integration/thin-harness-skills-20261021/v/engineering-review.json`、`source-package-input-audit.json`、`installed-probe.json` 与 `native-independent-accounting.json`。上方事前 `not_run` 保留为历史状态；V 未更改协议或产品实现使其通过。

V 独立验证 1196 条 baseline 与 1223 条受测来源全部归档条目/哈希及解压字节；受测来源 SHA `1101f873e2938570bd035781dc94f119ab3dbcd2d4cd7b5a9335f786f54edafc`，wheel SHA `4c04e2b318b2d97542070073a2f4c1aa258f07bca5158469ab3dd2669d75498b`，945 个实际 package member 与源码、安装逐字节一致。1196 个旧路径没有删除，MolQuarry 六源字节未改；领域算法不因此次库扩展重写。README 末尾能力族映射覆盖 data/design、research transfer/validation/feedback/pose/preparation/fixed_pool/online、原操作与方法版本/事件/改进，引用真实入口并说明副作用与未验证边界；历史 retained-baseline 的 R4b 硬绑定已明确，不伪装通用驱动。

新增 [test_thin_harness_skill_library.py](../../tests/test_thin_harness_skill_library.py) **8 passed / 9.25 s**：原来源全覆盖与零参字段兼容、空/混合 scope 不读无关 bundle、开放外部 supporting 文件删除的版本变化/旧快照不变、双宿主完整导出与漂移、旧 MolQuarry URI/新主入口一致、无 skill 的设计导入及部分 research、合法状态存在时坏 bundle 拒绝。首轮 **4 failed / 4 passed** 是 V 夹具错误：假定 assay-literature 含 references/workflow.md；改选实际含 supporting 文件的 target-modulators 后复测，未改产品或降低判据，两份 XML 均保留。新 V 测试是受测 v1 之后的独立检查源码，不声称它原已包含在 v1 中。

仓库外绑定 `i/installed-v1` 实际导入十个模块，运行所装 research script 与 pose/governance/data/revision 四段 reference 原例；仅供应所声明的绝对路径/Journal 等真实合成前提。零网络 data preparation、旧新 method parent 与 host_reported 归因、零观测、15 项 bundle 及 Codex/Claude 双导出通过。纯 API、合成回调和方法记录不升级为科学执行、完整原生采用或收益。I 的实际 stdio/旧 URI 原件及其 source/install 绑定已核对；Claude 导出可用与历史 native 401 阻塞分别记录。

V 独立核五个 runner 冻结文件、每任务953个安装文件、design54个和revision3个保护输入及 prompt/binding/来源/wheel，运行后保护输入/安装字节仍匹配。原生宿主仅两次，requested `gpt-6.1-sol`/medium，实际后端 revision 未知；无重复尝试。独立重算 stdout usage、completed shell/MCP、exit、工具脚本与产物，并只读核 design SQLite 状态。具体结果如下：

| 新工程任务 | 语义／实际行为 | 预算与成本 |
| --- | --- | --- |
| design-quarantine | **passed**：直接 installed SDK；A 加1结构候选、2 poses/4 slots，B 原hash错误被隔离；零观测/动作、余额不变、replay无新事件；原 FAILED/未尝试槽及假设角色保留。未观察到 skill/reference 或 MCP 读取 | **failed**：157.423 s、10 shell、443159 input（cached359296）、5111 output。超过冻结单次400000 input；turn.completed后才得到usage，runner以input_tokens终止，exit15。final.txt未落盘，stdout final及完整实际产物保留。没有补跑或提高上限 |
| local-method-revision | **passed**：自由创建外部修订，保全两文件原件/快照并产生完整新版本；真实review区分缺失、失败与假设，保留0.75 fixture_credit失败成本，总成本未知。V重算旧/新完整内容摘要不同；未读内置skill或调用ETALON SDK/MCP。实际文件/产物可观察，具体采用归因为host_reported，不能证明完整内部推理 | **passed**：72.505 s、4 shell、76523 input（cached60672）、2431 output、exit0；runner将可用input自动降至第一次后的累计余量356841 |

总计 **229.928 s、14 shell、0 MCP、519682 input、419968 cached input、7542 output**；累计未超800000/16000，但累计通过不能抵消第一次单次超限。全部 shell 返回0，runner预算终止另列；token仅在回合结束可观测，不能认证硬token截止。金额、宿主推理请求往返数和实际模型revision未知；CPU未测不补零。design读取两个完整bundle及多个SDK源码，累计工具返回92376 UTF-8 bytes；这仅定位可后续改进的查找/读取成本，不证明其造成全部延迟。

当前可采纳结论是完整可选库与独立 SDK 接缝已通过工程验证，且真实宿主仍可完全跳过内置方法或自由修订外部内容。两任务 **内置 skill/reference 实际采用均未观察到**，不能宣称新增方法已自然采用、提高效率或带来独立 S/使用策略/G 收益；第二任务本身是局部开发修订，不是后续独立评价。R4b、封存标签、科学评分/旧恢复、共享升级、commit/push 均未推进。V 产品测试/本交付已稳定；最终封口归档由 I 制作，V 后续仅检查新原件一致性。
