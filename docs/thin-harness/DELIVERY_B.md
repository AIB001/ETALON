# B 科学函数与方法记录交付

2026-10-21。本次是已授权的本地工程实现；R4b 保持暂停。独立科学改进收益、真实 PRISM 计算和宿主行为没有在 B 线运行。

## 来源与文件

复用 `F:/life_healthy/ETALON` checkout，分支 `main`，HEAD 和本地 main 均为 `5927aceb346475a6f97550f561e7eb3b291611da`；现场 `origin/main` 为 `0af4297fb7de435d858d54195561883a21526d5e`。I 的共同归档位于 `integration/thin-harness-engineering-20261020/`，其中保留原有 dirty/untracked 科学源码。薄分支来源 pin 为 `d3889e6c0600605f018fb81ce3df1b420d6dcbf9`，B 没有覆盖其宿主实现。

B 修改：

- `src/etalon/research/schema.py`、`journal.py`、`__init__.py`：追加三个方法类型、严格字典解析和同账本校验。
- `src/etalon/campaign/calibrate.py`：先按字节确认工作文件等于 HEAD，再单文件合入上述 origin/main 的方向、NaN/tie、空面板、分子级 enrichment 修复。没有该文件的本地 dirty 科学改动需要覆盖。
- 新增 `tests/test_research_methods.py`，归属已由 I 登记。
- 本交付文件。

其他 research 算法、历史 R3/R4/R4b 文档与原始证据不变。没有修改 PRISM、运行环境或暂存区，没有提交或推送。校准新接口的 population 是真实分子 score rows，A 已获知 `drivers.py` 需同源适配。

## 公共调用与边界

```python
from etalon.active import CampaignStore
from etalon.research import ResearchJournal, MethodVersion, MethodUse, method_record_from_dict

journal = ResearchJournal(CampaignStore(database))
version_id = journal.record(MethodVersion(
    name="candidate", roles=("domain_skill",),
    content_ref={"manifest_sha256": "a" * 64},
    verification_basis="host_reported",
))
use_id = journal.record(MethodUse(
    subject_refs=({"kind": "request", "id": "next-request", "scope": str(journal.store.path)},),
    selected_methods=({"method_version_ref": version_id, "scope": "partial use"},),
    host_conditions={"model": None},
    basis="host_reported",
))
body = journal.get(use_id)
# Wire payloads must include exactly one supported schema and its declared fields:
record_id = journal.record(method_record_from_dict(body))
assert record_id == use_id
```

`method_record_from_dict` 仅接受 `etalon-method-version/1`、`etalon-method-use/1`、`etalon-method-evaluation/1`，拒绝任意 metadata 和未知字段。对象的 `.as_dict()` 含 schema；ID 用既有完整记录 canonical/digest 规则，内容摘要保持 C 原有 bundle 算法。每次写入先快照并重新校验嵌套对象，追加不可变；同 payload 重放不新增事件。每个数据库 connection 自行关闭，不存在长期 journal 句柄需要 close。

`content_verified` 通过 C 的 `skills.verify_snapshot(content_ref)` 实际检查保存的文件清单、路径、大小和字节；`content_ref` 为 `persist_snapshot` 返回的 `{manifest_path, manifest_sha256}`。自报摘要仅支持 `host_reported`。读取旧记录保留“写入当时校验”的历史事实；需要确认当前快照仍在时再调用 verify_snapshot，不能把历史校验当作当前文件仍完好的保证。

`MethodUse` 可以不选、部分采用、组合、停止、改用及追加晚到信息。subject 的 scope 必须是该 campaign 数据库；request/task 是已知作用域内的预期或声明引用，action/result 必须真实存在。`resolve_method_use_refs(ids)` 提供 A 的可选引用校验，空列表合法。方法记录不会触发运行、科学准入或模型更新。

`trigger_event_ref` 是 `{**page["events"][i]["event_ref"], "log_ref": page["log_ref"]}`；复用 A 的 `read_events` 核验日志身份和已提交事件。事件触发不等于使用被工具观察到。目前没有证明“实际采用方法指令”的执行遥测，因此 `tool_observed` 明确拒绝，不能仅凭目录可见、返回内容或动作完成升级。可保留 `host_reported` 及真实事件/脚本证据引用。

`MethodEvaluation` 解析既有 MethodVersion、TaskSplit、Artifact 和结果/费用引用。费用引用支持现有 action ID、resource execution ID、或 `bindings.kind` 为 `accounting/cost_receipt/resource_accounting` 的已校验 Artifact；不会重记费用。diagnostic 允许证据不足，空 result/accounting 不填零。

独立角色只支持有既有冻结/释放 lineage 的路径：

