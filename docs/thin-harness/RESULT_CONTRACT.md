# 结果契约：etalon-action-result/1

2026-10-21 增量：可迁移证据引用、只读查询和结论 Artifact 见 [EVOLUTION_CONTRACT](EVOLUTION_CONTRACT.md)。本文件执行/准入/费用语义不变；索引不是新事实账本。

状态：兼容结果视图与分页接口已实现；实际范围见 DELIVERY_A/I/V。请求见 [ACTION_CONTRACT](ACTION_CONTRACT.md)，方法归因见 [METHOD_CONTRACT](METHOD_CONTRACT.md)。

## 1. 结果是兼容视图

`ActionResult` 引用既有执行、收据、产物和科学准入记录，不另建事实数据库。RuntimeStore 负责可靠作业，CampaignStore 负责科学动作／成本，ResearchJournal 关联研究证据；三者职责不因统一返回形状而被合并。

| 字段 | 约束 |
| --- | --- |
| `schema` | `etalon-action-result/1` |
| `request_id`、`plan_id` | 与原提交绑定 |
| `execution_refs` | 现有 job/action/execution 标识及各自所属存储；不得混为一种 ID |
| `execution_state` | `pending`、`running`、`completed`、`failed`、`blocked`、`cancelled`、`unresolved` |
| `native_status` | 保留原生产端／内部状态，不能丢失原语义 |
| `outputs` | 产物或类型化结果引用，包含身份、哈希／版本、来源和父级关系 |
| `verification` | 状态 `pending/passed/failed/not_required`、验证器版本、证据与理由 |
| `observations` | 每项原观测引用、端点／单位及准入裁决；非标量结果不伪造 Evaluation |
| `accounting_refs` | 权威费用记录和单位／测量依据，汇总是派生视图 |
| `recovery` | 原执行定位、待核对事项、已有恢复接口；不自动重试 |
| `method_use_refs` | 实际关联的方法记录，可为空 |

运行时必须保持字段类型稳定：尚无执行、产物或观测使用空列表；未知可选标量使用 null，不能使用 0 或虚构成功。

`completed` 仅表示执行已明确完成，不等于产物验证通过、观测准入、研究任务完成或药物有效。`cancelled` 只有在实际停止得到确认时使用；仅发出取消请求而子进程结果未知时仍是 running/unresolved。

每项标量观测的 admission 使用 `pending/accepted/rejected` 和原 ruling 引用；`not_required` 只用于不需要科学观测准入的产物验证，不表示此产物可以作为标签训练。

## 2. 典型结果映射

| 场景 | 执行 | 验证／准入／费用 |
| --- | --- | --- |
| 外部调用超时，完成状态未知 | unresolved | 原执行与收据保留；已测费用和未结预留不丢失，不生成 failed 标签 |
| 原生工具完成，产物身份错误 | completed | verification=failed；相应观测 rejected；保留实际费用 |
| 计算运行明确失败 | failed | 原错误、部分产物和已发生成本保留；缺失读数为 null |
| 合法完成，零命中 | completed | 合法空结果通过验证；observations 可为空 |
| 准备 NOT_APPLICABLE | 按实际执行映射，保留 native_status | 保存适用性结果及成本；没有执行的评分为 null，不改成 0 |
| 合格 Vina 等计算分数 | completed | 只准入对应计算端点，不能改名成实验活性 |
| Astra 机制解释或编辑建议 | 按产生方式保留来源 | 假设／判断产物，不因格式完整自动成为测量标签 |

旧内部 action 的 `invalid/completed` 已含科学准入语义，不能不看收据就机械映射为执行是否完成。A 负责状态视图，B 负责观测及证据解释，保留原生事实。

## 3. 成本、学习与未知量

1. 使用现有账本条目的唯一引用去重。Runtime 的费用汇总引用 CampaignStore 已有费用时，标明同源，不再相加。
2. 区分 reservation、charged、measured 与 estimated／declared quote。当前 `operations.accounting()` 的某些值是声明报价，不能改名为测量。
3. 已知执行收费与未知开销并存时，报告已知部分和 coverage；未知量不补 0。不同单位不直接相加。
4. 资源上限是调度／执行约束，不是实际费用测量，也不是外部工具一定能中止的保证。超额事实必须保留并停止不允许的新派发。
5. 训练消费端按端点、准入、角色和模型需求选择数据。执行失败可用于可靠性建模，但不能直接成为低亲和力样本。
6. 未知反事实、不适用分支、共享轨迹分析和独立重复明确区分。共享产物必须保留相关性来源。
7. 独立评价一旦用于方法修改，后续是已见反馈；原评价角色不改写，后续使用关系另记。

