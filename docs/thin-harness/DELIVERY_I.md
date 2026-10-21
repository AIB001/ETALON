# I 集成交付

## I2 全量 skill 库（2026-10-21，当前接续）

本节是新交付，后文保留旧轮事实。I2 接管共享入口，A/B/C 已实际写入并测试各自领域，V先冻结判据再独立复核。工程实施应用系统 skill-creator 和本地 life-healthy-contract-refactor；后者只在本机，未进入产品。

### 内容、接缝与兼容

主库现在包含全部原12项产品skill（旧主目录6项和原MolQuarry6项），加data-design、research、skill-improvement，共15项。MolQuarry10个源文件原样复用，`skills.bundle_root` 从原asset/安装_assets目录定位，旧 `etalon://skills/molquarry[/…]` 与主发现/按需读取绑定相同内容。注册只扩现有WORKFLOWS；完整版本、双宿主导出复用已有host实现，不新增router或强制workflow。

新方法覆盖九项data工具及design导入、transfer/validation/feedback/pose/preparation/fixed_pool/online与相关公开族；campaign增加治理/FEP方法，已有finite/paired/factorial算法和原reference保留。筛选/生成/校准/安装增加真实局部SDK例，monitoring既有内容复用。完整位置与副作用见README的“全量能力族覆盖”，A/B/C各DELIVERY记录具体用法。旧历史协议绑定的retained_baseline未被包装成新任务通用API。

HOST增加准确SDK/module/member指针；新增改进方法把内容S与使用指引分开保全版本，以既有MethodVersion/Use/Evaluation和单页hook作可选试用、记录和停止。没有默认权重训练、自动无限改进或S×策略门槛。改版和采用不自动构成收益。

### 固定来源、安装与工程验证

入口1196文件与上一轮final manifest完全一致，完整保全基线SHA256 `781095cb26f291feda07ccf27c3938f3bed8f576a0d1fbe63ec40699d00c922d`。main/两个本地origin refs和index保留，未fetch、暂存、commit/push。受测固定源 `i/tested-source-v1.zip` 含1223文件，SHA256 `1101f873e2938570bd035781dc94f119ab3dbcd2d4cd7b5a9335f786f54edafc`；后追加DELIVERY与V新测试只属于最终收尾源，不冒称在该受测源内。

