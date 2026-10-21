# A 动作、结果与可靠执行交付

日期：2026-10-21（Asia/Shanghai）。工程实现已交付；真实科学批次保持暂停。没有提交、推送、环境升级或 PRISM 付费调用。

## 来源与文件

复用 `F:/life_healthy/ETALON` 原 checkout，未 stash/reset/clean，未覆盖暂存区。
现场 `git rev-parse HEAD main origin/main origin/codex/refactor-thin-harness`：
HEAD 与本地 main 为 `5927aceb346475a6f97550f561e7eb3b291611da`；origin/main 为
`0af4297fb7de435d858d54195561883a21526d5e`；薄分支远端为
`d3889e6c0600605f018fb81ce3df1b420d6dcbf9`。暂存区为空。可恢复起点是
`integration/thin-harness-engineering-20261020/baseline.zip`、manifest 与 sources.bundle；
不把历史描述中的“main”误写为当前本地 ref。

前轮 A 已保留/合入 active 预算、screen exhaustion、campaign generation/drivers/supervisor/sweep、
preflight 与 screening 的领域修复，本轮在此状态增量实现。保留 research、active/prism.py、
boundary/prism_v1.py、data/design.py 和五个 molcascade Windows 修复。未改 PRISM 仓库或历史科学证据。

本轮主要修改：`active/runner.py`、`active/store.py`；`runtime/api.py`、`schema.py`、`operations.py`、
`service.py`、`store.py`、`executors.py`、`cli.py`、`README.md`；新增 `tests/test_runtime_actions.py`。
共享 MCP、打包与公共契约由 I 维护，ResearchJournal 由 B 维护。修改文件按 LF 保持最小差异。

实测解释器 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12；
`etalon.runtime.api/operations/executors`、`etalon.active.runner/store` 实际从
`F:/life_healthy/ETALON/src/etalon/` 下对应 `.py` 导入。不是仅记录 Git HEAD。

## 接口与语义

```python
prepare_action(request: dict) -> dict
submit_action(request: dict, *, workspace: str | Path, plan_id: str) -> dict
read_action_result(handle: dict) -> dict
read_events(log_ref: dict, *, after_sequence: int | dict = 0, limit: int = 100) -> dict
```

请求示例：

```json
{"schema":"etalon-action/1","request_id":"assess-one","operation":"active.evaluate","arguments":{"database":"F:/task/campaign.sqlite","candidate_id":"one","endpoint_id":"score"},"limits":{"campaign:wall_second":10},"method_use_refs":[]}
```

示例是字段示意，不是已运行科学请求。prepare 返回绑定、报价、能力限制、恢复入口与 plan_id；submit 返回
原 RuntimeStore job 句柄。宿主无须构造 DAG、GP 或 skill；已选候选—端点通过原 reserve/授权/executor/observer/resolve。
旧 active.run 保持数值选择；两者保留已承诺 confirmation_reserve 与协议配额。
新入口可单独构造选中端点执行器，不要求与本次动作无关的全部端点已注册。

plan_id 绑定执行参数、额度、候选、端点、执行器、不可变文件；不绑定 request_id、方法归因或无关事件尾序号。
workspace/request_id 幂等先查原记录，避免余额变化重新报价；改执行语义冲突。迟到归因返回原请求，追加 MethodUse 另行关联。
非空 MethodUse 通过 B 的 resolve_method_use_refs 校验；允许空引用。

结果 `verification.state` 与 `execution_state`、`observations[].admission` 分离。原错误单位结果仍保留
实际完成/费用和 rejected 观测；未知 execution 保留预留。原生执行费用条目通过 execution_cost_refs 引用；
runtime 汇总标 additive=false，未测 overhead=null。既有同 campaign 的非标量 research 费用随原 active_start
收据校验，原费用不丢、当次费用不重算旧研究费；未结研究预留先 reconcile。

事件返回 `{events,next_cursor:{log_id,after_sequence},has_more,log_id,log_ref}`；event_ref 为
`{log_id,sequence,event_type}`，保留 kind/body。campaign 引用 database；runtime 引用 workspace，可按 job_id 限定。
含日志身份的 cursor 跨日志拒绝；空页不推进到不存在序号，空日志首次追加也保持身份。无事件总线。
CLI `etalon action prepare|submit|result|events --config ...` 调用同后端，events 支持 `--cursor`。

普通 hook 不进入事务；result_observer 仍在准入前保存恢复收据，失败不能被普通 hook 吞掉。
恢复沿原 workflow.reconcile、status/cancel/artifact，不重新执行科学动作。

## 已运行工程检查

以下命令均使用上述 `.venv/Scripts/python.exe`，无外部科学派发。pytest fixture 与真实 CPU 属性计算明确区分。

| 命令/范围 | 结果 | 解释 |
| --- | --- | --- |
| `pytest tests/test_runtime_actions.py tests/test_active.py tests/test_runtime_prism_bridge.py -q`（中间组合） | passed：48 | 9 项当时新增动作反例 + 旧 active/bridge 工程回归 |
| `pytest tests/test_runtime_actions.py -q`（扩展组合） | passed：12 | 加入归因稳定、observer 恢复、confirmation 预算检查 |
| `pytest tests/test_runtime_actions.py tests/test_runtime_prism_bridge.py tests/test_runtime_executors.py -q -k 'not prism_build_and_drive_forward_process_observer' --basetemp=F:/lh-a-1021` | passed：41；skipped：1；deselected：1 | 新动作13项与旧执行器/bridge；包含真实本地 CPU RDKit molecular-weight计算；排除项明确依赖 POSIX |
| `pytest tests/test_runtime_actions.py tests/test_active.py tests/test_campaign_integrity.py tests/test_prism_v1.py tests/test_runtime_prism_bridge.py tests/test_design_import.py tests/test_local_windows_cpu_io.py tests/test_campaign.py tests/test_sweep.py -q --basetemp=F:/life_healthy/ETALON/.agent-local/a-final-tests` | passed：404；failed：32 | 30 个 legacy ledger POSIX flock 不支持；1 个 Unix `/tmp` 路径断言在 Windows 不成立；1 个创建 symlink 遇 Windows 1314 权限错误。未删除、改弱或伪称通过 |
| recovery_audit + 新动作中间组 | passed：31；failed：5；skipped：2；deselected：1 | 4 个旧 reconcile 用例在 POSIX 锁前拒绝；1 个新测试多余的 molcascade 导入已修正并在后续41项组通过。真实 POSIX reconcile 本机仍 not_run |
| `pytest tests/test_runtime_actions.py tests/test_active.py tests/test_runtime_prism_bridge.py tests/test_research_preparation.py tests/test_research_journal.py -q --basetemp=F:/lh-a-resource-1021`（最终同账本修复后） | passed：72 | 新动作14项，含历史非标量失败费用+新动作不重复累加及未结预留拒绝；旧 active/bridge/preparation/journal 回归 |
| `git diff --check`（A 本轮修改文件） | passed | 无空白错误 |