## 4. 复用点与新增点

| 文件 | 复用 | 新增目标 |
| --- | --- | --- |
| [active/schema.py](../../src/etalon/active/schema.py) | Evaluation 的分子、端点、数值、单位、checks、provenance | 不扩成万能结果类型 |
| [active/store.py](../../src/etalon/active/store.py) | `record_execution_costs`、`resolve`、admitted/ruling、`action_resolved` | 已提交事件读取和兼容结果引用 |
| [runtime/operations.py](../../src/etalon/runtime/operations.py) | 原 verify/accounting/recover | 各操作映射到统一结果视图 |
| [runtime/service.py](../../src/etalon/runtime/service.py)、[store.py](../../src/etalon/runtime/store.py) | 持久化结果、恢复收据、节点事件 | 有界、可续读的事件读取 |
| [research/journal.py](../../src/etalon/research/journal.py) | 同账本不可变研究记录 | 三种方法记录及评价事件的类型标识 |
| [research/validation.py](../../src/etalon/research/validation.py)、[feedback.py](../../src/etalon/research/feedback.py)、[baseline_feedback.py](../../src/etalon/research/baseline_feedback.py) | 原冻结／释放／比较绑定 | 兼容引用到方法评价，不假定附加反馈已经更新模型 |

## 5. Hook 的事件接缝

事件只传递已提交事实。Hook 的方法由宿主选择，可修改、替换、跳过；不是内核自动研究循环。

| 事件来源 | 可触发什么 | 不可混淆的边界 |
| --- | --- | --- |
| CampaignStore `action_resolved` 提交后 | 结果解释、适用性诊断、下一动作候选 | 检查 payload 的 admitted/ruling；事件名不等于准入成功 |
| RuntimeStore `node_completed/node_failed` 等已提交事件 | 继续、核对、停止或通知 | 未完成／丢失响应不能包装为验证成功 |
| ResearchJournal `research_recorded` 中的指定评价记录 | skill、使用方法或改进方法的候选修订 | 按 record type 过滤，不把每条研究备注都触发一次改进 |

`runner.result_observer` 负责准入前的恢复收据，保持其可靠性语义；普通 hook 不能插入其位置，也不能在事务内调用 LLM。Hook 失败不回滚科学事实。

公共读取接口为 `etalon.runtime.api.read_events(log_ref, *, after_sequence=0, limit=100)`，返回 `events`、`log_id`、`next_cursor`、`has_more`。`after_sequence` 可为起始整数或完整 `{log_id, after_sequence}` 游标；使用完整游标可拒绝跨日志误用。A 实现既有日志的读适配，I 封装为 `etalon_events_read`，C 消费。

- `log_ref` 明确 CampaignStore 或 RuntimeStore 的来源及 scope；序号仅在该日志内有效。
- 从提交记录读取稳定顺序；空页不跳过未来事件。返回内容有界，不能只返回“最近100条”而悄悄漏掉历史。
- 事件引用包含日志身份、序号和类型；跨日志使用显式 action/job/record 关联，不要求建立全局总序。
- C 在已有宿主工作区或现有 metadata 保存消费游标与 `(event_ref, handler_method_version)` 处理身份。不引入新数据库、消息总线或守护服务。
- 至少一次读取可重放；消费端幂等。若 hook 提交动作，用稳定的派生 request ID 处理“已提交但未确认”窗口，不能声称分布式 exactly-once。
- 事件触发最多生成候选或在已有任务／资源范围内提交动作；不能据此扩展预算、恢复暂停批次或无限自触发。
- 原生宿主 hook 不可用时，显式读取事件后继续工作即可；不以两种宿主插件机制完全一致为契约前提。

## 6. 验收

A 负责 RES-01—RES-07、EVT-01 的后端事实；B 负责 RES-08 和评价角色；C 负责 EVT-02—EVT-04 的消费行为。V 统一核验状态、计费、事件重放及多入口一致性。用例定义见 [ACCEPTANCE](ACCEPTANCE.md)。