wheel `i/dist-v1/etalon-0.1.0-py3-none-any.whl` 为7423336 bytes，SHA256 `4c04e2b318b2d97542070073a2f4c1aa258f07bca5158469ab3dd2669d75498b`。pip隔离构建后 `--no-deps --target i/installed-v1` 非editable安装，无共享依赖升级。945个源/wheel/install条目完全一致；仓库外实际解释器仍为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe` 3.13.12，12个实际模块均从installed-v1导入。15bundles/65tools、双宿主完整导出、stdio与全部旧MolQuarry资源通过；见 `i/wheel-check-v1.json`、`installed-check-v1/RESULT.json`。

| 检查 | 实际结果／边界 |
| --- | --- |
| I共享兼容、发现、MCP和新MolQuarry检查 | 68 passed；`i/shared-tests-01.xml` |
| 原host完整导出、快照/hook与范围行为 | 29 passed、1 skipped；hatchling未装在runtime，wheel另经真实隔离构建/安装验证，不把skip写为通过 |
| 全部产品格式与本地工程skill | 15产品quick_validate通过，本地工程skill另验通过；不代表方法采用 |
| A data/design与现有数据边界 | 72 passed、1 failed、1 skipped；失败是Windows symlink创建WinError1314，尚未进入产品断言；skip为可选gemmi缺失。原件保留，不变更系统权限/共享依赖 |
| B研究方法与相关回归 | 49个不重复选测通过；首轮tuple/JSON list测试断言失败保留，修测试后6新例通过，不改生产算法 |
| C改版/使用/评价与hook | 13 passed，首次TaskSplit夹具缺transfer失败保留后修正；诊断例不冒称独立评价 |
| 新安装下SDK示例 | data/design、exported research脚本、改进例全部实际完成；另以 `-S` 标准库环境读取MQ/research目录通过。`i/installed-examples-v1/RESULT.json`，不把本地SDK当原生模型行为 |

各测试集合有重叠，不累加成全仓通过数字。保全审计 `i/preservation.json` 确认901个基线src/asset文件（除两共享注册入口和HOST文档）全部未变，原1196路径无缺失、index及refs不变。A/B分别核对MolQuarry和research原件。辅助命令失败在各线原件和 `i/HELPER_FAILURES.md` 保留。

### 原生与最终独立结果

最终组合新增测试 **23 passed**（含V的8项，不再相加），`i/combined-new-tests.xml`。V还独立复核基线/受测源全条目、945包条目与原生冻结输入，并从installed运行研究脚本及data/pose/governance/revision参考片段；见本轮 `v/source-package-input-audit.json`、`installed-probe.json`、`engineering-review.json`。V首轮4项失败源于误以为某MQ bundle有reference，修正到实际有支持文件的bundle后8项通过，未修改产品来过验收。

两个正式Codex工程任务各运行一次，绑定installed-v1和冻结输入；requested `gpt-6.1-sol` medium，实际后端revision未知。预算沿V冻结协议，每次300秒/24个shell或MCP/400000 input/8000 output，合计800000 input/16000 output。无额外补跑。

| 原生任务 | 实际行为与质量 | wall /调用 / token | 预算结果 |
| --- | --- | --- | --- |
| design-quarantine | 直接读取已安装SDK，合格结构导入与坏raw哈希拒绝、零观测、幂等及身份原件保留；不把结构/Astra作为实验标签。未观察到内置skill/reference或MCP调用 | 157.4228秒；10 shell；input443159（cached359296）、output5111 | **failed**：input超过单次400000。usage仅在turn.completed才可见，runner随后终止任务进程树，exit15；不把正确产物写成完整有界验收通过 |
| local-method-revision | 宿主自行保存外部方法原件、修订支持文件并实际审阅三条新合成记录，保留missing/failed/prediction与失败费用0.75 fixture_credit；没有独立收益声明。未观察到内置方法读取 | 72.5048秒；4 shell；input76523（cached60672）、output2431 | passed，exit0；仍受累计剩余额度约束 |

合计229.9276秒、14 shell/0 MCP、input519682（cached419968、noncached99714）、output7542；真实金额和模型请求往返数未知。cached属于input子集，不重复计费推算。token是可观察usage到达时检查，**不是执行前可严格保证的上限**；第一任务超限原件完整保留。C原件位于 `c/library-design-quarantine/`、`c/library-local-method-revision/` 与对应audit，独立判定以DELIVERY_V及本轮v原件为准。没有循环重试或为证明采用而强迫读skill。

工程发现/完整版本/安装导出通过；这两次新原生对内置库的观察采用为0，另一次外部方法修订并实际使用成立。二者不是同一指标。旧skip/partial/combine等行为继续仅引用前轮固定来源；本轮不从工程采用推断生产效率、S/使用策略收益或因果提速。用户已有所有权和资源范围保持，无新审批门槛。

新最终全源码以 `i/combined-source-final.zip`、对应manifest和 `i/FINAL_RECEIPT.json` 交付，保留全部dirty/untracked及本轮报告，不覆盖tested-source-v1或旧1196文件归档。V封口核对只写本轮v目录；R按用户要求接收一次精简结果与证据入口。

本轮不做科学评分、封存标签读取、旧科学恢复或CRO；R4b仍暂停。Claude旧原生401保持独立blocked、Laya不默认启用、Jev未测。科学收益、独立S/使用策略/G改善、生产高频和一般效率收益均未建立。

2026-10-21，本地工程实现与共享集成完成；最终冻结组合测试 158 passed，V 独立工程反例 16 passed。范围与未运行项分别见本文件及 DELIVERY_V，不能把工程通过读成科学收益或全仓所有平台测试通过。

## 来源与保全

现场本机 HEAD 为 `5927aceb346475a6f97550f561e7eb3b291611da`，origin/main 为 `0af4297fb7de435d858d54195561883a21526d5e`，薄分支为 `d3889e6c0600605f018fb81ce3df1b420d6dcbf9`。未 fetch，故仅称本机引用，不声称远端最新。复用当前 main checkout，不创建遗漏 dirty/untracked 的工作树，不提交或推送。

共同可恢复基线位于 workspace `integration/thin-harness-engineering-20261020/`：

- `baseline.zip`、`baseline-manifest.json`：1121 个 tracked/nonignored-untracked 文件，39016246 bytes，逐文件 SHA 校验通过。ZIP SHA256 为 `0149945f75ff9b6c9e0ed8f388b1e343c0a3f4c399d1a2e1af45540dfa51dcc0`。
- `sources.bundle`、`refs.txt`、`working.patch`、`staged.patch`、`status.txt`：三个源码来源和原 Git 状态。忽略的环境、历史科学证据和本地指引原地保留，未改写。
- `integration-merge-record.json`：I 逐文件来源。A/B/C 各自合并领域修复，I 没有替代领域线串行改写。

起始与复核暂存区均为空。相关进程检查没有发现 ETALON 活动科学任务；未操作其他 PRISM/optics 进程。molcascade 144 个 Python 文件与起始快照逐字节一致。

## 共享实现

`mcp/server.py` 的 `tool_modules()` 是实际注册与费用发现的唯一来源；保留薄分支 `register_tools()` 兼容入口。新增动作准备、提交、结果、事件读取和三类方法记录/读取工具；MCP 只调用 A/B 后端，不复制校验或恢复状态机。所有 65 个工具有明确费用分类和无重复名称检查。

`mcp/runtime.py` 返回 `prepared`、`handle`、`result` 包装；事件页沿后端返回。方法工具返回 `record_id` 和 `record`，仅接受 B 的三 schema；读取使用 read-only CampaignStore。动作错误保留非重试拒绝；未知结果不会触发 MCP 自动付费重派。

I 合入 main 的 MCP 错误分类、governance/sweep 和 decision/authority/council/doctor/admissible 修复，保留本地 `prism_bridge_v1` 说明。打包保留本地 `design` 依赖 extra、全部科学源码和资产，加入薄分支完整 bundle 的 references/scripts；`.gitignore` 公共契约例外保持原样。

旧 MCP 测试保留科学测量数字、费用和拒绝断言；历史长内容改为按需 reference 校验，固定 workflow 路由改为可选方法。一个上游 POSIX 路径测试在 Windows 失败，改为实际临时绝对路径，未放宽实现校验。

## 工程 skill

I 按 skill-creator 制作本地 `.agents/skills/life-healthy-contract-refactor/`，由 Git 本地 exclude/hook 保护，未进入 C 产品注册。它引用现有契约，不复制管理框架。`merge_preview.py` 只在仓库外生成三方候选，不改 index 或源文件。

实际验证：对 `mcp/runtime.py` 合入 main，同时保留本地 PRISM bridge 文本；对 `pyproject.toml` 合入薄分支完整资源，同时保留科学 extra。两个预览均无冲突，审阅后逐文件应用。技能校验通过；初次系统默认 GBK 解码失败，改用 `python -X utf8` 后通过，没有改共享环境。

## 已运行证据

实际解释器为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12。开发模块从 `F:/life_healthy/ETALON/src/etalon/` 导入。以下均为本地工程测试，不使用 R4b 标签作为未知测试。

| 命令/范围 | 状态与实际结果 |
| --- | --- |
| `pytest tests/test_mcp.py tests/test_council.py -q --tb=short` 首轮 | 89 passed、1 failed；Windows 路径测试问题已保留记录 |
| 路径单项复测 | 1 passed |
| 新共享接缝首轮 | 2 passed、1 failed；I fixture 把 dependencies 写成 list，按 B 已明确对象协议修正为 `{}` |
| `pytest tests/test_thin_harness_integration.py tests/test_mcp.py tests/test_council.py -q --tb=short` | 93 passed，62.33 秒；含真实 stdio 客户端与同账本方法写入 |
| 新增 CLI/SDK/MCP 事件及外来游标一致性单项 | 1 passed，2.01 秒 |
| 非 editable 初版 wheel、checkout 外导入/资源/stdio | passed；10 模块、6 完整 bundle、65 工具、两种 host 导出 |

初版安装证据为 `installed-preliminary.json`；`verify_installed.py` 可复现。构建依赖仅装入 pip 临时隔离构建环境，目标包用 `--no-deps --target` 安装独立目录；未升级共享解释器依赖。最终源组合与独立包核验见下方收尾记录。

## 明确未证明的范围

HOST-01 原生 Codex 和 Claude Code 的发现/选择/改变下一步均 `not_run`；Python MCP client 与宿主导出检查不代替原生行为。Linux `/proc` detached worker/真实 PRISM 贯通未在本次 Windows 工程会话执行；A 使用短路径验证可运行 CPU 工程例，原生进程边界另列。

LEARN-01/02 独立收益、真实新增科学计算、收敛/亲和力/药效、完整 RSI 均 `not_run`。R4b 保持暂停，无新付费科学任务、CRO 订单、共享部署或旧计算恢复。开发开销未全量测量，不填零。

回退应先保存当前改动，再按 manifest 和固定来源逐文件恢复；不得整目录覆盖、reset/clean、自动 stash 或覆盖暂存区。

## 最终冻结组合与收尾

A 收尾发现旧 `_active_interval` 未计入同 campaign 非标量研究费用，已修复原收据校验；未知 resource 预留继续阻止新动作。V 独立验证历史失败 resource 0.5、新 action 1.5、后续 resource 0.25 的总费 2.25；动作结果只引用自身 1.5，重放不再收费。

最终命令（同一解释器）与事实：

```text
python -m pytest tests/test_runtime_actions.py tests/test_research_methods.py tests/test_host_capabilities.py tests/test_host_bundles_hooks.py tests/test_thin_harness_contracts.py tests/test_thin_harness_host.py tests/test_thin_harness_integration.py tests/test_mcp.py tests/test_council.py -q -k 'not wheel' --basetemp=F:/life_healthy/ETALON/.agent-local/i-final-20261021 --junitxml=F:/life_healthy/integration/thin-harness-engineering-20261020/final-contracts.xml --tb=short
# 158 passed, 2 deselected in 73.67s
```

两项按名称排除的 wheel 检查由下面实际构建/安装/内容校验替代；其中静态 force-include 表已在之前 93 项回归中通过。没有将 deselected 记为 passed。

- 最终成品：`integration/thin-harness-engineering-20261020/dist-final-v2/etalon-0.1.0-py3-none-any.whl`，7355643 bytes，SHA256 `6b71961494f11ff0fdbdea692adea1f05f4d2c74712e389c663597656807c9f2`。前两个候选保留在 dist/、dist-final/，不冒充最终版本。
- 构建：`python -m pip wheel . --no-deps --wheel-dir <dist-final-v2>`；安装：`python -m pip install --no-deps --no-compile --target <install-final> <wheel>`。未升级共享依赖。
- `verify_wheel_sources.py --repo <ETALON> --wheel <wheel> --output <wheel-source-final.json>`：325 个源码/完整bundle/molcascade文件逐字节匹配，0 差异，包含原未跟踪科学源码和本地 Windows 修复。
- 从 checkout 外 `integration/thin-harness-engineering-20261020` 运行 `verify_installed.py --installed <install-final> --output <installed-final.json>`：10 个模块路径全部在非 editable 安装内，6 个完整 bundle，65 个无重复且费用完整的工具，两种宿主配置导出和真实 stdio 请求 passed。不是原生宿主行为验证。
- `git bundle verify <sources.bundle>`、`git diff --check` 和本地 pre-commit guard passed；index 未变。起始 1121 文件没有丢失；V 另核验 43 个关键原科学文件字节保持原样。

领域交付中保留的非绿色结果不能被这组通过覆盖：A 广泛回归 404 passed / 32 failed（POSIX flock、Unix路径及 Windows symlink 权限），B 扩展范围 5 个缺 sklearn 的旧 conformal 测试失败；B 精确 research 为 84 passed / 5 skipped，后者需要显式 PRISM producer snapshot。原始失败、短路径 CPU 复测和适用范围在 DELIVERY_A/B。`wsl --list --quiet` 只读核验确认本机 WSL 未安装，记录 `local-posix-availability.json`；没有安装系统组件或部署 Linux 环境来改写该事实。

A/B/C/V 已分别实际启动并交付，I 公共文件和工程 skill 保持唯一维护。用户可从 README → DELIVERY_I/V → 各线报告及 workspace 工程证据复核。没有向原 chat 回传、要求 R 审批、恢复 R4b、升级共享环境、提交或推送。

## 2026-10-21 接续：原生宿主、POSIX 与回归归因

用户明确授权的同一 I 工程接续；以上第一阶段结果和未运行状态保留为当时事实。新证据根为 workspace `integration/thin-harness-native-20261021/`，不改写原目录。进入时 1153 个文件全部匹配第一阶段最终 manifest，无新增非 ignored 路径、暂存为空。A/C/V 实际并行启动，V 先固定判据，释放槽后 B 完成依赖归因，随后 V 返回审查实际组合。

V 的事前判据 `tests/fixtures/thin_harness/native_acceptance_20261021.json` SHA256 为 `5525fa623104559ee44ca968844e2ca15070b771cc8c408658f77c2107d09f9a`。判据不因试验结果改写。宿主 prompt 只给工程目标和约束；运行记录包含实际选择、工具响应和后续行为。

### 本轮实际生产改动

- A `runtime/service.py`：真实 Linux 取消 fixture 暴露 `_settle_failure` 把 pending 的已测部分费用与调用者提供的最终总费再次相加，导致合法失败无法结算。改为终态费用加 unresolved 最终总费，并拒绝低于已测部分费用的总费。没有释放未知预留或补造观测。最终文件 SHA256 `ffff910947feeb153f535dc8b1e15eb4c8fd07ac54b779ce515dadb2623f50fa`。
- I `mcp/runtime.py`：原生宿主曾猜错事件日志类型并将 replay action ID 当 runtime handle。补充两工具的准确参数说明：campaign/runtime 日志引用、游标，以及必须使用 submit 返回的完整 handle；replay 应读取 campaign 状态和事件。没有新增路由、执行器或第二实现。最终 SHA256 `d2b66f0d78d941b22aba06d36b190a0a8e1409f5cbf2a4803afbe412fecc41d6`。

其余生产文件与第一阶段最终源码一致。工程 skill 仍只在开发侧，由本地 exclude/hook 保护，没有加入产品注册、包或宿主导出。公共契约没有降低失败、成本、未知或验收要求。

### 宿主与平台证据

Codex `0.163.0-alpha.10.2` 使用官方 `codex.exe exec`，`--ignore-user-config --ignore-rules --ephemeral` 与独立 cwd/配置/账本；未改全局 MCP 配置。首次 discovery 是对上一案例账本的只读复核，该例的输入复用边界单独保留，后续独立副本复测不覆盖它。实际模型、调用和 token 记录见 C 的 launch、stderr 和 stdout JSONL，不把 SDK 调用冒充宿主。Linux MCP 经纯 SSH stdio 字节透传连接，工具选择仍由原生宿主完成。

这些 Codex 调用未显式指定模型，CLI 使用默认条件且日志未暴露最终解析的模型名，因此实际 model 标 `null/unknown`；不能根据当前集成 chat 的模型或配置猜测。已知 CLI/参数/响应/token 保留，这一条件限制方法比较的可复现性，不影响日志已经观察到的工具能力事实。

- `c_host/codex-direct`：无 skill，真实 MCP 离线夹具执行后因 `F_BUILD_INCOMPLETE` 拒绝准入，保留 0.5 fixture-cost，宿主读取事件并停止。这是工程回放，不是独立科学试验或 Windows durable worker。
- `c_host/codex-methods`：按需只读两个外部 bundle 的短入口，在 QC 反馈后加入费用审查并组合部分范围；通过真实 MCP 记录两个内容版本和一个 host_reported MethodUse。三个错误工具请求原样保留；只证明观测到的读取、声明和调用，不能推断读取了所有 reference 或方法优越。
- `c_host/codex-linux`：真实 CPU MolCascade 分子量动作，乙醇 46.069 Da 完成/验证/准入成功后，宿主根据余额和结果自行提交苯 78.114 Da；两次 `method_use_refs=[]`，无预先完整 DAG。总计 2 declared_cost、reserved=0、remaining=0；报价费用不冒充实测计算时间。该运行绑定原第一阶段 Linux 源快照，未涉及后来修复的失败结算分支。
- `c_host/codex-discovery`：在 I 工具说明修复后的原生只读复测，两个 MCP 读取成功，正确使用 campaign log，未伪造 runtime handle，继续区分成功执行与被拒准入。
- `c_host/codex-discovery-isolated`：另用 SQLite backup 创建独立数据副本，按同一目标由原生 CLI 只读复测，26.86 秒、两个 MCP 查询成功。保留前一次输入复用记录，并验证新的副本隔离，未执行新动作。
- `c_host/codex-revision`：在独立复制的夹具上修改本地 receipt-review 候选，原 manifest `7de1125e8e4e3c206f2e759ee61927a7748108d59988fc08f4a9bdee58fa006b` 与新 manifest `0d8090c65f60cad7970b9c51f6e6a0adfe13950f5aa2cbafd47410164a032880` 均有完整快照；真实 MCP 读取新内容、保存 MethodVersion/host_reported MethodUse，原观测不变。一次缺文件读取和一次方法证据引用类型错误保留，宿主自行纠正。只证明保存、修改和实际部分试用，未证明改进收益。
- Claude Code `2.1.150` 可定位，登录状态接口显示已登录，但实际原生调用返回 401 Invalid API key。保留失败，HOST-01/Claude 为 **blocked**；需要该 CLI 可实际使用的有效认证。没有读取、输出或复制凭证，没有用其他客户端代替，也不让此项阻塞 Codex/Linux。

revision 成功写入的新 MethodVersion 的 `parents=[]`，旧版本只在 change_summary 文本中引用；新旧内容快照和旧绑定均可核验，但本例没有证明结构化父版本关系写入成功。V 的原账本比较确认前 13 个事件和候选/动作/观测/余额不变，仅追加方法记录。

本机 WSL 仍不可用，未安装。A 现场核验已有授权 Linux 主机身份及相关活动进程后，使用新目录 `/root/zx/prism-minimal-20261017/engineering/etalon-thin-native-20261021-01`，旧源码/依赖/科学账本未覆盖。Python 3.11.17 的独立工程 overlay 仅读取既有依赖，缺项仅安装于新环境。原快照与修复 source-v2 独立保存，实际路径和模块哈希见 A 证据。

Linux 首轮真实进程检查 3 passed / 1 failed 保留；它触发了上述费用结算修复。后续测试还保留观察到进程死亡但锁仍暂时不可取得时的真实拒绝，测试使用有界 OS 锁探测记录就绪状态，没有取消生产锁检查。最终真实进程、32 项旧失败逐 ID 归因以及独立结论见 DELIVERY_A/V。

最终 Linux 组合为 **33 passed**，包括三种真实双进程竞争、一次 SIGKILL 后原收据恢复、一次真实 child 取消/未知/显式失败结算，以及旧恢复审计。原 A32 所在 campaign/sweep 两模块在固定原源码上 **250 passed**；最初归档没有 Git 元数据导致的另外 4 项来源断言失败保留，补入原 `sources.bundle` 的真实 HEAD 后通过。未把源码归档补成虚构 revision。V 独立核验最终 tar、原 SQLite/收据、调用计数、活动 child 身份及失败结算费用；收尾工程相关进程为空。

B 原 5 项失败在三个来源均为同一源码，缺少已经声明的测试依赖。只在本地 ignored 工程 overlay 安装缺项后，同样 5 项通过；相关两个模块共 **116 passed（包含该 5 项）**。原失败和首轮 overlay 缺 cloudpickle 的失败保留，未修改被测源码或断言，未升级共享 `.venv`。见 `b/ATTRIBUTION.json`。

### 最终组合、安装与保护

Windows 解释器仍为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12。实际源码从 checkout `src/etalon` 导入；安装验收从 checkout 外运行。没有把不同快照上的检查说成同一次运行。

```text
python -m pytest tests/test_runtime_actions.py tests/test_thin_harness_contracts.py tests/test_thin_harness_host.py tests/test_thin_harness_integration.py tests/test_mcp.py -q -k "not wheel" --basetemp=F:/life_healthy/ETALON/.agent-local/i-native-contracts-20261021 --junitxml=F:/life_healthy/integration/thin-harness-native-20261021/shared-contracts.xml --tb=short
# 84 passed, 1 deselected in 74.82s
```

该 wheel 项由实际新构建/安装检查替代，不记作 pytest passed：

- 新 wheel：`dist/etalon-0.1.0-py3-none-any.whl`，7356017 bytes，SHA256 `194f6c97aff8c6ea7ffda19e142a41ab1e77652a36a1ba04fd33a810cdc70f3c`，包含本轮两项生产改动。
- `pip wheel . --no-deps` 后 `pip install --no-deps --no-compile --target <本轮install> <wheel>`；未覆盖第一阶段包或共享 site-packages。
- 复用第一阶段 `verify_wheel_sources.py` 与 `verify_installed.py`：325 文件逐字节相符；10 模块均从非 editable install 导入；6 完整 bundle、65 工具、两种宿主导出、实际 MCP stdio 全部通过。原生行为另由 C 日志证明。
- `git diff --check`、现有本地 pre-commit guard 通过，工程 skill 和依赖 overlay 的 ignore 保护仍有效。最终全文件快照/保护复核另见本轮 `FINAL_RECEIPT_V2.json`，不覆盖旧 receipt。

各测试组有重叠，不累加宣称新的总覆盖率。宿主 token、工具夹具费用、实际进程耗时按各记录分别解释；未测量的总研发/宿主金额保持未知。R4b 仍暂停；真实新增 PRISM 评分、CRO、独立 S/φ/G 收益和科学 RSI 均未运行。没有固定 workflow 限制被测宿主选择；这不证明其选择策略优越。没有提交或推送。

### 接续收尾

V 的独立原件复核在 `tests/fixtures/thin_harness/v-native-review.json`、`v-native-package.json` 与 DELIVERY_V。判定为所测 Codex HOST-01 和隔离 Linux ACT-05/06、RES-07 passed，Claude HOST-01 blocked，LEARN-01/02 not_run；Windows 原有 32 项平台失败没有被修复或隐去。V 只审查原件和实现，没有修改被测代码使其通过。

A/B/C/V 报告和实际已测 V4 测试纳入本轮 `combined-source-final-v2.zip` 与逐文件 manifest。`FINAL_RECEIPT_V2.json` 保存封口后的 archive/wheel SHA、来源差量、保护范围和 index 状态；ZIP 关闭后重新打开逐字节核验，避免把未封口流的 hash 当成成品。原 1121 基线、第一阶段 1153 文件归档与 sources.bundle 继续保留。回退仍需逐文件核对，不运行 reset/clean/stash 或覆盖暂存区。

首次封口后 V 检出 README 一处并行文档更新，其他 1156 文件未漂移。该入口新增功能去向表注明来自原讨论 chat 的补表，随后把证据段更新为 V 已完成判定；I 没有向原 chat 回传，也没有据此扩大实施范围。当前文字与实际验收边界相符，保留外来文档改动后重新封口。首个 archive/manifest/`FINAL_RECEIPT.json`、两份 README 字节副本和 `v-final-receipt-check-first.json` 失败均保留；V2 是最新源码组合。此事不改变生产文件或功能测试结果，也不能把首个 live-check 失败记为通过。本轮完成的是已授权的宿主/可靠性/归因范围，README 功能表的其余待办不据此宣称全部完成。

## 2026-10-21：功能接缝与方法对齐接续

README 临时补表已由原讨论 chat 交回 I；用户授权本轮继续 FM-05—10 的最小有用场景。现场三来源 refs 与上一轮相同，1157 文件逐字节匹配 native V2，index 为空。新完整基线在 workspace `integration/thin-harness-functions-20261021/baseline.zip`，SHA256 `24b76668c45a72bd5c3517ffa6ef5bf9776f90e07a7f37b707bb36d32419cdc8`。原 science dirty/untracked、旧 archives 和失败原件保留，未用 worktree 假定包含它们。

A/C/V 实际并行，V 先冻结反例后释放槽给 B，B 完成校准/research 对齐后 V 再独立核验；文件唯一归属在 WORKSTREAMS。工程重构 skill 沿用本地受保护版本，只用于来源/职责/验收，不进入六个产品注册或导出。

### 实际变化与限定范围

- FM-05：原批次/数值策略继续复用。A 用真实便宜 CPU 批次发现 `Screen.run` 在持久 FAILED 后继续抛异常，使上层丢失可读结果；现返回匹配的新失败收据，保留 native FAILED、exhausted 解释及未计量费用。旧 run/锁拒绝不吸收其他写入者的状态。后续不同 revision 不回写旧产物。
- FM-06/08：原查询路径构造 Screen/Sweep 会创建目录或迁移旧库。A 提供只读旧池查询和 `Screen.open_existing`，复用原 artifact/hash/canonical 校验；I 将 status/progress/calibrate/authorize 的 MCP 封装接入，不复制算法。缺路径/坏收据明确拒绝，live WAL 读取最新提交而不使用 immutable 忽略 WAL；SQLite 允许自身 SHM 读锁记账，不把它伪称全文件物理无变。
- FM-07：原 `allocate`、可选 Pipeline/Campaign/Supervisor/propose 策略保留，已有库可跳过生成。I 去掉计划工具说明中“必须先于所有工具”的文字，但不删除对应支出操作的有效计划/授权约束。真实原生产率调用暴露的冷依赖超时见后续记录，不能用直接 pytest 替代该结论。
- FM-09：B 原 research/calibrate 生产源码未改；C 把实际 `ResearchJournal.factorial` 对齐到 campaign 的一个可选短 reference，没有第七个注册 skill。输入/输出、缺格、失败、混单位、未知 uncertainty、停止和描述性边界明确；不是独立因果或科学收益。
- FM-10：原生新案例在隔离 SQLite backup 上追加精确 `parents` 引用、另一个 root 的 `parents=[]` 和 host_reported partial MethodUse。前轮 parentless 记录/快照不修改，未新建科学观测、未宣称学习改善。

C 补足筛选、校准、产率、监控四个现有 skill 的具体适用范围/输入/输出/停止说明，六份历史 reference 逐字节未变。README 原行数表明确标为第一阶段历史值，不把入口长度当解耦或效果指标。

### 针对性检查与原失败

本机仍为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe` / Python 3.13.12。A 导入路径和哈希见 `a/final-v2-imports.json`，B 见 `b/RESULT.json`；原科学来源未升级或替换。各组有交集，不累加成总覆盖率。

