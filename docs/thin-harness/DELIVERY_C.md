# C 产品 skill、宿主与事件消费交付

2026-10-21。C 已实现并完成组件与 stdio 检查；独立最终组合判定由 V 负责。

## 来源、文件与环境

复用 `F:/life_healthy/ETALON` checkout，起点 HEAD `5927aceb346475a6f97550f561e7eb3b291611da`；I 已保全完整基线至 `integration/thin-harness-engineering-20261020/`。C 逐文件导入 thin `d3889e6c0600605f018fb81ce3df1b420d6dcbf9` 的产品 skill 短入口、完整 references/scripts、skills.py、host.py、HOST.md、HOST_SETUP.md、mcp/host.py 和 test_host_capabilities.py。现场 `git diff 5927ace main -- .claude/skills` 为空；该范围无需再覆写一遍 main。没有目录级覆盖、stash、reset、暂存、提交或推送。

C 增量：开放 bundle 引用与真实快照；使用记录薄适配；同步一页事件消费；宿主说明；`tests/test_host_bundles_hooks.py`（I 已登记 C 所有权）。共享注册、打包由 I 维护；research 验证由 B 维护；执行/事件读取由 A 维护。

实际解释器 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12。实测导入路径：

- `F:/life_healthy/ETALON/src/etalon/skills.py`
- `F:/life_healthy/ETALON/src/etalon/host.py`
- `F:/life_healthy/ETALON/src/etalon/mcp/host.py`

## 公共接口与接缝

- `skills.snapshot(slug, *, bundle_ref=None)` / `read_member(slug, member="SKILL.md", *, bundle_ref=None)`：内置调用兼容；外部 `bundle_ref={"root": absolute_dir, "manifest_sha256": optional_expected_digest}`。MCP `etalon_skill_read` 的外部引用要求 expected digest。只读取合法 SKILL bundle 的精确成员；拒绝重定向、穿越、无效元数据和超限内容。上限 512 项、256 KiB/文件、4 MiB/包。
- `skills.persist_snapshot(slug, target, *, bundle_ref=None)`：只创建新目录，保留全部实际字节，返回 `{"manifest_path": absolute_json, "manifest_sha256": digest}`。`verify_snapshot(content_ref)` 校验真实清单与字节；B 在 content_verified 记录入口直接调用。沿用薄分支 manifest 算法；不把记录 ID 与内容版本混同。
- `host.record_method_use(journal, **fields)`：直接构造 B 的 MethodUse 并调用原 ResearchJournal.record；无新账本。空选用、局部使用、组合和停止通过既有字段表示；仅目录可见/返回内容不标 tool_observed。探索不要求晋升。
- `host.hook_request_id(event_ref, handler_method_version)`：稳定派生 request ID。
- `host.consume_events(log_ref, *, state_path, handler_method_version, handler, event_types, record_types=(), limit=100)`：消费 A 的 `runtime.api.read_events` 一页；handler 接收 `(event, stable_request_id)`。状态绑定 log_ref、log_id、handler 版本和过滤条件；OS 文件锁限定单写者，逐事件原子保存。研究事件必须给 schema 过滤器；空过滤器表示主动跳过。异常不改科学事实、不自动重试或恢复科学批次。

Hook 提交成功但保存游标失败时会再次调用 handler；handler 必须以同一派生 ID 和同一动作语义提交给 A。其他副作用也需调用方自行幂等。这里承诺至少一次读取及稳定请求身份，不声称分布式 exactly-once。原恢复收据 observer 未修改。