初次旧 executor 组另有 62 passed、2 failed、1 skipped：真实 CPU 测试在默认 Windows 长临时根触发
artifact 安全路径检查；原样源码改短 `--basetemp=F:/life_healthy/ETALON/.agent-local/a-cpu-check` 后该测试
**1 passed**，随后 F:/lh-a-1021 再通过。molcascade/artifacts/store.py 与共同基线逐字节一致，SHA256
`1e4c6b8e596f8f09ba0562b24236f7d6dada29cb4f0866f78966d927dc951abb`；没有降低 artifact 完整性。
另一失败是 PRISM process-group 转发用例要求 POSIX，在 Windows 明确拒绝。

新增 tests/test_runtime_actions.py 使用明确 test_fixture executor 和 in-process launch：真实 SQLite 事务、
reserve、收据、费用、准入与事件分页，但不把 fixture 说成真实 PRISM/后台宿主。screen 零命中测试复用
已有 ScreenResult exhaustion 语义并核对 native record，额外 CUDA_OOM 仍失败。

ACT-01/02/03/04/06/08/09、RES-01/02/03/05/06、EVT-01 有上述针对性工程证据；
ACT-05 使用原 budget/round 事务和 campaign_integrity 并发回归；ACT-07 的 bridge 支持域、字段、预算拒绝有组件证据。
RES-04 的真实准备不适用来自历史/研究组件，本次没有重跑准备；RES-07 的真实进程取消在 Windows not_run。
真实宿主由 V 独立报告，真实 PRISM、独立科学收益与 R4b 恢复均 not_run，不宣称 RSI。

## 剩余边界与恢复

Windows 可以 prepare/读取及运行组件检查；detached worker 与原进程身份/锁语义仅支持 Linux/POSIX，
prepare.capabilities 明示不支持原因。没有新增后台 agent 主循环或第二账本。
若要回退本次工程，应按 I 的共同基线及本次文件差量逐文件恢复；不得对共享 checkout reset/clean，
不得覆盖其他线成果或本地科学修改。当前工作保持未提交，最终 wheel/集成 hash 与独立判定以 I/V 交付为准。


## 2026-10-21 已授权原生 POSIX 续验与修补

此前 Windows 的 POSIX not_run 边界由本节新增真实 Linux 工程证据补充；此前失败记录不删除。
用户本轮授权仅覆盖既有资源中的隔离便宜 CPU 工程 fixture，R4b、真实 PRISM 评分、科学收益验证仍未运行。

环境与来源：先复用既有连接器认证只读核对 `root@36.137.130.140:30000` / `instance-3l3cihf6`；未发现相关活动任务后，创建独立
`/root/zx/prism-minimal-20261017/engineering/etalon-thin-native-20261021-01`。实际解释器为其中
`env/bin/python`，Python 3.11.17。新环境只读引用旧 env site-packages，并设置
`LD_LIBRARY_PATH=/root/zx/prism-minimal-20261017/env/lib`；只在新 env 补装固定版本
pyarrow 25.0.1、openpyxl 3.1.5、et_xmlfile 2.0.0，没有升级共享基础依赖。
初次 SSH 无认证、缺 et_xmlfile、RDKit GLIBCXX 路径缺项的失败也保留。认证内容未打印或归档。

旧交付源码解压到 `source`，SHA256 `5f84fc5faa3ba4aaf797bc13c01295a245d76a2cc857fa6ee1cc8560280541a9`；
修补另存 `source-v2`，通过 PYTHONPATH 显式导入。实际模块绝对路径与 SHA 见
`integration/thin-harness-native-20261021/a/raw/evidence/environment.json`、`final-audit.json`。
原源码保留给 C 的独立宿主用例；I 后续 MCP 说明变化不在 A 的 source-v2 中，最终组合以 I 包验收为准。

真实进程覆盖：

- ACT-05：三个真实独立提交进程竞争案例（同 request、不同 request 同候选、不同 request 不同候选），共享同一 CampaignStore/RuntimeStore，真实 barrier、OS flock、SQLite 事务和 detached worker。每案仅一个科学动作、无负余额、无重复预算占用；同 request 返回原 job，其他竞争者拒绝或 blocked。仅有限三个调度案例，不是所有交错的形式证明。
- ACT-06：真实 CPU RDKit 执行完成且 action 收据落盘后向原 worker 发送 SIGKILL；沿原 job/process 身份 reconcile，执行计数保持 1，收据不变，重放不重派。
- RES-07：显式受控子进程 fixture，真实 PID/start identity/process group 与 process observer。子进程仍活动时 cancel 不报 cancelled、不退预留；子进程退出但缺最终结果收据时仍 reconciliation_required。提供真实测得最终失败费用后才终结，scalar value 保持 null。最终 child 实测费用与账本均为 0.4234823156148195 wall_second。锁、_launch、身份、cancel、reconcile 未 mock；fixture executor 与故障注入在测试中明确标记。

真实 v1 暴露 `_settle_failure` 重复计入已测 partial 的缺陷：workflow 总费用错误要求“partial + 最终总额”，
从而拒绝合法结算。现改为“其他终态费用 + unresolved 动作最终总额”，并在写收据前拒绝最终费用低于
已记录 partial。保留原 active_result 费用核验，没有退款或低报通道。两个针对性反例覆盖正常结算与低报拒绝。
生产 `runtime/service.py` 最终 SHA256 为
`ffff910947feeb153f535dc8b1e15eb4c8fd07ac54b779ce515dadb2623f50fa`。