- A：26 passed，真实 CPU 产物及零命中/未来版本、只读反例；最初发现的产品缺陷和新增测试自身字段错误均保留，详见 DELIVERY_A。
- B：4 passed，实际 SDK 对人工 fixture 的校准/停用/revision guard、持久 factorial 完整/缺格/单位拒绝；39 个原研究/校准文件与基线相同。
- C：25 passed / 1 wheel deselected，实际包检查另做；不是 25 个原生宿主实验。
- I：`tests/test_function_mcp.py tests/test_mcp.py` 54 passed / 44.41 秒。之前 2 failed / 1 passed 是新增测试错猜旧错误码和返回层级，原日志保留，修正为既有 `FileNotFound` 和平铺 `retire` 契约后再测。
- V：新增 5 个独立反例通过；零命中真实产物的整体 recall 仍为 None，不能因为已知一层零存活就编造完整测量。V 的测试假设修正与原失败见 DELIVERY_V；未改产品实现以迎合测试。

### 固定来源的原生案例与首个包

`pre-native-source.zip` 含 1164 文件，SHA256 `9fbcdc8cae6868a47b39236cfff79a9d5504df29824fa68e8372ea80652bdffd`。C 在独立 `c/fixed-source/` 解压逐项核验后，用正式 Codex 和真实 MCP 运行三个独立配置/导出/数据案例。模型由 CLI 默认解析且遥测未给出，model=null；总货币成本未知。原生三案例均绑定此快照，不把后续修复/README 文字称为其原受测代码。

