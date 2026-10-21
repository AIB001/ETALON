# 专项约定、元方法与证据复用契约

日期：2026-10-21。状态：设计与实施约定；不表示下述增量已经实现或收益已成立。沿用 ACTION/RESULT/METHOD_CONTRACT，不建立新的调度框架。验收见 [EVOLUTION_ACCEPTANCE](EVOLUTION_ACCEPTANCE.md)，文件负责人见 [EVOLUTION_WORKSTREAMS](EVOLUTION_WORKSTREAMS.md)。

## 1. 不变边界与现有基础

宿主选择研究问题、下一动作、方法及其片段，可组合、修改、跳过、停止或直接使用 SDK；检索、方法登记、评价和 hook 均不是执行前置 gate。证据身份、科学准入、实际执行预算和不可变记录继续由已有内核校验。保留历史数值算法、兼容入口与原生收据。不自动恢复暂停科学批次，不改写旧结果、未提交内容或原始实验角色。

| 能力 | 直接复用 | 增量 |
| --- | --- | --- |
| 宿主指引 | src/etalon/HOST.md、HOST_SETUP.md、host.export/verify | 精简共用内容，增加可选专项引用与项目资料入口；不覆盖已有 AGENTS/CLAUDE |
| S/U/G 版本 | MethodVersion/Use/Evaluation、完整 bundle snapshot、显式外部 bundle | 修订产物和生成关系；无新顶层 method schema |
| 原件与结果 | Artifact、Experiment、Observation、data snapshot、runtime 分页、费用引用 | 可迁移定位、结论内容约定与只读检索 |
| 事件 | read_events、host.consume_events、稳定 request ID | 可选有限触发；不增加常驻反思循环 |

S=科学 skill，U=选择/组合/跳过的使用方法，G=产生和检验修订的改进方法。U 用来避免与既有科学策略 φ 混淆；不重命名历史 φ 记录。记录新方法或知识不自动训练模型权重，也不自动更新科学 M/φ。

## 2. 专项指引

HOST.md 保留宿主自主权、核心事实语义和入口；任务方法放入按需 references。专项指引仅说明职责、资源范围、相关证据/能力入口和权威暂停状态引用；不复制完整历史，不规定统一研究顺序。研究、方法改进、工程维护是可选用途，不是固定 agent 类。

可复用专项文本由 C 放在 src/etalon/host-guides/，具体文件由 I 在所有权表登记。I 负责导出器把这些文本按需带入包/宿主导出；默认不将全文拼入 AGENTS.md。项目本机 AGENTS、工程 life-healthy-contract-refactor skill 与项目路径不进入产品科学 skill 库。宿主原生加载只对实际观察过的宿主作结论。

项目提供一份可选 etalon-workspace.json，指引仅给出其位置。清单是数据源定位，不是任务计划、授权系统或强制注册中心。已知 SDK、文件或引用可直接使用。

## 3. 项目资料清单与引用

新增 schema 标识 etalon-evidence-workspace/1；由 A 实现校验，I 负责公共契约。最小内容：

```json
{
  "schema": "etalon-evidence-workspace/1",
  "workspace_id": "project-scoped-stable-id",
  "sources": [{
    "source_id": "released-research-01",
    "kind": "journal",
    "location": "evidence/research.sqlite",
    "identity": {"kind": "snapshot_sha256", "value": "<verified digest>"}
  }],
  "location_map": [],
  "status_refs": []
}
```

示例只示意字段，尖括号不是有效 digest。sources.kind 初始仅 journal、data_snapshot、artifact_manifest。artifact_manifest 必须显式列成员、哈希/大小及可见来源；不能变成任意目录扫描。身份锚使用实际存在的日志身份，或不可变快照摘要；活账本另报告事件水位，不把会变化的整个 DB 哈希当永久身份。清单相对位置相对于清单目录解析，不依赖进程 cwd。位置改变不改变 source_id，绑定到其他来源须明确冲突/新来源，不得静默替换。

对外 EvidenceRef 形状：schema=etalon-evidence-ref/1、source_id、record_id、record_digest；成员读取另含 member/selector 与原件 sha256/bytes。record_digest 对原始规范化记录计算；不得改写旧 record_id。内容 digest、带来源的记录 ID、当前位置是三个不同概念。相同 bytes 的两个来源不自动合并其测定语义、角色或成本。

location_map 的条目绑定 source_id、原 artifact/manifest 引用、预期 sha256/bytes 与新位置。旧绝对路径/记录原文保留。新位置必须经过路径边界和实际内容验证；错误映射不能被当作恢复成功。旧 API 不传 resolver 时保持兼容。POSIX 历史路径在 Windows 上的解析、Artifact 验证、journal._artifacts、skill verify_snapshot 均须贯通；不能只替换展示字符串。

## 4. SDK 与 MCP 共用的最小读接口

目标 SDK 模块 etalon.research.evidence；以下为本轮冻结签名，实施调整须 I 更新生产端/消费端/兼容样例。

```python
query_evidence(workspace, *, query="", filters=None, limit=10, cursor=None)
read_evidence(workspace, ref, *, view="record", member=None,
              selector=None, offset=0, limit=100)
```