| 本轮实际检查 | 结果 | 保留的失败/边界 |
| --- | --- | --- |
| 既有真实 CPU executor 最小案例 | 1 passed | RDKit molecular-weight 工程计算，不是 PRISM 评分 |
| POSIX v1 | 3 passed, 1 failed | 暴露 partial 费用结算缺陷 |
| POSIX v2 + recovery audit | 32 passed | 修补后，尚为两个 race 参数 |
| POSIX v3 + recovery audit | 32 passed, 1 failed | 扩至三个 race；SIGKILL /proc 判死到 flock 实际释放的短暂窗口，真实锁正确拒绝 |
| POSIX v4 + recovery audit | 33 passed, 1 warning | 只修测试同步：有界获取真实 owner lock，保留身份/时间/尝试日志，确认原收据与执行计数不变后 reconcile；只捕获明确 live-owner，其他错误仍失败 |
| 新 partial 两反例，Windows | 2 passed, 26 deselected | 有针对性组件检查；未当作 POSIX 证明 |
| 原 campaign/sweep，Linux 同源首轮 | 246 passed, 4 failed | 归档无 .git 导致 revision 为空；源码不变 |
| 恢复隔离 Git 来源后的相同 campaign/sweep | 250 passed | 既有 sources.bundle + 独立 Git 元数据/索引，HEAD5927 与 dirty 来源保留；未提交、未操作用户暂存区 |
| 原 Windows 32 精确复测 | 32 failed | 30 flock unsupported、1 Unix tmp、1 symlink 1314；未掩盖或声称修复 |

原 32 个失败逐 ID、原错误、Linux 同源状态、Git 上下文失败重叠关系见
`integration/thin-harness-native-20261021/a/original32-classification.md` 与 `.json`。
其中 4 个 Git 上下文失败单列（3 个与原32重叠）；不能以 250 passed 隐去这些尝试。
原相关 5 个实现/测试文件与旧交付归档逐字节相同，见 `original32-source-identity.json`。
本结论归因本轮平台/上下文差异，不扩大为整个重构前后所有行为的等价证明。

最终 V4 测试已同步到 `tests/test_runtime_posix_acceptance.py`，与下载原始远端文件逐字节相同，SHA256
`72793173ce7f8c1d42d2ab674e9b14356e657121f7ee64cd364cd8ebd6286fbf`。
`tests/test_runtime_recovery_audit.py` SHA256
`4fda61a04d436c3cfbb21eac7a3e5a3628be0282f499785b5aece56f68175909`。
V4 同步不改变生产实现。V 独立核对 29 个 v4 RuntimeStore 的收据 body SHA、原 SQLite、进程身份与费用。

封口与原件：完整远端证据
`integration/thin-harness-native-20261021/a/native-a-evidence-final.tar.gz`，SHA256
`fc4223c2cee158fde5f1b4c7b329488dafbb2724d0659864831113a49209627c`。
原件含所有版本 stdout/XML、SQLite/收据、执行计数、进程身份、活动进程审计；V 已独立读取。
本机解压保留 1617 个普通文件于 `a/raw`，373 个 pytest symlink 别名不展开（原 tar 均保留），
安全拒绝绝对链接与 Windows 长路径的初次解压失败记录亦保留于本次日志/提取说明。
最后进程审计仅针对本工程目录，`active_engineering_processes=[]`，未广泛杀进程。

source-v2 初始 manifest 在 ZIP central directory 关闭前计算了 SHA，错误未封口值
`b2d9f6c2a828d00c0f52ed7e3b1d0d24a491735902845bd79048e218ba8c3790` 保留并明确作废。
本机/远端封口 ZIP 一致的正确 SHA 为
`dd5d939594d0698386464994ceb2118a7848fcce06ab83f779891e6cd9f2691d`，更正见
`a/source-v2-sealed-manifest.json`，旧 manifest 未覆盖。source-v2 内含 V2 测试，最终已测 V4 单独归档并同步仓库；
不能把 V2 ZIP 单独冒称完整 V4 测试组合。

以上为工程 fixture 与真实便宜 CPU 计算，不是独立科学改进收益；真实宿主另由 C/I/V 报告。
没有新云、WSL、共享部署覆盖、旧计算恢复、付费科学任务、CRO 订单、提交或推送。


## 2026-10-21 function-migration：FM-05/06/07/08 最小场景

起点为 I 保全的 1157 文件 `integration/thin-harness-functions-20261021/baseline.zip`，SHA256
`24b76668c45a72bd5c3517ffa6ef5bf9776f90e07a7f37b707bb36d32419cdc8`。沿用同一未提交 checkout；
开始核对 Git dirty/untracked，保留全部已有科学和本地修复。I 核验暂存为空且无相关活动进程。
本轮不重跑 native 阶段已通过的进程案例，不调用 PRISM、不恢复 R4b。

### 实现与原能力边界

FM-05 原 `Screen.plan/run` 和 `screening.plan_screen/submit_screen/screen_status` 已允许直接提供已有 CSV、
仅选择一个自定义 CPU 筛选配置，未发现必须先生成、先建 campaign、先选 skill 或全局 plan 的依赖。
真实小批次却发现实际接缝缺陷：LocalRunner 已写 FAILED 与 `FEATURE_EMPTY_INPUT` 收据后仍抛异常，
Screen.run 未将其变为 ScreenResult，导致 screening worker 丢失原结果。
现在仅本次执行写入了与请求相同 run/revision 的新 FAILED 结果时返回原 ScreenResult；
原状态、错误 code、已提交产物均保留，由原 outcome 分类区分 exhausted 与 failed。
已有 run 且 resume=False、RUN_ALREADY_EXISTS/RUN_LOCKED/RUN_LOCK_FAILED 一律保持拒绝，
不能吸收并发另一写入者的失败记录。尚未形成 durable 结果的异常仍抛出。

FM-07 复用 `generate.productivity.Chunk/Productivity/allocate` 与 `Pipeline.dry_run`：
无需实例化生成器、Campaign 或 Supervisor 即可分析已给定的产率记录、只选一个候选 loop，
也可完全跳过生成而计划已有分子。建议没有隐式执行副作用，既有 generation/pipeline/propose/supervisor
实现逐字节保留。Supervisor.tick/run 仍是显式选择的局部编排，包含 ingest/emit/recover/dispatch，
不是只读查询；本轮没有声称其中全部策略都已拆解或建立新的优化算法。