- monitor：4 次真实 MCP，37.115 秒；无 skill 内容读取，unknown/running fixture 的数据库字节不变，spent=0.25、reserved=2、观测=0，明确没有真实活动 worker。宿主读实际事件后选择停止，不释放预算或发起重试。
- lineage：18 次真实 MCP，87.077 秒；旧记录内容、原事件前缀、科学状态和快照均保留，恰好追加两条版本与一条使用记录。结构化父引用与独立 root 均真实写入/读回。
- productivity 首次：157.078 秒，工具等待 120 秒后超时；宿主保留未知并停止，**数值调用 failed**。I 同固定源码/参数的新 raw MCP 20 秒超时复现，faulthandler 显示 `wilson → scipy → numpy` 的扩展冷导入停滞；该协议探针不是原生验收。直接进程导入 scipy.stats 2.61 秒可完成，不能据此抹去 MCP 冷启动缺陷。

首个候选 wheel SHA256 `4b366ce39bb27fa8d4120bc6f78b7a3b99a5b19a63f8955e8aff6249b8f5eae0`，326 个源码/包文件匹配；非 editable 安装验证 10 模块、6 完整 bundle、65 工具、两种宿主导出与真实 stdio。它不包含产率冷依赖的后续修复，不代表该失败已解决。首次 `--no-build-isolation` 因共享环境无 hatchling 失败保留；随后 pip 临时隔离构建成功，未往共享环境安装或升级构建依赖。原 candidate 包、安装和日志保留，最终包另存。