1. 在同 journal 记录 subjects/comparators、协议 Artifact、初始条件 Artifact 和 TaskSplit。
2. 用既有 `policy_version(split, {"method_evaluation": binding})` 冻结，其中 binding 精确包含 `subjects/comparators/protocol_ref/task_split_ref/initial_conditions_ref`。
3. 冻结后登记实际评价证据，证据 bindings 须含确切 `policy_id/task_id`；调用既有 `record_validation` 绑定 held-out task 与冻结初始状态。
4. MethodEvaluation 的 result_refs 引用上述 validation 收据，并列出真实 accounting_refs。核验内容/角色、冻结先后、任务、方法版本、协议和初始状态；以前登记过的同一证据字节即使换名/角色也不能冒充新独立测试。

这只能核验账本可观测的冻结和释放声明，不证明宿主外部从未见过数据，也不自动认证费用完整、效果结论或真实科学独立性。探索性诊断不要求晋升。新方法不能借旧方法的 frozen receipt 宣称自身独立通过。

## 实际验证

解释器：`F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12。用 `importlib.import_module` 核验 `etalon.research`、`etalon.research.schema`、`etalon.research.journal`、`etalon.campaign.calibrate` 均从该 checkout 的 `src/etalon/` 导入。首次直接访问 `etalon.campaign.calibrate.__file__` 因包同名导出是函数而失败，已用 importlib 更正核验；没有隐藏这次检查失败。

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/test_research_methods.py tests/test_research_journal.py tests/test_research_validation.py -q` | passed，20 tests | 方法幂等/不可变、空/组合/晚到使用、真实本地快照篡改、错误引用、事件身份、独立声明拒绝、冻结绑定、旧字节泄露、费用与未知；旧 journal/validation 兼容 |
| 精确全部 `tests/test_research_*.py`：`$researchTests = @(rg --files tests \| Where-Object { $_ -match 'test_research_.*\.py$' }); & .venv/Scripts/python.exe -m pytest @researchTests -q -rs` | passed，84 passed / 5 skipped，23.72 s | 5 skips 为 producer 4 项、pose 1 项，要求显式 `ETALON_TEST_PRISM_SOURCE`/producer snapshot；没有擅自绑定 PRISM 或运行其真实计算 |
| `pytest tests -q -k 'research or calibrat'` 早期扩展检查 | failed：107 passed / 5 skipped / 5 failed | 5 失败全在现有 `test_learning_integrity.py`，`learn/conformal.py` 在拒绝非法参数前 import 缺失 sklearn；已报 I，B 不拥有该文件且未安装依赖 |
| MET-01/03/04/05/06/07 记录后端、RES-08/LEARN-03 数据角色工程检查 | passed，对应上述夹具 | 不代表真实宿主选择能力、真实科学独立隔离或改进收益 |
| HOST-01，真实 PRISM 贯通，LEARN-01/02 独立收益 | not_run | 本线没有实际宿主遥测、新独立任务授权或科学计算；由 V 单列判定 |

回退只针对 B 文件按 I 完整基线逐文件恢复；不得用 HEAD 还原整个 research 目录（这些科学模块原本未跟踪），不得 reset/clean、自动 stash 或覆盖 A/C/I/V 工作。费用账本没有新增真实科学执行，夹具测试使用 pytest 临时目录；本地工程开销未完整计量，不能称零研发成本。

## 2026-10-21 续轮：五个遗留失败的环境归因

用户追加授权后，B 按 V 冻结的 `native_acceptance_20261021.json` 做固定源码、隔离依赖检查；没有修改 `conformal.py` 或原测试。证据入口为 [ATTRIBUTION.json](../../../integration/thin-harness-native-20261021/b/ATTRIBUTION.json)，逐文件哈希和证据时间见同目录 `manifest.json`。

现场逐字节确认：

- `src/etalon/learn/conformal.py` SHA256 `b0fef0ed6ef7205c306e664bf1e2fa6f3b0c6154c105090908687dee2a6ce5dc`。
- `tests/test_learning_integrity.py` SHA256 `fbe05997be64e7d513af0da9c77b9df072ab2be70b0d1a60d8f6fa41fd10909d`。
- 两个文件均与 HEAD、origin/main、origin/codex/refactor-thin-harness 完全相同。隔离检查前后哈希不变；故不能将这五个失败归因为本轮薄 harness 源码回归。