FM-08 与 FM-06 共享读取接缝发现构造副作用：旧 MCP sweep status 构造 Sweep 时会创建/迁移 pool，
progress 和 calibration 构造 Screen/LocalRunner/ArtifactStore 时会补目录。
新增最小只读入口，I 将相关 MCP 转为薄调用，B 的校准算法不复制、不改义：

```python
from etalon.campaign.sweep import read_sweep_status
read_sweep_status(pool, *, batch_size=DEFAULT_BATCH_SIZE) -> dict

from etalon.boundary.screen import Screen
Screen.read_state(workspace, run_id) -> ScreenResult | None
Screen.read_progress(workspace, run_id) -> dict
Screen.open_existing(workspace, *, infra=None) -> Screen
```

`read_sweep_status` 保留旧 state 形状；只读事务在一个快照内读取计数，旧缺 gate/etalon 列投影 null，
不做 schema/index 迁移，缺库与不支持 schema 明确拒绝。SQLite `mode=ro` 仍使用真实读锁并读取最新 WAL；
允许共享内存 SHM 的读锁簿记变化，不声称全部 SQLite sidecar 字节绝对不变，不用 immutable 忽略活动 WAL。
测试对比 schema/科学行/主库/WAL；不存在路径查询无目录/数据库/sidecar 创建。

`Screen.open_existing` 返回 read_only 适配器，缺工作区拒绝；recall、parent_names、artifacts_carrying、read
复用既有算法与原验证，run/export_shortlist/with_handoff 明确拒绝写入。
MolCascade 既有 LocalRunner/LocalArtifactStore/LocalStageCache 增加默认 True 的 `create` 参数；
只读组合传 False，不补建缺失目录，不完整旧布局明确拒绝而不修复。
`read_run_state` 是原 load_run 规范 JSON/尺寸/身份校验的提取，旧 load_run 复用同一实现；
不是另造运行器、事件总线或第二本账本。I 已登记本轮额外 artifacts/store.py 唯一归 A。

实际修改仅：`boundary/screen.py`、`campaign/sweep.py`、
`asset/molcascade/src/molcascade/{runtime/runner.py,runtime/cache.py,artifacts/store.py}`，
新增 `tests/test_function_capabilities.py`；共享 MCP 归 I、校准归 B、产品 skill 与原生宿主归 C。
原 Windows 路径/缓存修复保留。回退应按本轮 baseline 逐文件差量处理，不覆盖其他线或 reset/clean。

### 实测与保留的失败

实际解释器 `F:/life_healthy/ETALON/.venv/Scripts/python.exe`，Python 3.13.12；
ETALON 从 checkout/src/etalon 导入，MolCascade 从 checkout/asset/molcascade/src/molcascade 导入。
精确路径和 SHA：`integration/thin-harness-functions-20261021/a/final-v2-imports.json`；
与基线逐文件比较：`a/source-comparison.json`。

| 场景/命令范围 | 结果与边界 |
| --- | --- |
| `test_function_capabilities.py` 最终 14 项 | passed；真实 RDKit CPU 单批 strict MW gate→exhausted，停止查询无重跑；后续新 config/new run→committed，旧收据/产物不变；真实 `_worker` 保留 succeeded/exhausted 与原 native FAILED；坏 CSV 保留 failed、旧 run 拒绝；独立产率与跳过生成；旧库/WAL只读；未知动作预算；只读校准读取 |
| 上述新用例 + 12 项旧相关 screen/sweep/runtime executor 回归 | **26 passed in 17.17s**；完整命令与退出码见 `a/final-v2-command.json`，stdout/XML `final-v2.log/xml` |
| 原生 OS 并发/杀进程/取消 | 本轮 not_run；沿用此前已封存 native 证据，不复跑充数 |
| 新并发拒绝 3 个 code 反例 | passed，但为明确注入的 interleaving 单元反例，不称为 OS 锁测试 |
| 真实宿主、非 editable 包 | 由 C/I/V 分别运行与判定，A 的 SDK/worker 用例不代替 |
| `git diff --check`，本轮 A 文件 | passed |

首轮 `first.log/xml` 为 4 passed/2 failed：一个是真实零命中缺陷，一个是新测试 RunState fixture
错用了 created_at（实际要求 started_at）。修补后 `second` 6 passed、`third` 8 passed；
`targeted` 21 passed、`final` 22 passed 是只读校准扩展之前的候选组合，不冒称最终源码验收。
只读扩展首轮 `readonly` 11 passed/3 failed，三个失败均为新增 StageOutcome fixture 缺必填 artifact_id，
补为 None 后最终组合 26 passed。原失败日志未覆盖，没有降低现有断言或跳过正确性检查。

未知动作 fixture 由真实 CampaignStore 创建 running reservation，再记录明确 fixture partial 0.25；
只读 status/export 后 reserved=2、spent=0.25、observations=[]、事件/收据/数据库内容保持。
它证明未知状态读取不退款，不声称实际运行过外部子进程。
真实筛选的费用口径仍为 `cost_status=not_metered`，不能把免费查询、耗时或零命中当成科学计算费用 0。
产率数值来自明确给定的 fixture chunk 记录，建议不当作新测得生成收益或科学效果。

实际产物/SQLite/收据/日志保全于 `a/fixtures-final-v2.zip`，SHA256
`16d07193fa4749ea89690a9357d0149a4dff2eee2bb30554dbb98d266d08e912`。
最终测试源 SHA256 `5964bab1911684bcb78085af66753ba11c0ad3ac0bb702fb363fcee5d0984d08`。
生产 SHA：screen `0ea9cc5bad25fd76cf72eb5505a82c1c677fc5e5824083a27b176d4a13ba0a9c`；
sweep `9db48faf72fdd1e39bed227f5034b97f5c32ff90683f8ff6017892ea951db402`；
runner `80ebeb15664ca0f650c9fca799e480cb3eedf84c57754b7c7988f18d835b37ed`；
cache `50cbb6eb92a5985173d3f366fe5f9677060ef99038119a52c7b5992eeabbdc59`；
artifact store `66d25df7fa338efdb877510ca06f36014f95b6429b9aeda95112a03baa7efa98`。