### 冷调用缺陷修复与新原生结果

B 的三个独立短进程（直接 import、MCP 默认环境、build 注册后 import）均能正常加载 SciPy，因此只把异常定位到真实活动 stdio 会话的冷导入，不宣称已查明 NumPy/DLL 底层根因。40 组合法数值对照的最大区间差为 `2.22e-16`，原件见 `b/dependency-summary.json`。

A 唯一修改 `generate/audit.py` 的 Wilson 标量分位数依赖：改为 `-NormalDist().inv_cdf((1-confidence)/2)`，保留 Wilson 公式、计数拒绝、零成功/全成功精确端点和旧生成策略；confidence 非有限或不在 `(0,1)` 时明确拒绝，避免旧 NaN 夹成似乎可靠区间。没有全局预热或强制加载科学依赖。A 的旧 audit/pipeline/productivity 与数值/边界组合 **69 passed / 85.61 秒**；其 SciPy 正常域和极端尾部对照详见 `a/wilson.log/xml`。

I 在共享测试加入真实冷 stdio 回归：新进程启动时 numpy/scipy 均未预载，产率返回后第二次 capabilities 仍成功；`tests/test_function_mcp.py` **4 passed / 7.77 秒**，见 `i-cold-mcp.log/xml`。这不是对原生产率成功的替代证明，C 另运行正式 CLI。

`pre-native-source-v2.zip` 含 1168 文件，SHA256 `943225fc55c0146c78e4d4ce3e19eb5d86c0433f10a1a31d10c2b08f9fcca24e`。C 使用新 `fixed-source-v2/` 和独立 `native-productivity-v2/` 配置、导出及输入，输入与首例逐字节相同。真实 Codex **26.480 秒**内完成 capabilities 和产率两个 MCP 调用：300/300 delivered、0/300 unique，Wilson 上界 0.0126，cost_multiplier=null（无界），exhausted/retire。宿主根据实际返回停止并建议使用既有库存；没有 skill 内容读取、计划、生成或科学派发。未测得未来新颖性或货币费用，三段 60 秒为 synthetic 输入，不是已支付计算账单。旧 120 秒超时与 tokens 保留，不能因复测成功删除。

monitor/lineage 仍绑定 v1，productivity 成功绑定 v2；两次源运行后 manifest 均逐项相同。单次原生能力不证明最优选择或 S/φ/G 收益；四次尝试的输入/cached/output tokens 和 wall 分别保留在 C 原件，不重复加 cached，不把并行 wall 相加当总历时。

### 最终包与保全

最终包为 `dist-final/etalon-0.1.0-py3-none-any.whl`，7365027 bytes，SHA256 `95ede2d138c18afc6ab4091be9527db31815de48b2074efeea6bee9058409f4c`。`wheel-source-final.json` 的 326 文件全部匹配；`installed-final.json` 记录 checkout 外非 editable 安装的实际 10 模块、6 bundles、65 工具、两种宿主导出和 stdio。V 另核最终包的冷调用，不拿候选包结果覆盖修复版本。

科学来源保护范围的 209 文件中 206 字节未变，三个有意改动是 molcascade 的 runner/cache/artifact-store 只读构造支持。原 Windows checkpoint 命名修复仍在；其差异及来源由 V 独立核对。不改 research、active/prism.py、boundary/prism_v1.py、data/design.py 或 PRISM 仓库。工程 skill 仍由本地 exclude/hook 保护，不在 registry/wheel/产品导出。

最终完整源码、逐文件 manifest、三来源 refs、原基线无丢失和暂存区状态见本轮 `combined-source-final-v2.zip` / `combined-source-final-v2-manifest.json` / `FINAL_RECEIPT.json`；文件关闭后再 hash 并核验，不覆盖前两阶段或本轮 pre-native/candidate 归档。README/报告是后续交付文本，不能冒充原 native 快照内的文字。V 封口复核只写仓库外 `v-final-receipt-check.json`。

当前限定工程清单已交付，进一步迁移其他 research 方法和更广旧策略不据此称为完成。Claude 的有效原生认证仍 blocked；独立 S/φ/G 收益、真实新 PRISM 评分、CRO 与科学 RSI 仍 not_run。R4b 暂停，未部署共享环境、升级共享依赖、恢复旧任务、提交或推送。