| 原 test ID 后缀（均属 `test_calibration_controls_fail_before_model_fitting`） | 输入 | 原环境 | 同源码隔离环境 | 归因 |
| --- | --- | --- | --- | --- |
| `[controls0]` | `folds=True` | failed，缺 sklearn | passed | 未安装已声明的 test 可选依赖 |
| `[controls1]` | `folds=1` | failed，缺 sklearn | passed | 同上 |
| `[controls2]` | `min_group=0` | failed，缺 sklearn | passed | 同上 |
| `[controls3]` | `min_group=1.5` | failed，缺 sklearn | passed | 同上 |
| `[controls4]` | `alpha=True` | failed，缺 sklearn | passed | 同上 |

原函数在校验参数之前 import sklearn，是三个来源共同已有的顺序；这解释了最小环境为何先抛 ModuleNotFoundError。测试环境依赖补齐后，原有禁止 `Surrogate.fit` 的断言不变，五种非法输入均正常在拟合前被拒绝。不需要修改源码或弱化测试。

仅向 `ETALON/.agent-local/native-b-deps-20261021/` 执行 `pip --target --no-deps`，并在专用子进程中显式加入该 overlay。固定包为 scikit-learn 1.9.1、joblib 1.6.0、threadpoolctl 3.7.0、cloudpickle 3.1.2、narwhals 2.27.1；精确安装命令、下载 wheel 哈希和版本见 `install*-command.json`、`install*-report.json`、`requirements-isolated.txt`。继续使用原 `.venv` 的 numpy 2.5.4 / scipy 1.18.1 / pytest 9.1.1；`dependency-check.json` 确认当前 overlay 的全部生效运行依赖满足版本范围。

| 运行 | 实际结果 | 原件 |
| --- | --- | --- |
| 默认共享环境复现五个原失败 | failed，5 failed | `baseline-failures.log/.xml`、`baseline-command.json` |
| 首次隔离 import | failed before collection：新版 joblib 缺 cloudpickle；元数据检查另发现 narwhals 依赖 | `controls.log`、`controls-command.json`、`overlay-requirements.json`；未覆盖本次失败 |
| 补齐隔离传递依赖后重复五个原用例 | passed，5 passed / 1.08 s pytest 时间 | `controls-02.log/.xml`、`controls-02-result.json` |
| 必要回归：完整 `test_learning_integrity.py` 与 `test_learning.py` | passed，116 passed / 3.56 s pytest 时间 | `learning-01.log/.xml`、`learning-01-result.json` |

116 已包含那五个用例，不能把两次结果相加称为 121 个独立覆盖。可复现命令（cwd 为 ETALON）：

```powershell
.venv/Scripts/python.exe ../integration/thin-harness-native-20261021/b/run_isolated.py controls --attempt 03
.venv/Scripts/python.exe ../integration/thin-harness-native-20261021/b/run_isolated.py learning --attempt 02
```

实际解释器仍为 `.venv/Scripts/python.exe` 3.13.12；ETALON 模块从当前 `src/etalon/learn/` 导入，新增依赖只从隔离 overlay 导入。`isolation-postcheck.json` 在不添加 overlay 的新进程中确认共享环境仍没有 sklearn/joblib/threadpoolctl/cloudpickle/narwhals，numpy/scipy 原路径和版本保留，暂存区为空。没有改共享依赖或永久 PYTHONPATH；默认 `.venv` 不带 overlay 时仍缺该可选依赖，因此本次交付是明确环境下的失败归因与验证，不是声称共享环境已修复。

未运行整个 test extra、独立科学评价或 R4b；无新科学任务费用。安装和执行尝试的已知 wall time 分项留存，完整工程成本未知，不将未计量部分补为零。

## 2026-10-21 功能接缝续轮：FM-06、FM-09/10

共同起点为 I 的 1157 文件 `thin-harness-functions-20261021/baseline.zip`（SHA256 `24b76668c45a72bd5c3517ffa6ef5bf9776f90e07a7f37b707bb36d32419cdc8`）。遵循 V 预冻结 `function_acceptance_20261021.json`（SHA256 `9ff39fe7c2471c8ba6ec6dc6b9d272f988fa39d3f3e2503eecee0ed3b31106f2`）及只读补充判据。B 新增已登记测试 `tests/test_research_function_methods.py`，追加本交付；没有改科学算法。现场对 39 个 research 文件与 campaign/calibrate.py 的合计来源逐项检查，运行前后均匹配共同基线。

实际工程证据：[RESULT.json](../../../integration/thin-harness-functions-20261021/b/RESULT.json)、同目录 `run_evidence.py`、`function-run.log`、`targeted-tests.log/.xml`、命令 JSON，以及独立的 `factorial-fixture/` 输入数据库。解释器仍为 `.venv/Scripts/python.exe` 3.13.12，实际算法从本 checkout `src/etalon/` 导入；精确路径、源码摘要和 Python 签名写在 RESULT 中。本轮不需要上轮隔离 sklearn overlay。

