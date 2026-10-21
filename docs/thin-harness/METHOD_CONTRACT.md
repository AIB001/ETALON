# 方法契约：版本、使用与评价

2026-10-21 增量：S/U/G 修订生成关系、G 自身改进和跨来源复用见 [EVOLUTION_CONTRACT](EVOLUTION_CONTRACT.md)。沿用本文件三类记录，不另建方法执行框架；新验收见 [EVOLUTION_ACCEPTANCE](EVOLUTION_ACCEPTANCE.md)。

状态：三类记录与内容快照接缝已实现；工程与科学证据范围见 DELIVERY_B/C/V。方法不是执行前置工作流。领域 skill、使用方法 φ、改进方法 G 都可版本化和演化；它们可以采用相同记录机制，但必须在评价中区分。

## 1. MethodVersion

目标 schema：`etalon-method-version/1`。

| 字段 | 约束 |
| --- | --- |
| `name` | 人可读名称，不作为内容身份 |
| `roles` | 描述性标签，如 domain_skill、usage_method、improvement_method；可组合，不封闭科学方法空间 |
| `content_ref` | 内容快照定位、manifest 摘要、文件摘要；复用已有 bundle／Artifact 机制 |
| `parents` | 父版本引用，可为空；支持修改、合并和另起方法 |
| `dependencies` | 对象，名称映射实际依赖的内容／工具／协议版本引用；未知依赖显式为 null |
| `entrypoint` | 可选入口文件或调用说明；不会因声明入口就获得执行能力 |
| `change_summary`、`evidence_refs` | 修改理由和开发依据；允许探索性假设，不能伪造独立效果 |
| `verification_basis` | `host_reported` 或有实际内容校验的 `content_verified`；校验的是字节，不是宿主实际采用 |

记录 ID 使用既有 canonical/digest 风格按完整记录生成；内容版本摘要与带来源的记录 ID 分开，避免“同样内容、多条来源”被误认为算法改变。既有薄分支 bundle digest 原样保留，不为统一命名悄悄重算不同摘要。

版本必须包含相关 references/scripts 的真实内容。修改、删除这些文件都形成不同内容版本。记录本身追加不可变；当前默认版本是另一个配置选择，不能修改旧记录来切换默认。

宿主可以直接使用新建／修改的本地 skill。记录只收到摘要时保留 host_reported；有可解析且核对过的快照才称 content_verified。不能要求先装进服务器内置目录才能探索，也不能把无法恢复的内容宣称为可重放。