V 最终独立组为 **7 passed / 7.02 秒**（包含此前 5 项，不能相加），另实际核验最终非 editable 包的冷产率 MCP 与后续查询、真实 native-v2 日志及数值定义域拒绝。最终状态为本轮 FM-05—10 限定工程场景 passed、Claude blocked、LEARN-01/02 not_run；V 仓库内报告已稳定，最终封口检查另存仓库外。

首次源码封口已核 1170 文件后，receipt helper 引用 C 进程退出证据时拼错文件名，未生成最终 receipt；错误和首档保留于 `receipt-first-error.txt`。另将 V 对 B 探针的文字按原件明确为 direct-main / MCP 默认环境 main / build 注册后 main。修正文档后另封 final-v2，不替换首 archive；生产代码与数值/原生日志均不变。

## 2026-10-21：更多 research 方法与旧策略可选性

本轮按用户最新授权继续 FM-07/08/09。入口 1170 文件与前轮最终归档逐字节相同，local HEAD/main 和两个 origin refs 均未变化，未 fetch；index 为空。共同基线为 workspace `integration/thin-harness-research-20261021/baseline.zip`，SHA256 `aab89b2d5a82571569a87e3e4ddae518f2d74f070d1e119084ec2f95232d31f2`，完整包含 dirty/untracked。现场相关 Python 为 PRISM_review 任务，不是本轮 ETALON 写入者；未操作这些进程。

A/B/V 实际并行，V 先冻结反例并释放槽给 C，随后 V 独立核验。沿用 WORKSTREAMS 唯一归属；I 使用本地 contract-refactor skill 保全来源和衔接接口，不把它加入产品。新证据均在上述 research 目录，旧三轮归档、原始失败和科学账本不覆盖。

### 实际改动和依赖核查

- B/C 在已有 campaign bundle 增加 `references/finite-diagnostics.md`，对齐三个**现有**函数：`calibration.bind_outcome` 写入原 Journal 的派生结果；`diagnostics.condition` 纯函数检查联合支持；`diagnostics.exact_program` 纯函数给局部有限预算建议。各自适用、输入/输出、失败/停止、角色/哈希/费用限制明确，可只用一个或部分组合，不增加科学 MCP、注册框架或第七个 skill。38 个 research 生产文件未改。
- A 实查旧 Campaign/Supervisor/propose：`ParameterChange.judge`、非空 `Acquisition.propose`、单阶段 `Pipeline` 和不 tick 的状态/生产率读取可独立使用。`Campaign.round` / `Supervisor.tick` 仍是显式选用的旧整体编排，不是普通方法的全局前置。A 仅修正三个文件的说明，可执行 AST 与基线一致，Supervisor 全文件未改。旧构造可能创建运行目录/池，不能当作只读入口；之前的只读适配继续保留。合法 gate、数据充分性、probe/resource 依赖没有被弱化。
- I 将共享计划工具返回的 `next_step` 文字改为可选调优/换法/停止，保持字段和数值逻辑。旧文字会造成必须继续调优的误导，但未发现对应运行时全局 gate；本轮是说明修正，不冒称拆掉了新的强制路由器。

### 测试、环境和原失败