| 范围 | 结果与具体边界 |
| --- | --- |
| FM-06 校准单能力 SDK | passed。调用真实 `calibrate(screen,result,panel,required_recall=1.0)`、`separation(scores,panel)`、`enrichment(scores,panel,population)`。`SyntheticPanelReader` 仅供应明确标记的合成行，不具备生成/派发方法；并非真实 screening run，计算结果也不是原生宿主证据。 |
| 常数/tie/方向/支持/NaN | passed。常量与相等分数不能声称排序；同分数反转方向改变排序判断；混合方向、单类支持不足保持 None；全 NaN 不产生数值排名；重复 pose 按分子计数，丢弃行数保留。输入评分单位声明 kcal/mol；纯 separation/enrichment 没有单位转换能力，调用方须先保证同一测量定义。 |
| 校准后改变选择 | passed。合成 gate 删除唯一 active，recall=0 是已知合成计数而非缺失值，调用方明确选择停用 gate；未派发 campaign。空 registered panel 的 recall=None，不伪装成功；其授权请求被拒绝。 |
| 准入与 revision 保护 | passed。calibrate 本身不生成 token；`require_gate(None,revision)` 拒绝。另行显式 `authorize_gate` 后只覆盖同 revision，改变 revision 仍拒绝。保留率合格但不支持排序可作为带限制的过滤器，不能误报 affinity ranking。 |
| FM-09 一个具体 research 方法 | passed，真实 `ResearchJournal.factorial(contrast_id)->dict` 在既有 journal 上独立运行，无 controller.schedule、policy fit 或 MethodVersion 门槛。完整 cells 1/2/3/7 得描述性交互量 3；缺 `11` 保持 unresolved/interaction=None；混合 count/angstrom 抛 ValueError；诊断 Experiment 不接纳 independent/holdout Artifact。 |
| FM-09 读取副作用 | passed。使用 `CampaignStore(database,read_only=True)` 后调用 factorial；前后 metadata/actions/observations/events/rounds 与预算完全相同，原件为 `journal-before.json` / `journal-after.json`。没有科学动作、标签或费用新增。 |
| FM-10 方法父版本规则 | 已向 C 说明既有实现：parents 解析实际 MethodVersion 及完整记录身份，旧 parentless 记录不能改写，新的 parents=[] 根仍合法。B 无需源码修改；本轮实际原生父版本追加和部分使用由 C 运行、V 独立判定，B 不将接口说明计作原生通过。 |

针对性测试命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_research_function_methods.py -q
```

4 passed；持久证据重跑仍为同 4 项（1.02 s），不是额外独立覆盖。一次直接算法证据脚本测得局部 wall time 0.43094 s；完整工程成本未知。没有扩展不相关回归。

B 与 C 选定的使用场景是已有 campaign skill 的按需“局部二因素描述性比较”reference，不新建 SDK 框架或第七个全局方法注册。fixture 构建函数 `build_research_fixture(new_root)` 只属工程测试，供 C 创建新的隔离输入；产品方法实际调用仍是上述公开 ResearchJournal。可重用的只读调用：

```python
from etalon.active import CampaignStore
from etalon.research import ResearchJournal