本轮没有生成框架、强制策略顺序、全局 router、后台科学 agent、共享环境改动、PRISM/CRO任务或自动提交推送。
FM-05/07/08 结论仅针对上述可独立调用与停止/换法/只读边界，不把已有能力复用称为整个旧 workflow 解耦，
也不把工程 fixture、方法说明或宿主试用称为 S/φ/G 独立收益。


### 本轮追加：产率工具冷调用的标量依赖修复

C 原生调用 120 秒超时、I 独立 fresh MCP 20 秒超时后，B 定位栈停在
SciPy→NumPy 冷导入 create_module；共享数值环境根因未建立，不将其说成 NumPy 本身已修复。
I 登记本次额外 `src/etalon/generate/audit.py`、`tests/test_generation_audit.py` 归 A。
原超时、旧源码和包保留于 I/C/B 本轮证据，不升级或重新安装共享依赖。

Wilson 原公式只需要一个标量标准正态分位数。现在以标准库
`-statistics.NormalDist().inv_cdf((1-confidence)/2)` 替换 `scipy.stats.norm.isf`；
不改变 Wilson 公式、默认 confidence=0.95、原 count 拒绝及 s=0/n 精确端点。
使用下尾概率避免 `(1+confidence)/2` 在接近 1 时舍入为 1；明确拒绝非有限或不在 (0,1) 内的
confidence，避免旧 NaN 被 min/max 夹成可信区间。原生成策略/allocate/retire 均不改。

本机同一 Python 3.13.12 实测 **69 passed in 85.61s**，包括完整 generation audit、pipeline plan、
8 项旧产率测试及独立调用场景。新增 25 个 confidence×n 参数组共 100 个 s/n/confidence 对照点，
置信水平为 .5/.9/.95/.99/nextafter(1,0)，与真实 SciPy 原公式按 rel=2e-14、abs=5e-16 比较；
另有 7 个无效 confidence 和 4 个非法计数反例。zero uniqueness 仍有退役建议，
cost_multiplier 仍为 None，不补成 0。完整命令/退出码、stdout/XML：
`integration/thin-harness-functions-20261021/a/wilson-command.json`、`wilson.log/xml`。

实际模块导入路径与两个源文件 SHA 记录在 `a/wilson-source.json`；生产 audit.py SHA256
`5c141540996d685ddee2c7418bfe03a278072f0d0482518e228cd54bdd983470`。
新的标量模块导入不加载 NumPy/SciPy；此普通 Python 导入检查和数值 pytest 不替代原生宿主。
I 已另行完成真实 fresh stdio MCP 的零预加载启动、产率请求及后续请求检查，C 新固定快照原生复测
由 I/C/V 单列判定。第一轮的 source-comparison/final-v2-imports 保留其当时版本；
该追加变更以 wilson-source 和 I 后续组合快照为准，不回写旧 manifest。

## 2026-10-21 接续：旧 Campaign / Supervisor / propose 的可选边界

本轮共同起点为 workspace `integration/thin-harness-research-20261021/baseline.zip`（1170 文件，SHA256 `aab89b2d5a82571569a87e3e4ddae518f2d74f070d1e119084ec2f95232d31f2`）。复用现有 checkout；旧 dirty/untracked、原始科学证据和上轮失败原件均保留。对应 FM-07/09，沿用 ACT-02/08、MET-04、HOST-01 的能力边界；本节是 Python SDK 工程检查，不是新的原生宿主或科学收益结论。

### 结论与改动

未发现独立 `ParameterChange.judge`、`Acquisition.propose`、`Pipeline.dry_run` 必须先进入旧 Campaign / Supervisor 的运行 gate。`Campaign.round` 仍是用户显式选择的固定兼容编排；`Supervisor.tick` 仍是显式机械调度，不作为查询或宿主必经路线。无需为薄化另拆正确算法，原数值策略和有效的校准、身份及权限约束保持。

只修正 `campaign/loop.py`、`propose.py`、`pipeline.py` 的误导说明：选择旧 loop 才选择六步骤；独立建议不派发、不预留、不自动应用；`ParameterChange` 实际使用 `WHOLE_PANEL`，对象名为 Panel 不能证明独立 holdout，重复适应性使用属于开发证据。Pipeline 可自选 stages，给 Acquisition 的预算可以直接指定，数值计划不授予执行权限。按 AST 去除 docstring 后，三文件与基线可执行 AST 完全相同；Supervisor 文件逐字节未变，详情见 [证据与导入清单](../../../integration/thin-harness-research-20261021/a/optional-evidence.json)。

### 实际检查

新 [test_optional_campaign_methods.py](../../tests/test_optional_campaign_methods.py) 4 项：

- 真实 MolCascade/RDKit featurization 和 `ParameterChange.judge` 只用合成 40 行开发 panel：常数对常数返回 within_noise；调用者据此换可见的合成 prefix ranker，得到 accepted 后停止。Campaign / Screen 构造及 Supervisor.tick 被 spy 拒绝，临时目录无产物；旧 proposal 保持不变。这只是方法可选和换法能力，不是新算法收益。
- 空候选 `Acquisition.propose` 在 fit 前明确拒绝，原 `as_loop_acquirer` 的空候选返回 None 保持；非空 surrogate 拟合本轮未运行。
- `Pipeline(stages=("fixture-validity",))` 只采用一个自定义 stage，不运行生成或默认其他 stage；请求缺失昂贵 stage 的预算明确拒绝，随后可停止。
- 显式构造的既有 Supervisor 在 `generators=()` 时可只读 state/productivity，不调用 tick、不启动 Screen；1/2 尾批仍报告 `unscreened_remainder=1`，不是把 complete 解释成全筛完。查询后池行相同、无 ledger 文件。缺 gate 的 flush 仍抛真实 NotAuthorized，未弱化为 no-op。

Supervisor / Campaign 的构造本身仍会创建显式运行工作目录，Sweep 构造仍初始化池；**本轮不把它们宣称为只读构造入口**。通用只读监控继续使用已经验收的 `read_sweep_status` / `Screen.read_progress`。MCP 没有把旧 Campaign / Supervisor 作为独立查询和建议的前置。