实际解释器为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe` / Python 3.13.12；各线 JSON 保存真实模块路径和 SHA。下面各组按范围报告，不与前轮统计合并：

- A 首批 4 个新案例加 3 个既有精确节点 **7 passed**；追加非空 Acquisition **1 passed / 8.15 秒**，真实 36 行合成 SMILES/人工 affinity、6 scaffold、400-tree fit、五折校准和四选二后停止。仅该进程复用旧 `.agent-local/native-b-deps-20261021` sklearn overlay，`LOKY_MAX_CPU_COUNT=2`；不安装或升级共享依赖，事后共享环境仍无 sklearn。人工面板不是独立科学收益。
- A 首个测试错误地期望 SweepError，实际 NotAuthorized 拒绝正确；原 1 failed 日志保留。第二次宽 `-k` 误选旧完整 round，在 Windows 缺 POSIX flock 再次失败，原件保留；改用精确节点没有把完整 round 标通过。本轮没有新 POSIX 运行。
- B **7 passed / 2.66 秒**：真实 Journal 追加/幂等、角色/单位/哈希拒绝、支持内外反馈、共享资源费用与停止。failed/missing/unsupported/not_evaluated 不补零；派生记录不是新的 active 标签。
- I `test_mcp.py -k 'plan or catalog or tools_have or descriptions'` **4 passed / 47 deselected / 9.68 秒**，验证计划/发现兼容；正文改动不另造镜像测试。
- V 首轮 **5 passed / 2.74 秒**：独立检查边际均支持但联合不支持、已有共享资源、实测费用与报价分离、等阈值及重放后 artifact 篡改拒绝、跳过无关旧策略同时保留必要依赖。最终独立判定见 DELIVERY_V。

fixture 的 spent=.25、reserved=2 是人工账本值，不是 CPU 实测费用。CPU/货币总开销未计量，不以零代替；无新 PRISM、CRO 或旧计算恢复。

### 正式宿主的部分采用与反馈后停止

`pre-native-source.zip` 含 1175 文件，SHA256 `e88a7ba697bdb0f61e9f7fe9e9bd00415f5dba889e72b76c81d4e3b1ad5604ea`。C 在独立固定源、配置、导出和 fixture 下运行正式 Codex CLI `0.163.0-alpha.10.2`，真实 MCP 来自该快照。运行前后 1175 文件一致；A 后续非空 Acquisition 测试和最终 DELIVERY 不冒称已包含在原生快照内。

宿主实际完成 2 次 MCP：发现能力、读取 `references/finite-diagnostics.md`（SHA256 `e4f9daf0d9511f2c1d323bf4ff1f093e4f63c5c27f9899d33f53c5b220e72cf6`）。随后**宿主 shell 到 SDK**调用 bind_outcome，再将真实 `x=failed` 返回交给 condition；返回 `model_challenge`、空 posterior 后停止。生成脚本虽导入 exact_program，但不进入其调用分支；未派发 query、换模型或追加方法记录。这里只证明该 reference 已读取、两函数确实使用及宿主相应声明，不证明整个 SKILL 已读取或内化。

仅追加一条允许的派生 provenance，原记录/事件前缀/科学状态/预算保留。一次 PowerShell wildcard 检索 exit=1 原样保留，随后宿主读取明确文件并继续。任务指定了诊断目标和使用 SDK 的约束，允许派生追加的措辞带有引导；因此证据限于目标内的部分组合和反馈停止，**不是完全无提示的自由选法实验**，也不证明 φ/S/G 收益。

运行 71.054 秒；input_tokens=394435，其中 cached_input_tokens=329216；output_tokens=1921，另报告 reasoning_output_tokens=21，分别保留，不把缓存重复加到输入。模型标识和货币费用未知。CLI 返回成功只作为进程结果，实际行为由工具日志、真实 SDK 返回及账本差量核验。Claude 沿用前轮实际 401 blocked，本轮未重试或读取凭证。

### 包、来源与收尾

新 wheel `dist/etalon-0.1.0-py3-none-any.whl` 为 7369337 bytes，SHA256 `70a64fa17fa08e9262183354a954bf532ea0462822c9420bd29c400ce5f131ba`。327 个源码/wheel/非 editable 安装文件一致；checkout 外检查实际 10 个模块、6 bundles、65 工具、两种宿主导出和真实 stdio。V 另外实际读取安装包的新 finite-diagnostics reference，不只检查旧 legacy。包验证不是原生行为的替代品。

209 个本轮保护文件（含既有 molcascade Windows/只读修复和 research）与共同基线逐字节不变；六份 legacy 及前轮 factorial reference 均保留。工程 skill 继续受本地 exclude/hook 保护，没有进入 registry、wheel 或产品导出。最终全源码、manifest 与 `FINAL_RECEIPT.json` 在本轮证据目录；各线完成仓库内写入后才封存，V 最终一致性检查只写仓库外。

本轮增加三个有用方法对齐并验证上述旧策略可选范围，未把所有 research 方法或全部 Campaign 路径宣称完成。独立 S/φ/G 收益仍 not_run，真实科学任务仍未授权执行。R4b 暂停，未部署、升级共享环境、提交或推送。

## 2026-10-21：配对模型与记录后使用缺口

按用户继续授权，本轮以真实使用缺口为先。新的 workspace `integration/thin-harness-research-use-20261021/baseline.zip` SHA256 `089c4589259232649bc6659e296441efb3578c4bcff2c93da7754eb15e9459e2`，1178 文件逐项等于上一轮 final；无新增/丢失/漂移，index 为空。local HEAD/main/两个 origin refs 未变且未 fetch；现场 34 个 PRISM_review Python 进程未动。保留完整 dirty/untracked 和旧科学/工程证据，没有创建会遗漏它们的替代 checkout。

A/B/V 实际先并行，V 冻结协议后 C 接续，与 A/B 对齐；A 完成后 V 回场独立验收。B 唯一维护 research 实现，A 从执行消费端提供复现和测试；I 沿用本地工程 skill 管来源/归属/公共文档/包，C 产品 skill 与工程 skill 分离。

### 实际缺陷和最小修复

两项真实缺陷均在原件中留下反例，不靠弱化测试通过：

- 原 `observed_branch` 只核 probe_id，另一已声明 condition 的观测也会进入原计划分支。A 从本轮 ZIP 独立加载旧 runtime 复现；B 现在匹配全部冻结 bindings，明确拒绝错误 record/readout。未声明的 task/method/unit 不由 probe 名字臆造，旧合法签名不变。
- 原 `record_program` 校验以后，模型证据文件仍可能变字节，后续编译却会追加新段。A 的红测 **1 failed / 7 passed** 保留。B 复用 Journal 原校验，在编译事务及反馈读取时重验模型 evidence 和 reused resource artifacts；不新增身份库或收费账本。V 使用三个独立文件分别表示模型、复用资源和观测，避免同一文件测试掩盖漏验。

Python 生产改动仅 `research/runtime.py`，SHA256 `bea93bae34be98dc6c83b4f9f957e500bf0c81a4a73c8b5c71d522bff69e8cd9`；另更新领域 README 的说明。`empirical_model`、`exact_program` 等算法不变。最初疑似 model_challenge/null-root 路径经核查不属于合法已记录 program：原 record_program 已拒绝其无 schema/id 的结果，不把人为损坏记录算作新发现的真实漏洞。

### 有用方法与实际检查

C 在现有 campaign 增加 `paired-models-and-segments.md`，对齐已有 `empirical_model`、可选记录/首段编译/离散反馈。完整 paired outcomes/losses、分组和 holdout 声明、默认独立任务权重与显式 group_balanced 均清楚列出；纯模型构造不是 Artifact 准入或科学独立性证明。需要连续量阈值时继续使用现有 OutcomeRule/bind_outcome，不让 observed_branch 猜测 low/high。没有新增注册 skill、MCP 科学工具、统一 router 或必经 DAG。

A 的 **9 passed / 3.67 秒** 验证首段/未知/停止及拒绝。实际调用 runtime.plan：假 bundle 虽能过 schema 编译，仍被输入预检拒绝；新隔离复制的已有工程 design bundle 通过，绑定 27 文件哈希和一个节点。测试到 compiled + planned 为止，无 worker、import/candidate 或科学执行。框架不自动保证 operation mapping 的科学等价，原预算单位/权限/准入仍由执行内核检验。

B 最终组合 **27 passed / 6.63 秒**，包含 B 新 8、原 Journal/validation 10、A 9；不与 A 单独运行或 B 首次 24 项相加。V 新 **5 passed / 2.48 秒**，独立覆盖不同 artifact 重放篡改、三组不等行数权重、独立评价资源拒绝、真实反馈仍有未来 query 时不自行编译/派发、数字不隐式变标签。各完整命令/XML、解释器/import/hash 均在对应 a/b/v，不能用这些数字代表原生或科学收益。

实际 Python 为 `.venv/Scripts/python.exe` 3.13.12；本轮未安装或升级共享依赖。合成账本的 spent=.25/reserved=2 保持，局部处理 wall 单列而总工程/货币成本未知。B 报告曾将 A 预检泛称“runtime 执行”，已由唯一负责人纠正文案；最终文本不是原生快照封存时的原文，不改原档。

### 固定来源与包

原生使用前源码为 `pre-native-source.zip`，1183 文件，SHA256 `cd99711db238f5c57c1927cc40ff520da7f8a25cd6f798c3002a7a2059c30d5c`。C 按完整 manifest 解压核对，使用独立配置/导出/fixture；后续报告和 V 证据纳入最终归档，不宣称已包含在该原生快照中。

新 wheel `dist/etalon-0.1.0-py3-none-any.whl`，7373554 bytes，SHA256 `dcf842dba633232534ce7aee1532e321ee26c33dcfa593569eb794858a897c14`。328 源码/wheel/非 editable 安装文件一致；checkout 外 10 模块、6 bundles、65 工具、两种宿主导出及真实 stdio 检查通过。V 另核安装包新 reference 与修复接缝，不能仅用旧包 smoke 代替。

本轮 209 个保护文件中 **207 未变**，仅 `research/runtime.py` 和其 README 为上述有意增量；molcascade 本地修复、PRISM 消费端、design 与其他科学源码均保留。六份 legacy 加前两份方法 reference 共八份旧内容逐字节不变；本地工程 skill 未进入产品注册、包或导出。

### 原生结果与实际范围

正式 Codex CLI `0.163.0-alpha.10.2` 的 native-paired 在 70.241 秒完成，3 次真实 MCP 为 capabilities、campaign 入口和新 reference 读取；实际计算是**宿主 shell → SDK**。默认 empirical_model 因重复 group 拒绝，显式 group_balanced 构造保留所有行并返回 `.25/.25/.5`，宿主根据真实返回停止在描述性模型，不记录/编译/执行后续程序。

两次 constructor 调用由宿主预先写在同一个 shell 片段中，故这里只证明宿主自选显式比较并按真实结果停止，不能说 LLM 先收到一次工具拒绝、再于另一个决策轮临时换法。任务没有指定 group_balanced 参数或强制工具次序；它仍是指定目标/已给 SDK 和数据条件下的一次能力测试，不是 φ/S/G 独立选择收益对照。最终文稿所述开发集零损失只是给定人工表内的描述，不是未见测试或科学优势。

没有新增 Journal provenance，原记录/事件/科学状态/预算不变；fixture budget=10、spent=.25、reserved=2、remaining=7.75。1183 固定源文件运行前后全部一致，原始输入和导出内容保留。输入 tokens=143835（其中 cached=113152）、输出=1173，另报告 reasoning=12，不重复加缓存；模型标识/货币费用未知。CLI 退出、MCP 读取、SDK 返回和宿主声明分别核验。

V 新包额外以 installed runtime 跑两项独立模型/资源篡改拒绝检查，**2 passed / 1.23 秒**；它们与源码 5 项有交集，不能相加成新覆盖。完整最终判定、限制与原件在 DELIVERY_V。所有仓库内报告稳定后封存 `combined-source-final.zip`、逐文件 manifest 和 `FINAL_RECEIPT.json`，V 只在本轮仓库外写最终一致性检查。

本轮实际新增一个经验模型方法对齐并修复记录后的两个真实使用缺口，没有迁移所有 research 方法。Claude 沿用前轮真实 401 blocked，本轮未重试；POSIX/完整旧工作流未重跑，独立 S/φ/G 收益与新科学计算 not_run。R4b 暂停，无共享环境升级、部署覆盖、旧计算恢复、提交或推送。

## 2026-10-21 按需使用与局部批处理接续

本轮按 `integration/thin-harness-efficiency-20261021/REQUEST.md` 实施，R 恢复审查，E 独占已登记的模型示例/测试/交付及外部 `e/`。实际并行 A/B/C，V 先定反例、后独立核验；共享 MCP、包和公共说明由 I 维护。开始时 1186 文件与前轮 final 逐字节一致，暂存为空；完整 baseline ZIP SHA256 `087729eabbff1c89157e58634af393c6428d65f052585fa099e0e971edd03a2c`。没有用 Git HEAD 代替 dirty/untracked 的运行版本。

### 实现与组件证据

C 实现可选 summary/范围发现，`skills`、`tools`、`operations` 的 None/空列表/精确项语义明确，零参保持旧完整形状。I 从原 `register_tools` 同一路径提取短描述和声明成本；未增加另一份工具注册、router、执行 MCP 或强制发现步骤。选择单 bundle 时仍读取该 bundle 完整字节计算内容版本，避免摘要被误称为另一版本；无关 bundle 不扫描。

B/C 将现有 finite-diagnostics 和 paired-models reference 对齐真实函数，用最小完整输入直接运行。A 的 `examples/local_sdk_batch.py` 只调用既有 `read_action_result`，宿主可修改组合；每项完整结果独立保存，普通摘要保留身份、失败/未知、费用、pending/恢复和准入。坏项不吞掉其他项，重复 execution 明示但不重复汇总费用；没有新账本、提交、自动重试或科学派发。该示例只在源码快照，不冒称 wheel 资源。

I 组合必要契约/兼容与新增测试 **41 passed / 14.22 秒**；包括 V 的 4 项及若干 A/B/C 测试，不能与子线数相加。C 原相关回归 38 passed、1 skipped（旧 wheel 测试缺 hatchling），实际包由 I 的隔离构建与安装核验补足；未升级共享依赖。首次 A 夹具缺 action binding、V CRLF 夹具失败和 I 提取助手语法/过早启动构建路径错误均保留或说明，不能冒充产品失败已消失。

I 从固定 G1 源启动两个全新真实 stdio 进程，各运行完整、局部和重复局部发现，共 6 个响应。完整 10489 bytes，单 campaign 摘要 1458 bytes，完整原件与单调计时存 `i/discovery-probe/`。这是组件负载检查；文件系统缓存未控制、没有原生宿主选择，也不据此声称端到端或科学收益。

### 交给 E 的固定来源

`i/READY.json` 已通过真实 queue 主动通知 E，绑定 G0 1186 文件和 G1 1191 文件；G1 源 ZIP SHA256 `42052caadc6a4f733b1a4183a1ba7c168fa562a2a12d35569c8b552e50b99581`。G1 wheel SHA256 `2d5d314049bee845b7fca1a5e2844146e8e6577cf680cf96b375eb27e21d82b5`；G0 复用前轮经本轮重新核对的 wheel `dcf842dba633232534ce7aee1532e321ee26c33dcfa593569eb794858a897c14`，安装到两个新的独立 target。

每组 **328 项源码/wheel/install 字节一致**，checkout 外实际 Python 为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe` 3.13.12，10 模块真实路径及 hash、6 bundles、65 工具、Codex/Claude 两种导出和真实 MCP 核查已记录。这证明安装与协议，不等于 Claude 认证或原生行为通过。E 的正式协议选择各自固定源码的 `PYTHONPATH` 导入与新配置/输入，I 则另验非 editable 安装；两者等价文件已核对，不把 E 原生调用写成安装包调用。后追加报告不回写 G1 固定源。