journal = ResearchJournal(CampaignStore(database, read_only=True))
result = journal.factorial(contrast_id)
```

输入为已存在的 Contrast，四格引用同任务、固定测量定义的 Observation；coupling 明示共享证据与相关性。结果 `uncertainty=None`、scope 明确“descriptive contrast; no independence or causal identification inferred”。缺格时停止定量交互结论或在另行授权下补证；单位/身份不一致先纠正绑定，不通过凑数继续。任何完整结果都不自动证明机制、因果、独立收益、模型更新或 RSI。

B 另向 I 指出共享 `etalon_calibrate_gates` 仍构造 `Screen(workspace)` 的潜在只读副作用；MCP/Screen 接缝由 I/A 唯一所有者处理，不能用本节 SDK 成功覆盖其验收。R4b 暂停，未读取旧释放标签、派发 PRISM/CRO、恢复计算、修改共享依赖或提交推送。

### 原生 generation timeout 的短依赖诊断

I/C 的真实 native timeout 及 raw MCP timeout 保留原件；B 只追加 `b/dependency-*` 证据，没有修改 A 所有的 generate 算法或共享环境。I 的实际线程栈显示 stdio 主事件循环首次载入 numpy 扩展时停留，最初“worker 线程导入”的猜测不成立。

三个独立短命子进程均成功：继承环境直接导入 SciPy 3.46 s；MCP 默认安全环境直接导入 3.42 s；MCP `build()` 注册后再导入 5.95 s。故不能把问题归为仅安全环境变量缺失、仅工具注册或一般 SciPy 不可用；实际 stdio 会话中的冷导入边界仍需由 I/A 用最终工具调用复测。B 没有声称定位到底层 DLL 根因。

`wilson` 仅为正态分位数调用 `scipy.stats.norm.isf`。对 8 种合法 confidence 和 5 组 successes/trials 共 40 组，使用标准库 `-NormalDist().inv_cdf((1-confidence)/2)` 保持原 Wilson 公式，区间最大绝对差 `2.220446049250313e-16`。建议已交 A：仅此局部计算移除不必要的重依赖，并明确校验有限 `0<confidence<1`；原实现对 confidence=1/越界的 inf/nan 夹取不应被无意保留为伪成功区间。成功数为 0 或 trials 的精确 0/1 端点仍须保留。

这只是局部数值对照和依赖边界诊断，未代替后续 raw/native MCP 修复验收。原科学协议、旧结果和 B 已完成 fixture 原件保持不变。C 新增的 factorial reference 已只读核对，API、3/None 数值、混单位拒绝、描述性范围与费用未知表述正确。

## 2026-10-21 后续 research 可选方法对齐（FM-09）

本轮共同起点为 `integration/thin-harness-research-20261021/baseline.zip`，SHA256 `aab89b2d5a82571569a87e3e4ddae518f2d74f070d1e119084ec2f95232d31f2`，1170 文件。B 只新增 [test_research_optional_methods.py](../../tests/test_research_optional_methods.py) 并追加本节；研究生产算法没有改动，实际核对 38 个 `src/etalon/research/` 文件均与起点逐字节相同。原科学证据、前轮失败和当前暂存区保持原样。

本轮选择三个互补于 factorial 的真实现有函数，不要求宿主全部采用或遵循顺序：

| 方法 | 实际检验与边界 |
| --- | --- |
| `calibration.bind_outcome` | 对持久合成 Observation 使用固定 OutcomeRule；实际数值 .2 映射到 low。写入同一本 Journal 的派生记录和事件，精确重放不新增；不是只读函数。failed/missing/unsupported/not_evaluated 保留原状态字符串，likelihood 与 causal_hypothesis 都为 None。 |
| `diagnostics.condition` | 用前一步真实返回的 probe_id/outcome 检查有限联合模型；支持内条件化仍限定给定模型，不是已校准机制概率。直接使用该函数无需 binder、skill 或 controller。 |
| `diagnostics.exact_program` | 接收真实已绑定 outcome 后，给出 z 的 .1 fixture_credit 可选建议；调用方可以不采纳。failed 反馈返回 model_challenge、空 posterior、program/decision 为 None。没有编译、提交或派发操作。 |

7 项针对性工程测试通过，原命令及结果为：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_research_optional_methods.py -q --junitxml=../integration/thin-harness-research-20261021/b/first.xml
```

`7 passed in 2.66s`；原日志为 [first.log](../../../integration/thin-harness-research-20261021/b/first.log)。除上述结果，还检查 task/condition/unit/method/独立评价/holdout/变字节输入拒绝时事件和预算不变；共享 setup 只计一次，完整报价 .45、预算 .44 或 myopic 单步基线均合法停止；缺失 counterfactual measured cost 拒绝，不补零。确定性科学身份和费用约束没有移到 skill 或弱化。

持久直接调用证据为 [RESULT.json](../../../integration/thin-harness-research-20261021/b/RESULT.json)、同目录 `run_evidence.py`、`evidence-run.log`、`optional-fixture/`。Python 为本地 `.venv/Scripts/python.exe` 3.13.12，实际导入本 checkout 的 calibration/diagnostics/journal/runtime，精确模块路径、hash 和签名见 RESULT。新的 synthetic Journal 原有 fixture 失败支出 .25、未知预留 2，调用后仍 spent=.25/reserved=2；派生记录各新增一条事件，active labels 仍为空。这些费用是**合成账本声明值，不是实测 CPU 或科学费用**；局部脚本 wall time 单列，总工程开销未知。

[INTERFACES.md](../../../integration/thin-harness-research-20261021/b/INTERFACES.md) 已直接交 C，含准确接口、输入/输出、停止条件、数据暴露限制和可复用新夹具 builder。C 的可选产品 reference 与原生行为由 C/V 分别实现和独立检查；本节的 SDK 成功不代替原生宿主证据。B 没有新建 SDK、controller、数据库类型、产品 skill 注册或科学账本。S/φ/G 与模型 M 的独立收益均未运行，R4b 仍暂停，无 PRISM/CRO/旧计算恢复、共享依赖升级、提交或推送。