workspace 是清单路径或已经校验的清单对象，不是自动创建数据库的目录。filters 初始支持 source_ids、record_types、task_ids、entity_ids、method_refs、statuses、roles；未支持字段明确拒绝，不静默忽略。limit 有界（查询 1..50）；默认摘要不超过 32 KiB，超限分页或明确 truncated。读取复用现有有界分页语义（最多 64 KiB 返回），原件内容核验可流式进行，不能以只读取片段冒充整个文件已核验。

query 返回 items、next_cursor、coverage、warnings。每项含 ref、summary、record_type、source、适用条件/关联引用、verification 与 availability。coverage 按来源报告已读水位、索引水位及 complete/partial/unavailable；没有匹配和来源不可读分开。游标绑定查询、筛选和来源水位，变化时明确 stale，不混页。优先结构化筛选和文本匹配；可重建索引不是权威记录，不引入必需向量服务。

read 的 view=record/summary/lineage/artifact，可直接读取已知 ref，无需 query。返回原记录/片段、来源链、核验状态、缺失信息；lineage 有界且可续读。获取记录元数据不冒充验证原件。所有查询/读取以只读方式打开已存在来源，未知路径不得隐式初始化 CampaignStore。不得启动模型、科学计算、外部下载或改写事实。

新增两个 MCP 薄工具 etalon_evidence_query、etalon_evidence_read，字段语义与 SDK 相同；共享注册只由 I 修改。现有 etalon_method_read、workflow_artifact、data status、零参 discovery 与旧 URI 保持兼容。新增工具不成为 router，不限制工具/skill 选择空间。

## 5. 存储、解释与跨源复用

原件保存在原执行/数据存储，SQLite 记录身份与关系；大文件不复制进索引。检索资料保留请求、返回、来源、时间及具体定位；计算保留输入/协议/执行/输出；实验保留样本、端点、条件、单位及原始数据。既有解析和科学准入可复用；不支持的端点先存原件及解析产物，不宣称已成为训练标签。

新增内容约定 etalon-research-claim/1，以 JSON Artifact 存储，不扩展万能 Observation。字段：question、subjects/conditions、statement、epistemic_status、support_refs、counter_refs、limitations、method_refs、supersedes。epistemic_status 区分 extracted_report/hypothesis/interpretation；它不是可信度分数，也不是科学准入。所有提取/解释指向具体原件位置，未知不补造。新解释追加并关联旧解释；事实、费用、原评价角色不回写。

跨源引用始终保留 source_id。当前 journal 的引用校验仍尊重本 campaign 范围；需要写入关系时，用显式来源封装 Artifact 登记原 ref/哈希/角色、此次可见性及复用类型，再建立本次记录。需要本地 MethodVersion 时登记所用完整快照并保留外部来源，内容摘要不变；新本地记录 ID 不等于新算法。引用历史费用不再收费，重新分析的实际新增成本另记。旧 independent_validation 经读取后是本次已见依据，不能复制后改称新的独立测试。

普通清单只列允许暴露的资料。角色过滤是接口约束，不是隔离证明；sealed holdout 原件、摘要、索引片段均不得进入普通查询视图。独立任务需由 V 保持评价材料在执行宿主不可读范围；若只能靠约定，报告隔离局限，不能宣称盲测。

文件与 DB 不是同一事务：先发布可校验不可变文件，再登记，缺失/孤立状态可识别；幂等重放不得重复事件/计费，不能自动删除孤立文件。索引由已提交记录重建，可失效，不回写原件。

## 6. 元 skill 的修订产物与自身改进

内容约定 etalon-method-revision/1 同样保存为 Artifact。字段：problem_refs、target_kind(S/U/G)、base_method_ref、producer_method_ref、producer_use_ref、candidate_version_refs、change_summary、mechanism_hypothesis、scope、counterexamples、trial_refs、accounting_refs。候选可为空，表示有理由的不修改；成本/原件仍保留。各引用有明确来源，不能编造执行遥测。MethodVersion.parents 只记录被修改对象的沿革，G 的生成关系在此单列，避免循环/混淆。

现有 MethodUse 支持多选、空选、片段与 usage_method_ref，直接复用。现阶段 journal 只接受 host_reported 使用归因；原生读取/调用/输出痕迹另存 Artifact，区分 available/read/applied，不能通过开放 tool_observed 标志伪造实际采用。既有 MethodEvaluation 的诊断与冻结独立评价继续使用。

G0 可依据开发轨迹修改自身形成 G1；保留 G0/G1 全部 bytes、父关系与实际生成痕迹。运行中的任务绑定启动版本；G1 用于后续调用，不热改当前 G0。G 的收益通过其在新问题上生成的候选方法及候选后续使用表现评价；G1 的自评分不构成证明。生成成本、失败候选、不修改、合理跳过均进入评价。被测 G 不可在看见结果后改评价标准。