最终针对性命令（实际 `.venv/Scripts/python.exe`，Python 3.13.12；etalon 和四个 campaign 模块均从本 checkout `src/etalon` 导入）：

```text
python -m pytest tests/test_optional_campaign_methods.py tests/test_campaign.py::test_the_panel_scored_change_does_not_claim_a_holdout_it_did_not_take tests/test_campaign.py::test_the_panel_cannot_resolve_a_small_improvement tests/test_campaign.py::test_too_few_positives_is_underpowered_rather_than_a_verdict -q
```

结果 **7 passed**（4 新＋3 既有），[日志](../../../integration/thin-harness-research-20261021/a/optional-targeted.log)、[JUnit](../../../integration/thin-harness-research-20261021/a/optional-targeted.xml)。真实解释器、模块路径、SHA、合成 proposal 完整输出、AST 对照见上方 JSON。

原失败不覆盖：[首次 3 passed / 1 failed](../../../integration/thin-harness-research-20261021/a/optional-first.log) 是新测试误期望 SweepError，实际权限拒绝 NotAuthorized 正确，修测试明确接受该原类型；[第二次 7 passed / 1 failed / 41 deselected](../../../integration/thin-harness-research-20261021/a/optional-final.log) 是宽 -k 选择器额外选中旧 Campaign 完整 round，Windows 缺 POSIX flock 的既存限制再次出现，未 mock/屏蔽/改实现。最后使用精确节点检查实际所改文案和独立调用，旧失败保持失败记录，不称本轮完整旧 round 通过。

本轮未运行 POSIX Campaign round、非空 Acquisition 拟合、原生宿主采用这些方法或独立 S/φ/G 收益。没有真实 PRISM 派发、R4b 恢复或新科学账本；合成计算 CPU 费用未计量（not_metered），不补成测得零。未提交、推送或升级依赖。

### 同日追加：非空 Acquisition 的真实局部拟合

I 在本轮 pre-native 源封口后要求补一个有区分力的非空 acquisition 案例，追加于同一 [test_optional_campaign_methods.py](../../tests/test_optional_campaign_methods.py)。**测试和本节晚于 pre-native 快照，不宣称已包含在该原生受测源码里；产品源码未因本追加而改动。**

使用 36 个明确合成 SMILES / 人工 affinity（6 个不同 scaffold，每类 6 行），真实运行 `Acquisition.propose` 的 MolCascade featurization、400-tree surrogate fit、五折 grouped cross-conformal calibration 及四候选中的二个选择。未替代数值函数、无历史标签。Campaign、Supervisor、Screen、Ledger 的构造被 spy 拒绝；返回 Proposal 后直接停止，候选未变且临时目录无文件。`act=spend/autonomy=acted_on` 是既有提案元信息，不是实际执行或已支付证明。

既有隔离 overlay `ETALON/.agent-local/native-b-deps-20261021` 通过本次新 Python 进程的 `sys.path` 复用，没有 pip 安装或共享环境升级。实际解释器仍为 `.venv/Scripts/python.exe` 3.13.12；sklearn 1.9.1、joblib 1.6.0 来自该 overlay，NumPy 2.5.4、SciPy 1.18.1、RDKit 2026.9.1 来自原共享 .venv。仅该测试子进程设置 `LOKY_MAX_CPU_COUNT=2` 限制便宜 CPU 工作，算法树数/默认策略未改。

命令为在新进程置上述 overlay 后调用 `pytest.main(["tests/test_optional_campaign_methods.py::test_nonempty_acquisition_fits_local_synthetic_panel_and_only_returns_advice", "-q", "-s", "--junitxml=.../acquisition-first.xml"])`。首次实际运行 **1 passed in 8.15s**，没有失败重试；完整真实 proposal、依赖版本/路径、源码 SHA 见 [日志](../../../integration/thin-harness-research-20261021/a/acquisition-first.log)、[运行元数据](../../../integration/thin-harness-research-20261021/a/acquisition-first.json)、[JUnit](../../../integration/thin-harness-research-20261021/a/acquisition-first.xml)。[事后检查](../../../integration/thin-harness-research-20261021/a/acquisition-postcheck.json)确认共享解释器仍找不到 sklearn，产品源码哈希不变。

该追加将“本轮非空 Acquisition 未运行”更新为“单个合成 SDK 局部拟合工程例已通过”；前文保留其当时状态。真实返回 `calibration_count=36`、`groups_measured=6`，coverage_scope 明确为 in-sample calibration-residual diagnostic；不得将这些 coverage 或合成排序视为独立泛化、S/φ/G 改善或科学收益。仍未派发任何真实科学任务，不恢复 R4b；墙钟只为工程处理开销，不是测得的 PRISM 科学费用。

## 2026-10-21 再接续：research 首段到可靠执行内核的真实接缝

本轮共同起点 `integration/thin-harness-research-use-20261021/baseline.zip`，1178 文件，SHA256 `089c4589259232649bc6659e296441efb3578c4bcff2c93da7754eb15e9459e2`。A 只新增 [test_research_execution_seams.py](../../tests/test_research_execution_seams.py)、本轮 a/ 工程证据及本节；`research/runtime.py` 由 B 唯一修复，A 没有跨线改实现。旧来源、dirty/untracked、科学身份、molcascade 修复和 R4b 暂停边界不变。

发现并交 B 的两个真实缺陷：

1. 原 `observed_branch` 只比 probe_id，错 condition 的观测也能返回本模型的后续分支。B 在 A 首次测试前已修正，因此首次 7 passed 不是声称先跑过红测试；A 另从本轮 baseline.zip 直接加载原 runtime 源到隔离测试模块，使用新合成 Journal 实际复现错误接受。原件见 [baseline-reproduction.json](../../../integration/thin-harness-research-use-20261021/a/baseline-reproduction.json)，记录原源 SHA 和错误返回，不覆盖 checkout 或旧科学记录。
2. `record_program` 后模型证据文件被改字节，原 `compile_first_segment` 仍追加新段。A 新增反例实际跑出 [evidence-second.log](../../../integration/thin-harness-research-use-20261021/a/evidence-second.log) 的 **1 failed / 7 passed** 后交 B；B 在编译事务中复用 Artifact 字节/角色校验，并在观察分支时同样重验。原失败保持，不改弱断言。