## 2026-10-21 实际研究使用接缝：配对模型与记录后反馈（FM-09）

共同起点 `integration/thin-harness-research-use-20261021/baseline.zip`，1178 文件，SHA256 `089c4589259232649bc6659e296441efb3578c4bcff2c93da7754eb15e9459e2`。本轮 B 新增 [test_research_use_methods.py](../../tests/test_research_use_methods.py)，只修改生产 `research/runtime.py`；`research/README.md` 纠正编译与操作 preflight 的界限。38 个 research 文件中，其余 36 个与基线逐字节相同；校准/经验模型/诊断算法均未重写。runtime SHA256 `bea93bae34be98dc6c83b4f9f957e500bf0c81a4a73c8b5c71d522bff69e8cd9`。

发现并修复两个实际缺陷：

1. `observed_branch` 原来只核 probe_id，同名探针的错误 condition 也能选择分支。现在匹配计划中声明的全部 bindings，并明确拒绝错误记录类型或不存在的 readout；旧合法签名保持不变。A 从本轮完整基线隔离载入原 runtime，保留接受错条件的原始重现 `a/baseline-reproduction.json`。
2. `record_program` 后模型证据被改字节，原 `compile_first_segment` 仍追加 compiled segment。现在编译追加事务和反馈读取都重验持久模型 evidence 与 reused resource Artifact 的 hash/role，拒绝时不写事件或改费用。A 首次红测在 `a/evidence-second.log` 原样保留；B 另覆盖只改变 existing resource 文件的独立反例。

没有凭空将 Probe.primitive 强制映射为另一套 Experiment.kind，没有新增 task/method/unit 参数或框架。当前探针未声明的身份不能由名称推断；实际科学采用仍需要明确绑定和已有测量准入。`observed_branch` 是直接离散标签入口，数字 .2 仍是字符串 `0.2` 并在 low/high 模型下得到 model_challenge；需要阈值时用已有 `bind_outcome` → `condition`/`exact_program`，不暗中补值。合法 stop program 拒绝编译和查询反馈；model_challenge 结果本来就没有可记录 program ID，不把人工损坏记录冒充实际新缺陷。

本轮有用方法为原 `calibration.empirical_model`：明确配对开发行、独立组、完整 outcome/loss 表和独立定义目标，保留可选 equal-group weighting。三个合成任务 a1/a2 属 A 组、b 属 B 组，显式 group_balanced 得 .25/.25/.5；默认模式拒绝组内重复，单组、重复任务、split 重叠、缺 pair/loss/evidence 均拒绝。模型构造是纯函数，不能检查 Journal Artifact 角色；实际 `record_program` 拒绝 independent_validation/sealed_holdout，且失败原子不写入。权重和分组是声明，不证明真实独立或已冻结未见数据。