skill-improvement 保持一个可选入口，补 selection-composition、self-revision 等按需 references/必要脚本；research 增加 evidence-reuse reference。不因“元”增加全局管理层。G 可修改 S/U/G 或提出专项使用指引候选，不能借此静默改科学内核不变量、用户授权或共享默认。

已有有限事件消费可触发候选或 skip/stop；与请求绑定，所有后代尝试共享已授权任务预算。MethodVersion/Use 写入本身不递归触发改进。科学结果提交不依赖 hook 成功；候选可直接局部试用，无晋升 gate。

## 7. 完成边界

接口可运行、原件可复用、宿主自由行为、S/U/G 独立收益分别验收。负结果可完成一次诚实验收，但不能把收益状态改成 passed。两宿主分别报告，导出成功不替代 Claude 原生运行。CRO/PRISM 新实验和 R4b 恢复不属于本工程批次；本轮真实任务使用已释放工程/研究证据。

## 8. 已实现接点及兼容说明（I4）

`ResearchJournal(store, *, resolver=None)` 与 `skills.verify_snapshot(content_ref, *, resolver=None)` 接受可选 `EvidenceResolver(workspace, source_id)`。不传 resolver 保留原路径读取。`journal.artifact_path(artifact)` 返回核验后的当前位置，原 Artifact.path、content_ref 和记录摘要不改写。历史 POSIX/Windows 绝对路径均可作为 Artifact 原始定位；非本机路径的实际读取仍需要显式映射。

`EvidenceResolver.resolve(original, *, sha256, bytes)` 映射条目为 source_id/original/sha256/bytes/location；新位置限制在清单目录内，全文身份核验后消费。`resolve_snapshot(content_ref)` 用映射的文件字节身份定位 manifest，再由 verify_snapshot 校验语义 manifest_sha256 和完整 bundle。两种 hash 不混用。`resolve_source(original)` 只接受绑定 journal 的原位置或明确映射，并核验来源身份；不允许跨 campaign scope 替换。immutable snapshot 被显式追加后其旧 pin 失效；不能把活 DB 摘要当永久身份。

`EvidenceError(ValueError)` 表示输入/身份拒绝，`StaleCursorError(EvidenceError)` 表示分页已失效。读取 artifact 的 selector 当前为 `bytes`，offset/limit 为字节，返回 base64 fragment、next_offset、total_bytes；lineage 使用关系偏移，每页最多 50。记录较小时返回原 JSON，超界时返回规范 JSON 的有界字节片段。MCP 只调用相同 SDK，并遵循现有工具错误封装和 workspace 绝对路径要求。

`method_revision.journal_ref(journal, record_id, *, source_id)` 使用调用方显式清单来源绑定，不从路径、运行时 inode/log_id 或可变数据库哈希推断永久 source_id。`build_method_revision` 纯构造内容，`record_method_revision(..., source_id=..., source_kind=...)` 可选写入普通 Artifact；没有新方法 schema 或执行前置条件。

host.export 在原先新目录导出语义下携带独立 `host-guides/*.md` 和 `HOST_SETUP.md`，既有 AGENTS/CLAUDE 不覆盖；不自动将专项全文拼入默认提示。工程 skill 不进入产品库。实际安装、导出与原生收益分别留收据，本文不替代验收结论。

## 9. R 审查修订：只读内容投影与精确关系

方法筛选包含已有 `MethodUse.selected_methods` 和 `usage_method_ref`，不改变多选、空选或跳过语义。lineage 的精确 EvidenceRef 保留原 digest、member、selector、sha256、bytes；目标不符在 missing/relationships 中明确标为 digest_mismatch、artifact_identity_mismatch 或 unavailable，不生成替代成功引用。历史字符串引用单独标为 legacy_string/resolved_current_metadata，不能冒充精确版本绑定。关系验证只覆盖记录及声明成员元数据，不自动证明目标文件内容正确。

普通 Artifact 可用既有 `bindings.schema` 显式声明 `etalon-method-revision/1` 或 `etalon-research-claim/1`。在明确发布的 workspace 来源中，读取器核验原件全文哈希/大小后为这两种内容生成临时检索和 lineage 视图。历史外壳缺少声明时可在 source 中显式列 `content_projections: [{record_id, schema}]`，最多64条，不接受未知 schema、重复条目或目录扫描。每来源最多64份投影、单份1 MiB、合计8 MiB；不建新权威账本，不回写 Artifact 或费用。

查询仍返回原 Artifact 的 ref、record_type 与原记录摘要身份，并报告 projection 状态及带原件身份的 artifact_ref。view=record 返回原外壳，view=artifact 返回原件；投影补充主题检索与声明关系。原件缺失、损坏、未公开或超预算时保留明确 unavailable/partial/warnings，不把失败解释为空内容成功。普通清单发布和 schema 声明都是可选数据能力，不是执行 gate。`examples/evidence_reuse.py` 的可选普通 claim 写入与具名 journal 快照发布示例不自动隔离封存文件；只可显式发布允许暴露的来源。

这些修复使用新源码/安装版本验证。已经冻结并启动的旧版本原生评价不热换包、不补跑、不追认为修复版收益证据；G0/G1 内容与旧失败保持不变。