最终新 9 项 **9 passed in 3.67s**：[plan-third.log](../../../integration/thin-harness-research-use-20261021/a/plan-third.log)、[JUnit](../../../integration/thin-harness-research-use-20261021/a/plan-third.xml)。覆盖：仅编译选中第一节点、未来非法 operation 分支不提前装载或执行；缺绑定、未来 `$ref`、未注册 operation 拒绝且无追加；合法 stop 明确 StateError 且账本不变；错 condition / 改字节证据拒绝；failed 仍返回 model_challenge、program=None、requires_replanning=True，原 .25 失败支出与 2 pending 预留不丢。

另外真实调用原 `runtime.service.plan` 核对消费边界：普通合成 readout JSON 可以通过 workflow schema 编译，但作为 design bundle 在真实 plan 阶段明确拒绝，故 compiled_not_executed 绝不是“已可执行”。在新隔离目录复制已有 `tests/fixtures/prism_design_bundle_v1` 工程夹具后，同一个首段编译结果可交给原 runtime.plan，真实绑定 **27 个源文件哈希、1 个节点**；重放 plan 相同，没有 import、candidate、科学 label、submit 或 worker。本例复用历史工程结构夹具，不能宣称新的科学输入或未见科学验证。

持久重现入口：[run_evidence.py](../../../integration/thin-harness-research-use-20261021/a/run_evidence.py)、[RESULT.json](../../../integration/thin-harness-research-use-20261021/a/RESULT.json)、[evidence-run.log](../../../integration/thin-harness-research-use-20261021/a/evidence-run.log)。实际解释器为本地 `.venv/Scripts/python.exe` 3.13.12，research.runtime/journal、runtime.service/operations、data.design 均从本 checkout `src/etalon` 导入，精确路径/hash 已记 RESULT。该持久脚本约 .958253 wall_second，仅本次局部工程处理开销；spent=.25/reserved=2 是原合成夹具声明值，不是实测科学费用。

本轮没有绕开执行内核的预算/单位/准入或原 Linux /proc worker 限制。`compile_first_segment` 的 operation mapping 仍是调用方显式提供的绑定，不自动认证它与科学 primitive 的等价；编译、真实 runtime 输入计划、实际执行和科学准入分别记录。本轮到 **compiled + runtime-planned** 为止，worker/实际科学计算未运行。普通模型成本单位不能因 active.evaluate 需要 campaign 前缀就暗中改写；最终预算/注册执行器校验仍属原执行内核。

这些检查沿用 ACT/RES/MET/INT 的来源与可靠性标准，不引入新 controller/router/科学账本。无 PRISM 派发、付费任务、R4b 恢复、共享依赖升级、提交或推送；原生宿主实际采用与独立收益由 C/V 另行记录，不由本节 SDK 工程检查替代。

## 2026-10-21 效率接续：宿主可选的局部结果批读

共同基线 `integration/thin-harness-efficiency-20261021/i/baseline.zip` 为 1186 文件，SHA256 `087729eabbff1c89157e58634af393c6428d65f052585fa099e0e971edd03a2c`。A 独占新增 [examples/local_sdk_batch.py](../../examples/local_sdk_batch.py) 和 [tests/test_local_sdk_batch.py](../../tests/test_local_sdk_batch.py)；可靠执行生产源码、研究源码、MCP、公共契约均未修改。本轮 REQUEST/COORDINATION 的 I/E/R 衔接优先于历史不回传安排，E 自有文件不触碰。

### 实际调用及边界

提供一个可直接使用、修改或完全不用的本地例子，不是新 SDK 框架、批处理 MCP 或 router：

```python
from examples.local_sdk_batch import read_action_results
summary = read_action_results(handles, "new-output-directory")
```

其中 `handles` 为现有 `submit_action` 返回的完整 `etalon-action-handle/1` 对象列表；在仓库根目录可直接导入示例，也可运行：

```text
python examples/local_sdk_batch.py --handles handles.json --output new-output-directory
```

该文件是源码示例，不声称安装 wheel 带有 examples。函数逐项调用原 `runtime.api.read_action_result`，每项完整返回或读取错误另存 JSON，以绝对路径、SHA256、字节数引用；stdout 只打印当前状态、验证、准入摘要、费用/未知/恢复及原件引用。单项读错与执行失败分开：坏句柄是 `read_status=failed, execution_state=unknown, accounting_refs=None`，不假称执行失败、不补费用零，也不阻断其他独立读取。所有分支只给可选注意事项，绝不自动 submit/retry/reconcile。

原始 `costs`（含单位、spent、reserved、basis）、Campaign 的 charged/reserved/overhead、nonadditive 和 derived_from 保持；详细 execution_cost_refs 在完整原件中可按需读。重复成功读取标记 `same_execution_as_item`，提示不可重复加总，同一请求的读取不会重复记账。摘要只服务状态/恢复判断；科学判定仍需按需查看原件和准入证据，不把 verified 执行等同于科学成功。每次要求全新输出目录，拒绝覆盖已有原件。这些只是观察快照，不是第二科学账本。

### 验证与失败保留

真实 durable RuntimeStore 中创建明确的工程夹具状态（没有 worker）：已付失败、未知结局、已完成；测试依次读取已付失败、坏身份句柄、未知、已完成、重复失败。使用真实 SDK 和独立 Python CLI 读取，不 mock `read_action_result`。失败仍 spent=1.25；未知仍 spent=None/reserved=2，恢复 required=True/automatic_retry=False；坏项后续成功项正常返回。比对原件与原完整 API 输出、SHA/大小、状态/事件/费用均未变。第一次读取和重复读取都不新增任何账本记录。

最终 **2 passed in 1.55s**：[final-v2.log](../../../integration/thin-harness-efficiency-20261021/i/a/final-v2.log)、[JUnit](../../../integration/thin-harness-efficiency-20261021/i/a/final-v2.xml)。[first.log](../../../integration/thin-harness-efficiency-20261021/i/a/first.log) 的原 2 failures 保留：测试夹具手写 action workflow 缺少强制 action_binding，契约正确拒绝；修夹具改用真实 `api.prepare_action`，没有放松生产校验。中间 second/final 记录也原样保留。