实际运行（本地 `.venv/Scripts/python.exe` 3.13.12；模块从本 checkout 导入）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_research_use_methods.py tests/test_research_journal.py tests/test_research_validation.py tests/test_research_execution_seams.py -q --junitxml=../integration/thin-harness-research-use-20261021/b/second.xml
```

`27 passed in 6.63s`，分别为 B 新增 8、原 Journal/validation 10、A 当前 9。原 `first.xml` 的 24 passed 与本次重叠，不累加为独立覆盖；第二次原日志 `b/second.log/.xml`。A 的真实 runtime.plan 输入预检与合成账本保全另见 DELIVERY_A，本轮未执行 worker 或任务，本节不将纯方法检查冒充执行。C 原生宿主和 V 独立验收另行报告。

持久直接 SDK 证据：[RESULT.json](../../../integration/thin-harness-research-use-20261021/b/RESULT.json)、`run_evidence.py`、`evidence-run.log`、`paired-fixture/`。正确 low 返回 stop/keep 建议但 requires_replanning=True；错误 condition 明确 StateError，账本 spent=.25/reserved=2 和 active labels=[] 保留。这些成本是合成声明值，不是实测科学/CPU费用；局部证据脚本 wall 0.3593 s，完整工程成本未知。准确签名、解释器/导入路径/hash 与源码变更集合写在 RESULT；[INTERFACES.md](../../../integration/thin-harness-research-use-20261021/b/INTERFACES.md) 已交 C/V。只读审阅 C 的 paired-models-and-segments reference 与上述接口一致。

回退是使用保留的起点或原冻结快照分析旧运行，不覆盖当前 checkout、历史证据或修改旧账本。S/φ/G/M 独立收益、真实化学/科学任务均 not_run。R4b 继续暂停；无新增 PRISM/CRO、旧计算恢复、共享依赖升级、提交或推送。

## 2026-10-21 按需使用效率轮：可直接运行的 SDK 输入

本轮基线为 `integration/thin-harness-efficiency-20261021/i/baseline.zip`，1186 文件，SHA256 `087729eabbff1c89157e58634af393c6428d65f052585fa099e0e971edd03a2c`。B 实际核验 38 个 research 文件均与本轮基线一致，未重写算法或增加 SDK 框架；保留上一轮 runtime 两项修复。

针对旧原生调用曾宽泛读取源码的问题，B 提供两个完整、独立、无未定义输入变量的 SDK 片段，C 唯一维护并合入现有 campaign references：

- `finite-diagnostics.md` 的 `runnable-example: finite-diagnostics`：真实构造 JointModel/World/Probe/Resource，并示范 JSON 中嵌套 dataclass 的恢复；在同一个局部脚本中依据实际 failed 反馈得到 model_challenge 后停止推断。报价 .1 fixture_credit，与未测实际费用 null 分开。
- `paired-models-and-segments.md` 的 `runnable-example: paired-models`：完整构造配对行、probe/resource、独立组声明及开发/holdout ID，调用原 empirical_model，然后用实际 low 条件结果选择 stop/keep 建议。示例证据 ID 明确未登记/未准入，输出 evidence_admitted=False、heldout_gain=None；不建立科学账本。

片段可分别使用或修改，既不要求完整 workflow，也不要求宿主人为拆成多轮。A 的只读局部批处理另行交付；B 没有重复实现 dispatcher 或批处理库。两片段都是 synthetic engineering fixture，输出不是科学收益，也没有隐含成功费用零值。

新增 [test_sdk_usage_examples.py](../../tests/test_sdk_usage_examples.py) 从**产品 reference 本文**定位稳定 marker 并提取 Python fence 执行。除实际输入可运行外，还验证不足预算停止、high 反馈给出相反终端选择、未知 probe 拒绝、相关组不伪装独立、holdout 重叠拒绝、not_evaluated 保持支持外以及未知费用不补零。运行结果：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_sdk_usage_examples.py -q --junitxml=../integration/thin-harness-efficiency-20261021/i/b/sdk-examples.xml
```

**2 passed in 0.56s**，原件为 `i/b/sdk-examples.log/.xml`。不是用文本字符串包含检查代替执行，也不要求共享环境安装新依赖。

另有两次完整示例子进程记录 [RESULT.json](../../../integration/thin-harness-efficiency-20261021/i/b/RESULT.json)、`run_evidence.py` 与 `evidence-run.log`：实际 `.venv/Scripts/python.exe` 3.13.12，模块从 checkout/src/etalon/research 导入，精确路径/hash、命令、stdout、退出状态和片段 hash 保留；两进程 wall time 分别约 .491/.488 s，仅是局部工程测量，不能当作宿主端到端提速。SDK/runtime 源码不变；无原生宿主质量或 G0/G1/G2 收益声明，这些由 C/E/V 的实际运行单独判定。

R4b 继续暂停；没有真实科学执行、新 PRISM/CRO、旧任务恢复、共享依赖升级、提交或推送。原始失败、未知、预算、证据和所有旧 DELIVERY 保留。

## 2026-10-21 I2 全量 skill 覆盖：B research 与 governance 方法

本轮 B `/root/b_research` 依据 REQUEST 与 I2 WORKSTREAMS 独占归属实施；应用 skill-creator 和本地 life-healthy-contract-refactor，二者不是产品科学方法。原件见 [b/RESULT.json](../../../integration/thin-harness-skills-20261021/b/RESULT.json)、[b/COVERAGE.md](../../../integration/thin-harness-skills-20261021/b/COVERAGE.md)。I2 共同基线为 `integration/thin-harness-skills-20261021/i/baseline.zip`。实际逐字节比对 **38 个 research 基线文件全部不变**，没有改算法、建路由器或恢复科学任务。

新增 [etalon-research](../../.claude/skills/etalon-research/SKILL.md) 一个短入口，五份按需参考覆盖真实公开能力族：

- transfer：GroupSplit/Catalog、fit/calibrate/next_action/evaluate、概率评估、持久策略、联合支持和未知界；明确完整配对行、组/家族隔离、释放绑定与离线复用。
- validation/feedback：冻结决定的释放比较、transfer 与 retained-baseline 反馈、原始角色与 seen 后续用途；附加记录不是模型更新或晋升，oracle 调用不是纯读。
- pose/preparation：pose selection、图/状态/slot/坐标框架、外部 producer/pose 适配；PREPARED/FAILED/NOT_APPLICABLE、pending、认证重用费用和原收据不混淆。
- fixed_pool/online：paid-subset、可见 receipt、pool/rows/workflows、plan/run/额外 counterfactual acquisition；retained-baseline 明确只是原十二任务 R4b 布局与固定权重的兼容实现，不冒充通用新任务 API。
- evidence/panel：Artifact/Experiment/Observation/TaskSplit、scientific revision、面板/选择/冻结预测及可选 finite controller；不会把历史轮次脚本升为必经顺序。