薄分支 [skills.py 固定基线](https://github.com/AIB001/ETALON/blob/d3889e6c0600605f018fb81ce3df1b420d6dcbf9/src/etalon/skills.py) 的六项 WORKFLOWS 是默认目录，不是上限。新增 bundle 引用必须保留有界读取、路径穿越和内容身份检查；不把动态 skill 读取变成任意服务器文件读取接口。

## 2. MethodUse

目标 schema：`etalon-method-use/1`。这是一次实际选择、应用、组合、停止或改用方法的记录，不是必须完成的计划。

| 字段 | 约束 |
| --- | --- |
| `subject_refs` | 任务、已知作用域中的 request、action、结果引用；至少一个。提交前可指向计划 request，不伪造已存在 action |
| `selected_methods` | 方法版本及可选使用范围，允许空列表和多个条目 |
| `usage_method_ref` | 可选，记录本次选用指引／示例／使用规则版本 |
| `visible_evidence_refs` | 可选，实际可见资料的引用；不要求保存完整私有思考过程 |
| `host_conditions` | 可观测的宿主、模型、工具条件；不可得字段为 null |
| `basis` | `host_reported/tool_observed`，分别表达声明与直接可观测行为 |
| `reason` | 可选简短依据，可说明跳过、停止、部分采用或组合 |
| `trigger_event_ref` | 可选，说明由哪个已提交事件触发 |
| `parents` | 可选，关联此前使用或补充说明；不改写旧使用记录 |

目录可见、服务端返回了内容、宿主声明采用、脚本实际执行是不同证据。任何单项都不能证明完整内部推理过程。晚到的实际使用信息追加记录，不补写成当时已冻结的决策。

组合关系可按实际发生的顺序记录；不要求预先列出整个 DAG、穷举全部备选 skill、建立 router 或统一步骤语言。修改 A 得到 A′ 后，后续 request 可绑定 A′；既有 request 仍绑定 A。

## 3. MethodEvaluation

目标 schema：`etalon-method-evaluation/1`。

| 字段 | 约束 |
| --- | --- |
| `subjects`、`comparators` | 被比较的 S、φ、G／固定方法及内容版本 |
| `protocol_ref` | 评价规则／预注册内容引用，包含指标、成本范围、分组、比较方法 |
| `task_split_ref` | 开发、校准、评价／迁移划分与暴露状态；复用既有 TaskSplit／Artifact |
| `initial_conditions_ref` | 初始 M、可见证据、宿主／模型／工具和资源起点 |
| `result_refs`、`accounting_refs` | 真实结果、失败、未知及完整费用引用 |
| `assessment_role` | diagnostic 或有实际边界支持的 independent_validation；不能由一句自我声明升级 |
| `conclusion`、`scope` | 改善／无改善／证据不足等有依据的结论和适用范围 |

独立性依据使用现有冻结、释放与 lineage 校验；不能只靠专项 AGENTS.md 或角色名称宣称信息隔离。评价协议若是看过结果后才制定，应如实标为探索性。

复用 [research/journal.py](../../src/etalon/research/journal.py) 的不可变记录、Artifact 和 TaskSplit，扩展受支持类型；不把 skill 修改塞进现有仅面向科学配置的 Change，也不重写旧 policy_version 或历史评价。

## 4. 公共调用面

B 在现有 ResearchJournal 上提供三类记录的校验／写入和读取。可直接扩展 `ResearchJournal.record(value)` 与 `get(id)`，无需建立方法服务。

I 提供目标 MCP 入口 `etalon_method_record(database, record)` 和 `etalon_method_read(database, record_id)`：前者只接受三种明确 schema，复用 B 的校验；不能成为任意 metadata 写入口。C 负责生成内容快照和使用记录；B 验证类型、引用与评价角色。

普通科学动作不要求先调用方法记录接口；没有方法记录会限制归因，不会自动否定已通过独立科学准入的观测。提交前显式传入的方法引用应可解析；错误引用可纠正，或在未要求方法归因的任务中显式省略，不静默伪造。

## 5. 自由修改与改进闭环

1. 宿主可创建／修改候选 skill、使用规则或改进 skill，在已有授权及资源范围内试用；不必先经过正式晋升。
2. 用版本和 MethodUse 绑定实际输入、动作及结果；科学知识／预测器 M 的更新另外记录，不自动归入 skill 改进。
3. 用固定起点、独立后续任务及费用范围比较；失败候选和无效修改也进入证据。
4. 新默认方法或适用范围的改变形成可追溯配置选择。历史运行绑定不变，可选回旧方法。
5. 新评价成为下一轮开发反馈；再次声称独立效果需要新的未暴露评价材料。

需要区分联合变化时可选的归因设计（不是试用、执行或改版的门槛）：

|  | 旧使用方法 φ | 新使用方法 φ′ |
| --- | --- | --- |
| 旧 skill S | 基线 | 选用能力的变化 |
| 新 skill S′ | skill 内容的变化 | 联合效果与相互作用 |

固定基础模型、工具、初始 M 和其更新规则，并保留直接工具／便宜静态方法对照。评价的是任务决策损失、有效产出、停止／跳过不适用方法及成本，不仅是“选中了标准答案 skill”。

G 的改进另做等起点、等修改／评价预算的后代比较；自我修改文本或产生更多候选都不足以证明元改进。有限任务效果不扩张成无界 RSI 或实验药效。

## 6. Hook 与专项指引

Hook 绑定仅需表达“什么已提交事件可能适用哪个方法版本”，放在宿主／skill 配置中。绑定不是内核强制路由；按 [结果契约](RESULT_CONTRACT.md) 的事件与幂等语义运行。

专项 AGENTS.md／CLAUDE.md 只提供简短职责、能力入口和科学边界。使用方法按需加载并独立版本化；默认不灌入全部 skill、设计文档和固定路线。产品 HOST 指引同源导出，本机 AGENTS 保持本机属性。

C 必交 MET-01—MET-07 与 HOST-01；B 必交记录、引用和评价角色验证；V 主导 LEARN-01—LEARN-03 的评价设计和证据判定，不能把尚未执行的独立比较写成通过。

## I2 全量库接缝补充

内置目录扩展为旧六项、六项 MolQuarry 原始 bundle，以及 data-design、research、skill-improvement 能力入口。零参数字段形状保持不变，条目可增；目录仍不是合法方法白名单。MolQuarry 的 `etalon://skills/molquarry[/...]` 旧 URI 与新的 `molquarry-*` slug 引用同一原内容，完整版本包括支持文件。安装后由现有资产目录解析，宿主导出带齐实际成员，不增加一套复制维护的数据工作流。

S 是方法内容；HOST 使用指引的摘要单列。两者可独立改版、部分试用和撤回。记录、读取技能、使用快速模型、进行 S×使用策略对照都不是动作前置条件。独立收益仅在具体问题和候选需要时按事前方案检验，新增或修改方法本身不构成收益。