实际 CLI 运行材料：[RESULT.json](../../../integration/thin-harness-efficiency-20261021/i/a/RESULT.json)、[输入 handles](../../../integration/thin-harness-efficiency-20261021/i/a/handles.json)、[摘要](../../../integration/thin-harness-efficiency-20261021/i/a/batch-summary.json)、`i/a/batch-originals/` 完整原件。真实解释器为 `.venv/Scripts/python.exe` 3.13.12，api 从 checkout `src/etalon/runtime/api.py` 导入，路径/hash 和示例 hash 记录在 RESULT。独立 CLI 约 .517480 wall_second；摘要 7344 UTF-8 bytes、完整原件共 88016 bytes。原件特意带人工长说明，因此**这组字节数只是可压缩和可展开能力检查，不是代表性真实工作负载性能、宿主 token 收益、G1/G2 对照或 S/φ/G 改善**。

费用 1.25/预留 2 均是合成声明值，不冒称实测科学成本；未运行真实 worker、科学评分、CRO、R4b 计算或独立收益评价。未升级共享依赖、提交或推送。C 已收到准确接口和可复制到新隔离位置的句柄夹具；产品 reference/原生使用由 C，V 独立判定。

## I2 全量方法接续：数据与独立操作（2026-10-21）

A `/root/a_data_operations` 完成已分配产品内容，生产算法未改。
新增 [data-design](../../.claude/skills/etalon-data-design/SKILL.md) 及三个按需 references：
九个 `etalon_data_*` 的实际 SDK/参数/副作用，design
`read_bundle/import_bundle/verify_import/design_index`，六个原 MolQuarry 方法与
ETALON 适配。候选、测定、Astra 假设、坐标和准备身份分开；没有声称完整 Astra 标注学习闭环。

已有 [screening-sweep](../../.claude/skills/etalon-screening-sweep/SKILL.md)、
[gate-calibration](../../.claude/skills/etalon-gate-calibration/SKILL.md)、
[generation-planning](../../.claude/skills/etalon-generation-planning/SKILL.md)、
[install](../../.claude/skills/etalon-install/SKILL.md) 增加具体局部 references：
显式组件配置/plan/status/export，sweep 七类单独操作，separation/enrichment 的真实
行输入，产率/可选 funnel/tuning/资源平衡，以及 infrastructure/doctor/devices 和宿主导出。
现有 monitoring 已有实际批读方法，本轮复用不复制。既有 finite/paired/factorial 未重做。
所有能力可独立、部分或组合使用，未增加 router、method gate 或自动研究循环。

[源码示例](../../examples/data_design_methods.py) 可由宿主直接运行或修改，
不要求读取 skill 或登记方法。新目录运行实际零网络 catalog，3 行产生 2 个候选，
重复导入新增 0；普通 catalog 缺 dossier 测定依据时真实 `ValueError` 并停止 assay 导入。
可选 design 使用已有工程 producer fixture，严格读/仅索引/重放/verify：4 槽、2 pose、
0 池候选/观测，原失败槽保留。另局部调用校准/产率方法，得到不支持继续的结果后停止；
properties-only 配置冻结及真实 plan 后停止，没有启动 worker。它是 source example，
不宣称 wheel 自动提供 examples 命令；安装用户可以直接用 references 的 SDK 调用。

实际命令（`.venv/Scripts/python.exe`，3.13.12，工作目录 ETALON）：

```powershell
& .venv/Scripts/python.exe examples/data_design_methods.py --output ../integration/thin-harness-skills-20261021/a/sdk-run-02 --bundle tests/fixtures/prism_design_bundle_v1/bundle.json
& .venv/Scripts/python.exe -m pytest tests/test_data_design_methods.py tests/test_data_integration.py tests/test_data_boundaries.py tests/test_design_import.py tests/test_doctor.py -q --junitxml=../integration/thin-harness-skills-20261021/a/selected-tests.xml
```

[实际示例原件](../../../integration/thin-harness-skills-20261021/a/sdk-run-02/result.json)
SHA256 `2b7c7bc1221448ada565c03a041ffa079b6adc0503a973e3b32591ea3f1e3602`；
函数体 wall 5.6880245 秒，启动开销未测、真实金额未知；0 HTTP 来自实际 usage，
科学预算未花不代表全部 CPU 成本为零。首版 sdk-run-01/日志完整保留，二者不构成效率比较。

[选测](../../../integration/thin-harness-skills-20261021/a/selected-tests.xml) 为
**72 passed、1 failed、1 skipped，35.74 秒**，含全部 4 项新示例测试通过。
唯一失败为旧 `test_seal_refuses_extra_files_and_symlinked_directories` 在创建测试符号
链接时触发 Windows `WinError 1314`，未到产品 symlink 拒绝断言；没有改权限或删测试。
跳过为旧 AF3 bundle fixture 缺可选 Gemmi。两项不算通过。已有 mock HTTP 测试只证明
显式工程边界，不冒充实时外部采集。新/改及复用的六个 A 范围 skill 全部通过
[quick_validate](../../../integration/thin-harness-skills-20261021/a/quick-validate.log)，
格式有效不等于宿主实际采用。

[现场检查](../../../integration/thin-harness-skills-20261021/a/inspection.json)
SHA256 `f54ba3b90557520a7d9ca66b16a097c92be45793305cb42c953fbcf356bc67cb` 记录实际
模块路径/hash、依赖和设备快照；16 个保护文件（MolQuarry 10 源文件及 6 个生产模块）
对 I 本轮 baseline 全部字节未变。诊断显示 planning/active/cascade/quarry/mcp 可用，
可选 PRISM build tools 未探测；此结论不覆盖模拟、框架 GPU 可执行性或资源所有权。

完整能力族映射、旧验证入口和工程命令失败记录在
[A COVERAGE](../../../integration/thin-harness-skills-20261021/a/COVERAGE.md)，
由 I 汇入既有 README。共享注册、安装、两种宿主导出和原生验证由 I/V 绑定最终来源；
A 的 SDK 调用不冒充新的原生宿主或独立方法收益。R4b/原科学证据保留暂停；无封存标签
读取、科学评分、旧恢复、外部采集、共享升级、commit/push。