原 campaign 的 factorial、finite、paired 三份方法未改，description 补真实 SDK 名称并链接新 research 能力；新增 [governance-and-fep](../../.claude/skills/etalon-campaign/references/governance-and-fep.md) 对齐八个现有 governance MCP 入口，包括 handoff、stability、admission、spend token、council reliability/adjudication、waiver recommendation 和 FEP network design。没有运行 MD/FEP；网络设计仅本地 RDKit 图计算，不可用分子仍保留 unreachable，缺实测 anchor 的组件只有相对差异。既有真实授权继续有效；没有额外常规审批门槛。

实际可运行方法源是 skill 内 [scripts/research_methods.py](../../.claude/skills/etalon-research/scripts/research_methods.py)，随完整内容版本/导出携带，由 I2 纳入包验证。[examples/research_methods.py](../../examples/research_methods.py) 只作为源码 checkout 的便利调用，不声称 wheel 自带 examples 命令。脚本不打开科学数据/账本、不调用 producer，可 stdout 或独占创建显式 --output 文件；输出已存在时拒绝覆盖。可单独使用任何函数，不需要按完整脚本顺序采用。

实际首次运行输出 [example-first.json](../../../integration/thin-harness-skills-20261021/b/example-first.json)。它用明确合成任务调用现有 fit/calibrate/next_action/evaluate；calibration 比较不当独立 test；将 subset_rank_outcome 与 execute_visible_task 在一个局部脚本组合，正常路径只获取 r0/r1/r2 各一次，缺失评分的真实返回为 failed → unsupported_history/abstain，r2 未取，候选分数保持 null。fixture 路径 .3/.2 wall_second 是**合成 callback 声明成本，不是原生实测费用**，measured_scientific_cost 与 scientific_gain 均 null。pose 示例保留三次尝试与两次 admitted 的分母，最佳评分与最佳已知结构损失不同。

实际解释器为 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12；13 个相关模块的实际 import 路径、SHA256、签名与 11 个交付文件身份在 RESULT。两项 quick_validate 均通过；它们仅是格式检查。

工程行为测试为 [test_research_skill_methods.py](../../tests/test_research_skill_methods.py) 的六项：新版本但不伪造测试释放、可见顺序和终端费用、缺失返回停止、无 policy 的局部使用及未付子集拒绝、实际执行 reference 中 pose/council 示例、显式新输出且未知值保留。选测相关原 research 的 41 项和 FEP 的 2 项，共 **49 个不重复通过用例**。命令/原件：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_research_skill_methods.py tests/test_research_validation.py tests/test_research_preparation.py tests/test_research_transfer.py tests/test_research_online.py tests/test_research_panel.py tests/test_research_controller.py tests/test_research_transfer_feedback.py tests/test_research_baseline_feedback.py -q --junitxml=../integration/thin-harness-skills-20261021/b/research-first.xml
.venv/Scripts/python.exe -m pytest tests/test_research_skill_methods.py -q --junitxml=../integration/thin-harness-skills-20261021/b/research-second.xml
.venv/Scripts/python.exe -m pytest tests/test_fep_network.py::test_an_unparseable_molecule_is_simply_absent_rather_than_crashing tests/test_fep_network.py::test_a_component_with_no_reference_yields_differences_only -q --junitxml=../integration/thin-harness-skills-20261021/b/fep-reference.xml
```

首轮 **46 passed / 1 failed in 18.55s**：新测试直接比较 SDK tuple 与 JSON list 失败；只把断言改成同一 JSON 表示，保留首轮日志，不改 SDK。六项重跑 **6 passed in 2.85s**；FEP 两项 **2 passed in .59s**。三次日志/XML 均在 b/，重复六项不再累加。未重跑无改动的昂贵科学路径；包/导出安装来源和原生宿主行为由 I2/C/V 独立报告，本节不替代它们。

覆盖与方法可执行不等于实际宿主采用或独立收益；M/S/使用策略/改进方法收益均未在 B 线验证。原文/失败/未知/科学 dirty 与 untracked 保留，R4b 继续暂停，无封存标签读取、PRISM 评分、旧计算恢复、共享升级、commit/push。B 产品已通知 I2 稳定，能力族映射在 COVERAGE 供 I2 汇入现有 README，没有新增产品 registry。