产品 HOST 仍为短入口，HOST_SETUP 保存详细样例。导出只写新目录，Codex/Claude 源同源，旧 bundle 快照可选回；不将本机工程 skill 注册到产品目录。Codex 配置资料已按 OpenAI Docs 核对官方 [MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 和 [skills 文档](https://learn.chatgpt.com/docs/build-skills)。配置可解析不等于原生宿主已使用。

## 实际验证与边界

| 命令/范围 | 结果 | 证据等级 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest -q tests/test_host_bundles_hooks.py tests/test_host_capabilities.py -k 'not wheel and not stdio and not cost_index'` | passed：20 tests（早期接口阶段） | 组件夹具、导出完整性 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_host_capabilities.py tests/test_host_bundles_hooks.py` | passed：24，skipped：1（增加最后接缝测试之前） | stdio + 组件；wheel因hatchling不可导入跳过 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_host_bundles_hooks.py -k content_snapshot` | passed：1 | 真实本地SQLite工程fixture；snapshot→MethodVersion→MethodUse |
| `.venv/Scripts/python.exe -m pytest -q -rs tests/test_host_capabilities.py tests/test_host_bundles_hooks.py -k 'not wheel'` | passed：25，deselected：1，11.98秒 | 最后C组件组合；wheel由I独立统一检查 |
| HOST-01 Codex 原生宿主 | not_run | Python MCP client 不能替代 Codex 原生发现/选用 |
| HOST-01 Claude Code 原生宿主 | not_run | 独立宿主记录未产生 |
| 真实科学计算、LEARN-01/02独立收益 | not_run | 未运行PRISM、未使用旧R4b充当未知任务 |

MET-01/02/06/07 的内容版本与探索工程能力有组件检查；MET-03/04 有记录接口和选用能力检查，不证明完整宿主内部采用。EVT-02/03/04 覆盖稳定ID、游标未存崩溃、已提交事实保留及schema过滤；其中派发幂等模型使用明确标记fixture，跨 A 真实动作幂等贯通由 V/I 最终检查。MET-05 的不可变记录和评价角色由 B/V 负责。

本次25项没有测试失败。工程测试产生临时文件和fixture SQLite；未新增付费科学费用、CRO订单或共享环境部署。未知科学效果、未运行宿主行为及未测总开发成本不填0。R4b保持暂停。

## 保留与回退

C 没有修改 PRISM、历史科学原件、本地 molcascade 修复或共享科学进程。外部快照与宿主导出不覆盖旧版本；恢复代码时按 I 的完整基线逐文件恢复 C 文件，并保留本次与旧快照，不重置整个 checkout。依赖接口变化通过对应所有者修复，不修改 V 验收使其通过。最终wheel、跨入口和独立组合结论以 DELIVERY_I/V 为准。

## 2026-10-21 接续：真实原生宿主与 Linux action

本节追加原生行为证据，保留上方第一阶段 `not_run` 的历史含义。C 接续者 `/root/c_native`；I/V 事先固定的判据为 `tests/fixtures/thin_harness/native_acceptance_20261021.json`，SHA256 `5525fa623104559ee44ca968844e2ca15070b771cc8c408658f77c2107d09f9a`。没有改判据、旧原件或被测实现来制造通过。

共同起点仍为 I 保存的 1153 文件最终组合、本地 `main`/HEAD `5927aceb346475a6f97550f561e7eb3b291611da`。本轮 C 的产品实现未修改；仅追加本报告，并在独占 `integration/thin-harness-native-20261021/c_host/` 保存独立导出、输入、一次性准备/采集脚本、原生 JSONL 与完整原件。A 的连接辅助程序自动保存的 `a/c-native-*` 新文件也已由 I 登记归 C。共享 MCP 文档修复归 I；远端运行仍绑定 A 创建的固定源码快照，不覆盖旧科学部署。

### 实际宿主、运行条件与身份

- Codex `0.163.0-alpha.10.2`：`C:/Users/21734/AppData/Local/OpenAI/Codex/bin/896a0166b774037a/codex.exe exec --json`。初始 `login status` 显示 ChatGPT 登录；6 个真正原生进程均正常完成。使用 `--ignore-user-config --ignore-rules --ephemeral` 与单次 `-c mcp_servers.etalon.*`，没有改 `CODEX_HOME`、复制认证文件或修改全局 MCP。模型使用 CLI 默认；该 JSONL 没有输出解析后的模型 ID，因此记录 `model=null`，不根据当前宿主或宣传名称推测。
- Claude Code `2.1.150`：`C:/Users/21734/.local/bin/claude.exe -p --output-format stream-json`，独立 `.mcp.json`、`--strict-mcp-config --setting-sources "" --no-session-persistence`。`auth status` 显示已有环境 API key，但真正原生请求返回 **401 `Invalid API key`**、退出码 1；初始化报告 `claude-opus-4-7[1m]`。该宿主的实际方法调用为 **blocked**，不是通过；没有读取/输出凭证、修复全局认证或循环重试。
- 本地 ETALON：`F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12，模块来自 `F:/life_healthy/ETALON/src/etalon/`。每次导出和调用配置留在各 run 目录。Linux 原生 action 使用已授权主机的新目录 `/root/zx/prism-minimal-20261017/engineering/etalon-thin-native-20261021-01/`；Python `env/bin/python` 为 3.11.17，模块来自其 `source/src/etalon/`。环境/源码现场核验与依赖条件见 A 交付。
- Linux MCP 为真正 `python -m etalon.mcp`。`ssh_mcp.py` 只转发标准输入/输出字节，不解析 JSON-RPC、不调用 SDK、不合成响应。宿主正式 CLI 实际发现和调用远端工具。源码哈希及模块实际路径在远端原件的 `final-capture.json`；例如 `runtime/api.py` SHA256 `8c15c65c71e2529484ebcea34dd7ede1e5d530c86bbb3f1de84294c3714f03d6`、`runtime/executors.py` `06832fbe751d9598633bb01962d3a085a33324d1ce6a24a5825466bd7bc55e6a`。Linux 固定快照的 MCP docstring 是修订前版本；后述本地 discovery/revision 进程使用 I 的修订版本。

本地原生进程从 checkout 直接导入 MCP，不是最终 wheel 安装。`discovery` 和 `discovery-isolated` 的 `trial-inputs.json` 保存启动前 `mcp/runtime.py` 内容哈希；早期 direct/methods 未逐模块冻结运行前哈希，不能把后来当前文件哈希冒充其原始绑定。本轮 A 的 service 修改、I 的 docstring 修改与本地进程启动存在不同时间点；本地回放没有使用 durable service。Linux CPU case 则明确绑定上述远端 first-phase 固定快照。I/V 最终 wheel 校验和原生行为证据是两个独立验证范围，不声称所有原生调用来自最终 wheel。

CLI 资料以实际 `--help` 和官方 [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[Claude CLI](https://code.claude.com/docs/en/cli-reference)、[Claude MCP](https://code.claude.com/docs/en/mcp) 核对。配置或登录状态本身没有被计为行为成功。

### 已执行案例与保留失败

所有 prompt 先落盘并保存 SHA，再启动宿主，只提供目标、输入语法和资源/停止边界，没有提供完整强制工具顺序或预写下一步答案。原始 `stdout.jsonl`、`stderr.log`、`launch.json`、`exit.json`、`prompt.txt` 保存在对应目录；[native-summary.json](../../../integration/thin-harness-native-20261021/c_host/native-summary.json) 是逐行派生索引，不替代原始日志。

| 原生 run / 条目 | 实际结果 | 判定范围 |
| --- | --- | --- |
| `codex-direct`，HOST-01 no-skill | 未读 skill，直接通过 MCP 完成一次限额 offline replay；真实 SQLite 记录 fixture-1 的 −11、QC `F_BUILD_INCOMPLETE` 拒绝准入。宿主随后读状态/事件并停止，保留支出 0.5 fixture-cost、reserved 0。 | passed：无 skill 的实质原生操作和反馈后停止；是合成回放，不冒充 durable action 或科学计算。首次状态读 `FileNotFound`、一次错误 log kind 都保留。 |
| `codex-methods`，HOST-01 / MET-01/03/04/07 | 先只读外部 `receipt-review/SKILL.md`，QC 反馈后才读 `cost-review/SKILL.md` 并部分组合；未读两 bundle 的无关 references。真实 MCP 追加 2 个 content-verified MethodVersion、1 个 host-reported MethodUse。 | passed：开放 bundle、按需读取、部分组合、失败收费和适当停止。目录可见、内容返回、声明采用及工具行为分开。另保留首次数据库不存在、两次错误事件 kind、一次错误 result handle 共 4 个错误返回。 |
| `codex-linux`，HOST-01 / ACT-01/02 | 宿主自行选 ethanol，正式 prepare/submit/result 后得到 46.069 Da、completed / passed / accepted；看到余额 1、pending 空后才增加 benzene 动作，得到 78.114 Da，同样准入。两请求 `method_use_refs=[]`，两轮 `selection=host`、`model=null`。 | passed：原生宿主经真实 Linux detached worker 的 2 个便宜 CPU MW 动作。最终 spent 2 **declared_cost**、reserved/remaining 0、无 pending；费用是 executor quote-based 声明成本，不能称测得 CPU/模型成本。 |
| `codex-discovery`，HOST-01 输入发现性修复复测 | 在 I 修订工具说明后，新原生进程只调用 status/events 两次，首次参数均合法；只读解释 QC 拒绝和实际支出，未编造 runtime handle。 | 行为 passed，但本次只读引用 `codex-methods` 原数据库，未满足每次独立测试数据要求；前后 SHA 相同仍不能补作独立数据副本。历史试验保留，随后补运行下一行。 |
| `codex-discovery-isolated`，同目标独立数据复测 | 新导出/配置、通过 SQLite backup 取得新独立数据库副本；记录来源与副本哈希。真实原生只调用 status/events 两次，均首次成功；正确报告拒绝原因与保留支出后停止。 | passed；副本及来源数据库前后 SHA 均不变。数据是已见工程材料的新副本，不是独立科学任务；单次成功不证明总体错误率下降。 |
| `codex-revision`，MET-02/06/07 / HOST-01 | 宿主根据已发生误用修改本地 receipt-review 候选，新增有关执行/准入、offline action ID 与 durable handle 的区别；保存新 snapshot、MCP 读取新内容并用于只读审计，追加 1 个新版本和 1 个 host-reported 使用记录。 | passed：实际本地改版/读取/探索试用，旧 snapshot 可核验、原版本引用保留。首次缺失 live manifest 的文件读取和一次错误 `evidence_refs` 类型写入均保留，后者被拒绝后宿主自行简化正确记录。无新科学执行。 |
| `claude-direct`，HOST-01 Claude | 真 CLI 发起请求后 401，未产生成功 ETALON 调用。 | blocked：需要可用的原生 Claude 认证。后续 Claude methods/revision/Linux 均 not_run，不由 Codex 替代。 |

Linux 两个 request/job ID 是 `codex-native-20261021-ethanol-01` 和 `codex-native-20261021-benzene-01`；action ID 分别为 `f3dac09c9ae54f008f9da1cf0e74a2a6`、`f097c78bb11640e599ea1c66bae2612d`。原始账本、worker 收据和输出在 [c-native-fixture.tar.gz](../../../integration/thin-harness-native-20261021/c_host/c-native-fixture.tar.gz)，SHA256 **`02be71654fcbb624760918406e4d18a2e688f7c97bb4bc97412bb165ac68f768`**；C 逐项复核其中 50 个记录文件的大小/SHA，无不符，V 独立复核另列。

改版前 manifest SHA 为 `7de1125e8e4e3c206f2e759ee61927a7748108d59988fc08f4a9bdee58fa006b`，新版为 `0d8090c65f60cad7970b9c51f6e6a0adfe13950f5aa2cbafd47410164a032880`。新版本 `methodversion-8d61881a39f7f5356e556e67d30faf937947a2ba86e698f18d16b9c3b58c3a76`，使用 `methoduse-5f6e9ee30561bd31a8266633912550c72e3a64390f35bbcbdc74d5abde30c80a`。旧/新完整快照的字节复核、原观测/余额/round/原事件不变和所有宿主管理导出字节不变，见 [post-native-checks.json](../../../integration/thin-harness-native-20261021/c_host/post-native-checks.json)。辅助 snapshot API 只保存/核验文件字节；所有 journal/state 写读均为原生 MCP。

### 资源、修复与未验证范围

| run | CLI wall 秒 | input tokens | cached input tokens | output tokens |
| --- | ---: | ---: | ---: | ---: |
| codex-direct | 111.635 | 663447 | 620416 | 1805 |
| codex-methods | 182.815 | 741188 | 703744 | 3407 |
| codex-linux | 55.137 | 364202 | 321536 | 1721 |
| codex-discovery | 29.248 | 147800 | 108672 | 667 |
| codex-discovery-isolated | 26.860 | 149396 | 108672 | 597 |
| codex-revision | 92.643 | 411592 | 368256 | 3387 |
| claude-direct / 401 | 4.862 | 0 | 0 | 0 |

这些是 CLI 原始 usage 字段，不把 cached 再与 input 相加。6 个 Codex run 共 input **2477625**、其中 cached **2231296**、output **11584**、reasoning-output **317**；货币收费未知。Claude 本次 401 原生返回 `total_cost_usd=0`，仅适用于该失败请求，不代表所有开发成本为零。表中 wall 是每进程 elapsed，原生进程曾并行；不可求和后声称项目实际历时。两个回放各 0.5 fixture-cost 与 Linux 2 declared_cost 不同单位、不同账本，不合并成科学成本；revision 中复制的旧账本费用不是新增执行费。

本轮真实反馈暴露共享 MCP 工具说明缺少 `log_ref` kind/字段及 result handle 边界，I 已修订 `mcp/runtime.py` 的 docstring，未改变协议/逻辑；只读原生复测见上表。C 收集远端原件的首个辅助脚本误将 facade 的 `completed` 用作 RuntimeStore 的状态，assert 失败；原 v1 脚本与错误日志保留，v2 按真实 `succeeded` 状态收集，未改 worker 或结果。临时摘要读取曾因 Windows 默认 GBK 失败，显式 UTF-8 后完成；这些是工程辅助失败，不计为产品/科学通过。

本次未发现所测宿主行为受固定 workflow、skill gate、内置目录白名单或预声明 DAG 限制。它只证明这些具体原生能力可用，不证明宿主在独立任务中更会选择方法 φ，也不证明 S、G 或科学知识 M 的独立改进收益。所有 LEARN 独立收益继续 not_run；Windows durable worker 仍不支持，Linux恢复/竞争/取消的专门证据归 A/V。R4b 保持暂停，未调用 PRISM 科学评分、CRO、旧计算恢复、旧部署覆盖，未自动提交或推送。

## 接续交付：功能方法与结构化父版本（2026-10-21）

本轮起点为 I 保全的 1157 文件 baseline，ZIP SHA256
`24b76668c45a72bd5c3517ffa6ef5bf9776f90e07a7f37b707bb36d32419cdc8`。
C 沿用原文件归属，没有替换整个 skill 目录。V 事前协议为
`function_acceptance_20261021.json`，SHA256
`9ff39fe7c2471c8ba6ec6dc6b9d272f988fa39d3f3e2503eecee0ed3b31106f2`；
V 在运行前也复核了原生任务目标，未给宿主完整强制调用顺序。

### 产品方法内容

四个既有入口补全可实际使用的方法内容：单批筛选、gate 校准、生成产率、
只读监控均给出适用问题、实际参数、输出解释、失败及停止边界。它们可单独
或部分使用；没有增加全局必先规划、默认阶段推进或新的产品 registry。
`etalon-campaign` 增加一个按需 reference：
`references/factorial-diagnostic.md`，直接对齐既有
`ResearchJournal.factorial(contrast_id: str) -> dict`。输入为已有 Contrast
和观测；正常例 interaction=3、缺格保持 None、混合单位拒绝、诊断角色与
耦合边界由 B 的真实 SDK 函数测试和持久夹具验证，见 DELIVERY_B。
这里没有新增研究算法或假称该 SDK 函数是 MCP 工具。

仍为 6 个既有产品 skill；工程重构 skill 不在产品注册中。
6 份 `references/legacy-workflow.md` 对 baseline 逐字节未变，见
[legacy-preservation.json](../../../integration/thin-harness-functions-20261021/c/legacy-preservation.json)。
5 个修改入口经 skill-creator quick_validate；宿主/开放 bundle 测试
第二次执行 **25 passed, 1 deselected**（wheel 留给 I 的完整打包验收），
12.84 秒，原 log/XML 在本轮 `c/host-tests-second.*`。

### 固定来源的真实原生案例

本轮正式 CLI 使用 I 冻结的完整 `pre-native-source.zip`，SHA256
**`9fbcdc8cae6868a47b39236cfff79a9d5504df29824fa68e8372ea80652bdffd`**。
解压到 `integration/thin-harness-functions-20261021/c/fixed-source`，运行前后
1164 文件逐项核验无变化；该快照不含 `.git`，不是新工作树。
真实 Python 3.13.12 位于原 `.venv/Scripts/python.exe`，MCP 实际模块来自
固定快照 `src/etalon`；原生 capabilities 响应也返回该路径。
没有把 checkout 后续文档变更、最终 wheel 和本次运行源混为一体。

CLI 为 `codex-cli 0.163.0-alpha.10.2`，原生登录状态为 ChatGPT。
每个 run 有独立 cwd、导出、会话 MCP 配置和输入；lineage 使用 SQLite
合法 backup，monitor 是新建明确标注的人工 running/unknown 夹具。
未读取、复制或输出凭证，未改全局配置。模型使用 CLI default，实际
resolved model 未被日志暴露，故仍记 unknown；货币成本也未知。

| run / 对应 ID | 实际观察 | C 结论，待 V 独立判断 |
| --- | --- | --- |
| `native-monitor` / FM-08、HOST-01 | 4 次真实 MCP：capabilities/status/events/executor-list；无 skill 内容读取。宿主区分 journaled running 与活跃进程，读到 0 observations、0 admitted、reserved=2、spent=.25、remaining=3.75，然后停止。 | 只读监控通过；原 DB SHA、所有记录及导出字节不变。人工状态不证明真实 worker 执行或取消。宿主还指出 fixture 的 wall_second 与 fixture-cost 标签不代表已验证换算或实际计算成本。 |
| `native-lineage` / FM-10、MET-02/03/07、HOST-01 | 18 次真实 MCP；按需读旧/新局部 bundle，新内容保存在独立 snapshot；成功追加派生版本的 structured parents、一个无父 root 和 host-reported partial MethodUse，共 3 条方法 provenance。 | 结构化父版本追加通过；旧 parentless record、旧 snapshot、历史原 DB、原事件前缀、观测和预算保持。不是修写上轮 parents=[]，也不证明改进收益。 |
| `native-productivity` 首次 / FM-07、HOST-01 | capabilities 成功，真实 `etalon_generation_productivity` 等待 120 秒后 timeout，无数值响应。宿主保留未知，明确检查未完成，基于原输入和已有库存停止；无 skill、全局规划、campaign 或科学派发。 | 数值验收 **失败**，退出 0 不算通过；反馈后停止有真实行为证据。完整错误、等待和费用保留。 |

派生版本 ID 为
`methodversion-5f61d7cfe848ef7a3bdfa485702379ae5d9a10a0a0e369a7611575b53298a3fd`，
其 `parents` 精确包含上轮真实记录
`methodversion-8d61881a39f7f5356e556e67d30faf937947a2ba86e698f18d16b9c3b58c3a76`。
新 snapshot manifest SHA 为
`e3544b6b284ee4b1d37a1af3cb3ca9980429d61463c146535e7d26f93d586d13`；
旧 `0d8090c65f60cad7970b9c51f6e6a0adfe13950f5aa2cbafd47410164a032880`
仍可完整验证。无父 root 为
`methodversion-3a3dfc53517d90aff4643fa2b764bcc6c89eb8115733470f6547751d791ce43d`；
使用记录为
`methoduse-9bba1d249efe0d16feef44abe85a7399a11e5685572dee0f76d26604f0b16c12`，
basis 为 host_reported。内容返回、声明 partial use、实际 MCP 查询分别
可观察；不将它们升级为工具证明宿主使用整个 skill。

原始 prompt、launch、stdout.jsonl、stderr、exit、版本和内容哈希在各
`c/native-*` 目录，便于复核的逐调用行号/参数/结果/资源摘要见
[native-summary.json](../../../integration/thin-harness-functions-20261021/c/native-summary.json)；
源码复核见 `c/post-native-source-check.json`。
首次 C 审计 helper 把 Python tuple 与 baseline JSON array 直接比较而误报，
原报告保留 `native-summary-initial-audit.json`；按相同 JSON 表示复核后
相等，DB SHA 从未变化。修正说明见 `audit-correction.json`，未修改产品、
fixture 或宿主原始日志来通过验收。

| 首轮 run | CLI wall 秒 | input tokens | cached input tokens | output tokens |
| --- | ---: | ---: | ---: | ---: |
| native-monitor | 37.115 | 189219 | 164480 | 807 |
| native-lineage | 87.077 | 389364 | 344960 | 3195 |
| native-productivity / timeout | 157.078 | 495115 | 466816 | 1047 |

input 包含 cached，不重复累加；3 进程并行，wall 不作为项目总历时求和。
成本单位、已复制旧支出、人工夹具支出和宿主货币费用不互换。Claude 仍是
上轮真实 401 后 blocked，本轮未重试或伪称兼容通过。独立科学收益、
选择能力 φ、S/G 改进依然 not_run。R4b 继续暂停，没有新科学评分/CRO、
旧任务恢复、共享依赖升级、部署覆盖、提交或推送。

### 产率冷启动缺陷与独立复测（2026-10-21 03:04 CST）

上表的首轮 timeout 没有抹除。I 用相同固定源码/参数的真实 stdio MCP
复现，在 `wilson -> scipy -> numpy` 的冷加载扩展模块处取得阻塞栈。
A 作为登记负责人保留 Wilson 公式，改用标准库 `NormalDist` 计算分位数，
并明确拒绝非法 confidence；数值/统计回归由 A 记录，I 的 4 项真实冷
stdio 检查另列。没有升级共享 SciPy/NumPy，也没有用 SDK wrapper
代替宿主调用。

C 随后使用新的完整 `pre-native-source-v2.zip`，SHA256
**`943225fc55c0146c78e4d4ce3e19eb5d86c0433f10a1a31d10c2b08f9fcca24e`**，
独立解压到 `c/fixed-source-v2`。运行前后 1168 文件 SHA 均一致；
没有覆盖旧 1164 文件快照。`native-productivity-v2` 有新导出/配置/输入
副本，输入与首轮逐字节相同，prompt 只替换新目录，不预写工具顺序或答案。

复测真实通过：宿主仅调用 capabilities 和 generation_productivity，
得到 delivered=300、unique=0、Wilson 95% `[0, .0126]`、
cost_multiplier=null（无界）、exhausted 和 retire 原因后，自主决定
停止该循环、建议使用现有 20 个分子的库存。未读 skill 内容、未调用
plan/campaign/generate/screen/action/recovery，未写输入或导出。它同时
明确未来新增仍不确定、0.05 小时来自人工输入、不是真实支出，货币费用
未知。此结果仅证明这个小任务可跳过旧阶段并按实际反馈停止，不证明
φ、S、G 或科学收益。

复测 CLI wall **26.480 秒**；input **155072**、其中 cached **127872**、
output **555**、reasoning-output **0**，模型仍为未解析的 CLI default。
原生原件在 `c/native-productivity-v2/`；汇总已追加到
`c/native-summary.json`，新源码核验在 `c/post-native-source-v2-check.json`。
本轮四次实际 attempt 总 input **1228770**、其中 cached **1104128**、
output **5604**、reasoning-output **189**，含首轮失败，货币成本未知。
原两项通过的监控/父版本案例继续绑定旧快照，不冒充在新快照上重跑。

## 更多 research 可选方法与原生部分采用（2026-10-21）

本轮沿用 C 产品 skill 唯一写入权，并使用本机 `skill-creator` 指引。
只修改 campaign 的 SKILL 入口并新增
[`references/finite-diagnostics.md`](../../.claude/skills/etalon-campaign/references/finite-diagnostics.md)：
对齐已有 `calibration.bind_outcome`、`diagnostics.condition`、
`diagnostics.exact_program`，明确具体输入/返回、支持外停止、role/hash/身份
拒绝和引用费用。三者可以单用或部分组合，没有规定必经调用顺序，也没有
增加第七个 skill、router 或新的科学 MCP 工具。`bind_outcome` 写同一 journal
的不可变派生 provenance；后两者为纯函数。研究后端算法由 B 确认复用。

入口同时区分可选旧 `Campaign.round`、显式 `Supervisor` 调度与
`ParameterChange.judge/Acquisition.propose` 建议：构造 Supervisor 可初始化
stores，不能用作通用只读监控；建议不应用配置或授予执行权。
实际绕过无关阶段和旧默认行为核验归 A，不以文字说明替代运行证据。
6 份 legacy reference 与上轮 factorial reference 共 7 文件均与本轮基线
字节一致；新入口 quick_validate 通过。相关证据为 workspace
`integration/thin-harness-research-20261021/c/legacy-preservation.json` 和
`skill-validation.log`。工程重构 skill 未混入产品内容。

### 固定源原生案例：失败观测的有限模型支持边界

I 新封存 `pre-native-source.zip` 的 SHA256 为
**`e88a7ba697bdb0f61e9f7fe9e9bd00415f5dba889e72b76c81d4e3b1ad5604ea`**。
C 解压到本轮 `c/fixed-source`，1175 文件运行前后逐 hash 一致；不覆盖前轮
snapshot、原始超时或失败。实际 Python 为原
`F:/life_healthy/ETALON/.venv/Scripts/python.exe` 3.13.12，模块从该固定源
导入；路径与模块 hash 见 `native-diagnostic/binding.json`。正式 Codex CLI
现场版本仍为 `0.163.0-alpha.10.2`，原生 auth status 为 ChatGPT。
每次运行使用本轮新配置、导出、数据和 cwd，不读写凭证或全局 MCP 配置。

本次 goal 给定一个合成失败 Observation、有限模型、固定 outcome rule 与
工程边界，请宿主自主选择实际 SDK 检查并根据返回决定下一步。没有预写
完整工具顺序或答案。prompt 中 “A required derived outcome provenance append
... is permitted” 本意为所选方法需要时允许追加；V 在运行开始后提醒此
措辞可能被理解为要求 binder。该 prompt 和 hash 原样保留，不据结果重写，
故本次不据它证明完全自由选择是否使用 binder；其余可选/停止行为由日志
具体限定。未来类似任务宜写成 “If a chosen method needs to append ...”。

原件在
[`c/native-diagnostic/`](../../../integration/thin-harness-research-20261021/c/native-diagnostic/)，
便于复核的索引在
[`c/native-summary.json`](../../../integration/thin-harness-research-20261021/c/native-summary.json)。
实际观察如下，最终通过判定由 V 独立复核：

- **真实 MCP**：`etalon_capabilities` 后只通过 `etalon_skill_read` 返回
  campaign 的 `references/finite-diagnostics.md`，文件 SHA
  `e4f9daf0d9511f2c1d323bf4ff1f093e4f63c5c27f9899d33f53c5b220e72cf6`。
  宿主另作源码检索和局部读取；没有通过 MCP 读取整份入口或历史 reference，
  不能将 catalog 可见性当作已读取全部 skill。
- **原生 host shell → 实际 SDK**：宿主自己生成并执行 `bounded_check.py`，
  调用真实 `bind_outcome`，将其实际 `probe_id/outcome` 送入 `condition`。
  `x=failed` 保持原失败，返回 `model_challenge`、空 world indices/空 posterior；
  likelihood 和 causal_hypothesis 都为 null。具体 Observation/model ID 与
  原返回在 `bounded-result.json`。这不是新的 MCP 科学计算工具。
- **部分采用后停止**：宿主脚本根据真实返回走支持外分支，没有调用
  `exact_program`（仅 import，`if conditioned` 分支未进入），没有改模型、
  拟合、派发 query、screen、score 或恢复。
  最终文字声明只部分采用该 reference；声明为 host-reported，实际函数和
  未执行分支另有 helper/输出证据。支持外只说明当前模型边界，未宣称所有
  可能查询无用或发现真实科学机制。
- **状态与成本**：只追加 1 条 `probeoutcome` 派生记录。原 research storage
  bytes、事件前缀、科学状态和导出不变；budget=4、spent=.25、reserved=2、
  remaining=1.75 保持。它们均为人工 fixture_credit，非测得 CPU 或货币成本。
  一次宿主源码 wildcard 检索 exit=1 原样保留，之后改为有效检索继续；未把
  该失败删除或算作通过。没有 SDK 执行异常或未知值补零。

本轮 1 次 native attempt 的 CLI wall 为 **71.054 秒**；input tokens
**394435**（其中 cached **329216**，不另相加）、output **1921**、
reasoning-output **21**。resolved model 未暴露，记录 unknown；货币费用和
全部本地处理成本未知。宿主调用成功只证明这个合成任务可读取相关内容、
在指定诊断目标与 SDK 约束下部分组合真实函数并根据反馈停止，不代表完全
无提示的自由方法选择或整份 skill 已内化，不证明 phi、S、G 或科学独立收益。
三函数的其他 SDK/反例测试归 B/V，不能将其数量与 C 原生次数相加。

Claude 继续沿用前轮真实 401 的 blocked 状态，本轮未重试。R4b 继续暂停；
未重跑旧科学批次、未新增评分/CRO、未升级共享环境、未提交或推送。

## 配对开发模型与记录/编译/反馈接缝（2026-10-21）

本轮按 `skill-creator` 指引继续维护同一个 campaign skill，仅增按需入口和
[`paired-models-and-segments.md`](../../.claude/skills/etalon-campaign/references/paired-models-and-segments.md)。
它对齐现有 `empirical_model` 的完整配对 rows、group/split/evidence 约束、
默认拒绝相关行及可选 group-balanced 权重；纯构造不冒充 artifact 准入或收益。
同一 reference 说明 `record_program/compile_first_segment/observed_branch`
真实接缝，包括 B 修复后的已声明 probe bindings 与 model/resource artifact
重新检查、categorical 与连续 readout 的区别，以及编译、runtime preflight、
提交执行三个不同边界。没有增加新注册 skill、强制函数顺序或管理框架。

`model_challenge` 不具备可保存 program schema/ID；合法 stop program 可保存
但不可编译。调用者的 operation mapping 不等于科学语义认证；数值 readout
不能凭字符串猜成 low/high，而应使用已有明确 OutcomeRule/binder 后重新判断。
上述领域实现与反例由 A/B/V 负责，C 不用文字说明替代真实检查。
6 份 legacy、factorial 和 finite-diagnostics 共 8 个旧 reference 与本轮基线
逐字节一致。入口 quick_validate 通过；证据在 workspace
`integration/thin-harness-research-use-20261021/c/prior-reference-preservation.json`
及 `skill-validation.log`。新 reference SHA256 为
`b58cab98cf2d55c272b873d05c0422b2985be07a24a36575f79184a33396060d`。

### 一次新的原生配对模型使用案例

I 固定来源 `pre-native-source.zip` SHA256
**`cd99711db238f5c57c1927cc40ff520da7f8a25cd6f798c3002a7a2059c30d5c`**，
独立解压到本轮 `c/fixed-source`。1183 文件运行前后逐 hash 一致。
使用正式 Codex CLI `0.163.0-alpha.10.2`、原生 ChatGPT auth，以及原
`F:/life_healthy/ETALON/.venv/Scripts/python.exe` 3.13.12；实际 source/import
路径及模块 hash 在 `c/native-paired/binding.json`。未改全局配置、凭证或旧证据。

V 在运行前阅读 prompt：只有 paired-model 目标、数据/预算/副作用约束，
未指定 group_balanced、完整工具顺序或必须造出模型；可拒绝、明确假设或停止。
输入是新的合成 rows：a1/a2 共组 A，b 属组 B，holdout 仅为合成声明。
原件见 [`c/native-paired/`](../../../integration/thin-harness-research-use-20261021/c/native-paired/)，
索引及状态比较见 [`c/native-summary.json`](../../../integration/thin-harness-research-use-20261021/c/native-summary.json)。

- **真实 MCP**：3 次调用，capabilities、campaign SKILL 入口及新 reference；
  没有读取其他 reference。内容返回与宿主声明使用可观察，但不证明整份
  skill 内化、最佳方法选择或任何独立收益。
- **原生 host shell → 实际 SDK**：宿主检查真实构造函数后，自行写两个
  `empirical_model` 调用。默认调用真实拒绝相关任务；显式
  `group_balanced=True` 返回完整模型，a1/a2/b 的权重为 .25/.25/.5，
  全部 rows 与组身份保留。原拒绝文本、函数输出和 input SHA 在原始
  stdout.jsonl 第 18 行，没有删去拒绝或更改任务使默认路径通过。
- **准确的行为范围**：两次调用在同一个宿主编写的 shell 片段，第二次在
  try/except 后无条件执行。因此证据是宿主自选两种调用进行明确比较，
  查看实际结果后解释假设并停止；**不是**收到第一次工具响应后另一个
  LLM 轮次自主换法的证据。无需为扩大声明另跑一次。
- **未执行范围与保存**：没有记录 model/program、编译 segment、调用
  `exact_program`、preflight 或科学派发；0 条新增 provenance，原记录、
  事件、输入、数据、导出和科学状态全部保持。budget=10、spent=.25、
  reserved=2、remaining=7.75 不变，均为人工 credit，非实测 CPU/货币支出。
  宿主最终明确纯构造不证明 artifact 准入、真实独立、预冻结、预测校准、
  留出收益或科学价值。默认拒绝为预期领域边界，不计成通过的拟合。

本轮 1 native attempt 的 wall 为 **70.241 秒**，input tokens **143835**
（其中 cached **113152**，不重复相加）、output **1173**、reasoning-output
**12**。模型实际 resolved ID 与货币费用仍 unknown；其他处理 overhead
未计量。C 一次日志摘要显示遇到 GBK UnicodeEncodeError，随后仅改用
ASCII JSON 显示；原始日志和宿主运行均未修改，记录在 `c/audit-notes.json`。

本轮不重复上一轮成功案例填表；独立 S/phi/G 和科学收益仍未运行。
Claude 沿用真实 401 的 blocked 结论，未重试。R4b 暂停，无新科学评分/CRO、
旧计算恢复、共享升级/部署、提交或推送。C 材料稳定后交 V 独立核验与 I 封口。

## 按需发现、准确 SDK 示例与局部批处理（2026-10-21）

本轮 C 按既有唯一归属修改 `skills.py`、`mcp/host.py`、`host.py`、HOST
指引及产品方法内容；共享 tool registration/index 由 I 修改，A/B 提供并
实际验证领域示例。未改 E 独占的场景/三组对照文件，未替 E 重复跑原生任务。

新增的兼容接口为：

```python
etalon_capabilities(detail="full", skills=None, tools=None, operations=None)
from etalon.skills import catalog
catalog(detail="full", skills=None)
```

零参默认返回原完整 catalog、工具名称到 cost 的映射、operation 参数描述
和 runtime 路径，旧调用无需迁移。`detail="summary"` 去掉 bundle 文件清单，
保留说明、入口及完整 bundle 内容 digest；summary tools 以实际工具名称为键，
值为 `{cost, description}`，由 I 同一个注册路径的 `tool_catalog()` 提供。
`None`/省略选择该类全量，`[]` 选择空，其他列表为精确名称。未知、重复、
错误类型或未知 detail 明确拒绝，不静默扩大范围。`scope` 声明覆盖范围；
scoped catalog hash 只覆盖选中的 skill 版本，不据此证明未请求的内容未变。

例如 `detail="summary", skills=["campaign"], tools=[], operations=[]`
只扫描该 bundle，不访问其余 5 个 bundle。为了返回真实版本身份，server
仍读取所选 bundle 的全部成员字节进行 hash；**这不等于宿主读取了所有
reference 内容**，也不声称 summary 已消除这部分文件 IO。
需要成员清单时，用同范围 `detail="full"` 展开。外部/修改后的本地 bundle
继续通过原 `bundle_ref` 读取，目录筛选不是方法授权白名单。
已知工具或成员可直接调用，不新增“先发现/先skill/先方法记录”的门槛。
CLI 增加 `python -m etalon.host catalog --detail summary --skills campaign`；
裸 `catalog` 保持原行为，`--skills` 不带值明确选择空。

两个既有 research reference 末尾加入 B 验证的完整可运行 Python 例，
包括显式 dataclass 构造、JSON 恢复、实际反馈分支、预算/支持外停止和
unknown 费用；普通使用不再需要为填未定义变量而宽搜源码。它们可以被
修改或部分采用，不是新的必经链。monitoring skill 增补完整 action handle
的直接 SDK 入口与 A 的 `examples/local_sdk_batch.py` 使用边界：这是可改的
repo 示例，不假称 wheel 自带 examples。单项读取失败不遮蔽其余结果，
摘要保留费用、pending、未知和原件 hash；完整原件另存新目录，重复/不可
加总的执行费用不相加。局部一个脚本中的真实结果条件分支无需人为增加
LLM 轮次，也没有任意代码执行 MCP、后台循环或新 SDK 框架。

6 份 legacy reference 和旧 factorial reference 共 7 文件与本轮基线
字节一致；原 finite/paired reference 仅追加已测示例。两个相关 SKILL
均通过 `skill-creator` quick_validate。源码、文档和示例未提交或推送。

### 实际验证及限制

证据目录为 workspace
[`integration/thin-harness-efficiency-20261021/i/c/`](../../../integration/thin-harness-efficiency-20261021/i/c/)。

- 新增 `tests/test_discovery_scope.py` **13 passed**，覆盖零参旧形状、精确
  子集不扫描其他 bundle、空范围、完整展开同版本、错误不回退、reference
  改动改变 digest、外部 bundle 保留及目录外 `-S` CLI。它包含在此前
  scoped+旧 host 回归 **38 passed / 1 skipped** 内，不相加。skip 是当前
  `.venv` 无 hatchling 的旧 wheel 测试；打包/安装由 I 的真实独立环境另验，
  不将此 skip 写为 passed。原日志分别为 `discovery-first.*` 和
  `discovery-final.*`。
- 用真实 stdio MCP client 完成 **5 个调用**：首次直接读取已知 member，
  然后完整目录、campaign summary、tool/operation summary、未知范围拒绝。
  完整原始 response、参数、实际模块 hash 和分段 wall 在
  `stdio-results.json`；不是原生宿主行为验收。该组件运行的实际 Python 是
  `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，从当前 checkout `src`
  导入；并不冒称后续 E 固定来源或最终安装包。
- 该次所见文本字节分别为 direct member **4134**、完整目录 **10437**、
  campaign summary **1411**、tool/operation summary **1299**、拒绝 **267**。
  后两类选择了更小范围；这是具体返回大小，不能单独推出任务质量、token
  或端到端效率收益。其 timing 未控制系统/缓存，不充当 G0/G1/G2 对照。
- B 从两个实际产品 reference 提取执行 **2 passed**，包括额外反例；A
  对局部批读示例的状态/费用/坏句柄/重复及 CLI 核验另见各 DELIVERY，不
  将他们的检查数量重复算成 C 独立测试。

真实宿主三组比较由 E 使用 I 固定的来源推进；本节不预判快速模型价值，
不把新方法说明或小 payload 当成 S/phi/G 独立收益。既有 Claude 401 阻塞
未因配置/组件检查而变成通过。R4b 继续暂停，未新增 PRISM 评分/CRO、
恢复旧科学任务、升级共享环境或部署覆盖。

## I2 全量能力覆盖：可选使用与改进（2026-10-21）

本轮 C `/root/c_usage_improvement` 仅维护 WORKSTREAMS 所分配的 HOST.md、
HOST_SETUP.md、新 `etalon-skill-improvement`、SDK 示例和测试；共享注册、
MCP、host.py 和打包交 I2。已应用 skill-creator 和本地工程重构指引；后者
保留 local-only，不进入产品库。

新增方法基于具体问题证据提出局部候选，明确 S 内容与使用指引分别版本，
保留完整旧新 supporting files、部分试用、失败/未知成本、后续独立评价，
由宿主选择保留/修订/撤回/停止。没有 router、必读 skill、登记 gate、后台
自改进循环或权重训练。HOST 补已有 SDK/reference 精确入口以回应前轮实际
宽搜索日志；这是优化候选，不将其表述为已验证的延迟或 token 收益。

`examples/skill_improvement.py` 真实调用现有 snapshot、ResearchJournal
MethodVersion/Use/Evaluation、consume_events；在隔离合成数据库保存 S 和
usage 各两个版本，实际读一份 paired reference，记录 partial/host_reported
使用、diagnostic 评价与未知费用，评价事件处理一次、重放无新增处理，随后
停止且保留旧新版本。SDK 返回内容与原生宿主采用分开；没有科学观测、默认
晋升或 independent_validation 伪记录。未来任务 ID 是明确未分配占位，不
是已冻结/执行的独立评价。该示例随源码交付，不声称 wheel 内置命令。

实际验证使用 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`、checkout
`src/etalon`，原件在 `integration/thin-harness-skills-20261021/c/`：

- `tests/test_skill_improvement.py tests/test_host_bundles_hooks.py`：13 passed，
  `c-tests.xml`；新例单独 1 passed 已包含，不重复相加。
- skill-creator quick_validate：passed，`quick-validate.txt`。
- CLI 实例实际运行结果 `sdk-example/result.json`，含真实模块路径、完整
  snapshots/journal、trial 和 accounting 原件。
- 首次测试暴露 TaskSplit 不能空 transfer，已保留 `first-work.json` 后按
  原契约修正示例；首次没有完成，不能计成功。

原生验证由 V 事前冻结的两项工程任务决定，限一次/任务，不补跑至通过。
原生结果在固定安装来源交付后另追加。独立科学/方法收益仍 not_run，旧
Claude 401 仍独立 blocked，R4b 继续暂停。

### 两项原生结果与预算（同日追加）

两项均严格绑定 I 的非 editable `installed-v1`，实际 Python 为上述 `.venv`
解释器；固定源码 ZIP SHA256
`1101f873e2938570bd035781dc94f119ab3dbcd2d4cd7b5a9335f786f54edafc`，
wheel SHA256 `4c04e2b318b2d97542070073a2f4c1aa258f07bca5158469ab3dd2669d75498b`。
任务、原始输入、全部安装文件、prompt、宿主二进制和 runner 在启动前冻结。
宿主只获任务局部文件、普通导出和必要安装 SDK；未向它提供 rubric、旧答案
或 fixture builder。边界为指令与日志审查，不冒称操作系统访问隔离。

| 正式任务，各一次 | 实际结果 | 预算 | wall 秒 | shell/MCP | 累计 input / cached / output |
| --- | --- | --- | ---: | --- | --- |
| library-design-quarantine | SDK 导入 A 的 1 molecule/2 poses/4 slots；B 的实际 hash 拒绝；重放无新增记录；0 observations/actions | **failed**：443159 input 超 400000，runner 在 usage 首次可见时终止，exit 15 | 157.42 | 10/0 | 443159 / 359296 / 5111 |
| library-local-method-revision | 完整旧外部方法可验证，新正文与 supporting note 改版后实际用于三记录 review；保留 missing/failed/hypothesis 和 0.75 fixture_credit | passed，exit 0；实际采用剩余 input 上限 356841 | 72.50 | 4/0 | 76523 / 60672 / 2431 |

累计 input 519682、cached 419968、noncached 99714、output 7542、wall
229.93 秒、14 shell/0 MCP。实际模型请求为 gpt-6.1-sol/medium，resolved
revision 与金额未知；shell/MCP 次数不是所有模型请求数。第一项的业务产物
及 REPORT 已落盘，但 `final.txt` 未落盘；原始 agent message/turn usage
保留。其语义产物可核实，仍不能计作预算通过或完整成功退出。没有重跑、
调 prompt 追求通过或新增第三次任务。token 上限只在 usage 可观察时检查，
本次暴露 turn-end telemetry 会造成 overshoot，不能声称硬性前置 token
限制；第二项已据真实已花 input 扣减，累计额度内结束。

采用证据分列：第一项直接调用安装 SDK；第二项自主局部修改外部方法并形成
实际 review。两项均未观察到内置 skill/reference 的显式读取，不能用导出
存在或语义结果冒称新增库采用；导出的 HOST 指引可能已进入宿主上下文，
也不据此断言全部指引未生效。第二项没有调用 ETALON snapshot/journal；
它保存完整原件和各成员 SHA，C 事后只读使用已安装 snapshot 核实完整旧新
版本不同，该核实不算宿主 SDK 使用。S/usage 独立收益、生产频率和科学
改进仍 not_run。

原件：本轮 `c/runner-freeze.json`、两份 `*-freeze.json`、两任务目录的
stdout/stderr/exit、`*-audit.json`，以及只读实际数据库/完整外部 bundle
核验 `output-check.json`。所有原始输入与安装受测文件 hash 保持一致；
没有科学评分、标签读取、后台工作或共享环境改动。SDK示例另在固定源码
加同一安装来源运行成功，`c/sdk-example-installed/result.json`，与原生
两项成本分开。V 的独立判定见 DELIVERY_V。