E 在 V 检查冻结的任务/输入角色/成功判据/预算/模型 runner 后才开始正式对照，READY 本身不是结果。频率、原生三组结果及其独立复核在本轮 E/V 原件中另记；不据组件通过推定模型收益。R4b 暂停，LEARN-01/02 独立科学收益仍未运行。

### E 原生三组的实际结果

V 事前核验后，E 按冻结 v2 执行两项历史工程任务的 G0/G1/G2，共六次真实 Codex CLI `0.163.0-alpha.10.2`；requested model 为 `gpt-6.1-sol`、medium，实际后端 revision 未知。隔离源码导入、配置、原始调用与用量在 `e/runs/`；完整输入和方法保持冻结，不向宿主提供标准答案或强制工具顺序。

V 逐项核对实际 SDK 返回、数据库和读取范围：六项均符合冻结语义判据（loss=0），原记录/事件前缀/预算保全，未见读取其他组结果或旧答案。诊断任务保留 failed 和 model_challenge 后停止；paired 任务保留相关行默认拒绝，在明示等组假设下构造 .25/.25/.5 后停止。同一个局部脚本内比较两个构造，不能称作另一轮宿主临时换法。

| 组（两任务合计） | 端到端秒 | 完成工具调用 | 累计输入 token | 实际采用边界 |
| --- | ---: | ---: | ---: | --- |
| G0 | 213.634 | 17 | 374802 | 旧接口/内容；两任务均完成 |
| G1 | 264.462 | 17 | 380185 | 两次均直接使用 SDK 源码，未采用 skill/capabilities |
| G2 | 224.018 | 13 | 375599 | paired 读取本地 skill/reference；两次均未用 Laya |

这些原值没有显示 G1 的端到端提速；G2 少调用也不能归因 Laya，因为其采用率为 0/2。按需发现和准确说明已可用，不代表本次宿主实际选择了它们。两个已见来源工程任务、不可控缓存与单次条件运行，不支持总体效率或科学 S/φ/G 优势。人工 Laya 反例与公开 smoke 在 E/V 交付另列，不能替代原生采用。cached token 是 input 的子集，不重复累加；实际计费未知，不能补为零。所有失败及处理成本保留。

原生六次合计 702.115 秒、47 次完成工具调用、3 次工具失败；input 1130586，其中 cached 882688、noncached 247898，output 12337。不可由未缓存 token 差异单独归因接口，也不推算未知账单。模型独立环境准备 945.256 秒，依赖网络传输量未完整计量，不能认证历史总下载低于 3GB。

E 的独立人工 Laya 案例使用固定官方 ModernBERT-large 检查点与 CPU 两线程：开发语义 1/2、评价语义 5/6 正确，两个长度拒绝和一个不可用故障回退成立。`dev_unknown` 在缺信息时建议比较，`eval_batch` 将成功/错误/不适用混合批次判为全成功；这些高选项概率错判不通过改阈值或事后修任务抹去（`answer_confidence` 不是 SDK 的 entropy-based `confidence` 字段，也不是校准保证）。含公开 smoke 共 12 个请求、9 次真实 forward、7.729 秒 forward 时间；人工 batch 32.230 秒、故障注入 8.587 秒另计。未测 Jev，原生 G2 无实际模型调用。本轮不默认启用 Laya，不将它作为结果或科学准入裁判；人工小样本同样不能泛化为模型整体优劣。

I 保全检查确认本轮 209 个受保护文件全部未变；工程 skill 不在产品包或导出中。最终完整源码和后追加报告使用新的 `i/combined-source-final.zip`、manifest 与 `FINAL_RECEIPT.json`，由 V 再做仓库外一致性核查；不覆盖原 G1、早期 1121/1153/1157/1170/1178/1186 文件证据。R 本轮接收一次精简结果通知，无自动提交或推送。
