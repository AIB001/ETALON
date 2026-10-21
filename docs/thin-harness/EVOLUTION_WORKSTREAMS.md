# 演化与证据复用：并行文件归属和任务书

日期：2026-10-21。本轮 I4/A4/B4/C4/V4 与历史同名字母区分。依据 [EVOLUTION_CONTRACT](EVOLUTION_CONTRACT.md) 与 [EVOLUTION_ACCEPTANCE](EVOLUTION_ACCEPTANCE.md)；这是工程协作分工，不进入产品运行时。R 继续审查与保留理念，不承担业务实现。

## 1. 唯一文件负责人

所有路径相对于 ETALON。I4 管理共享接口和新增路径登记；每个文件只有一名写入者。各线可以立即做无依赖工作，不等整个实现完成。未列路径默认不可写，向 I 提出具体需要并更新归属；不同文件可以并行，不跨线整目录覆盖。

| 唯一负责人 | 允许写入的产品文件 | 接口与交付责任 |
| --- | --- | --- |
| A4 证据 | 新 src/etalon/research/evidence.py；新 src/etalon/research/evidence_refs.py；tests/test_evidence_query.py、test_evidence_refs.py | 清单/ref/claim 校验、query/read、只读跨源查询、位置解析；向 I 提供 journal/skill 接点所需的纯函数签名；向 B/C 提供 SDK 样例 |
| B4 元方法 | .claude/skills/etalon-skill-improvement/**；新 src/etalon/research/method_revision.py；examples/skill_improvement.py；tests/test_method_revision.py、test_skill_improvement.py | 修订 Artifact helper、S/U/G references、自身修改实际开发痕迹；不改现有 schema/journal；向 V 交 G0/G1 和完整来源 |
| C4 专项指引 | src/etalon/HOST.md、HOST_SETUP.md；新 src/etalon/host-guides/**；.claude/skills/etalon-research/SKILL.md 与新 references/evidence-reuse.md；examples/evidence_reuse.py；tests/test_evidence_guidance.py | 按需专项与新 chat 入口、真实 SDK 例；不覆盖 validation-feedback.md/released_feedback.py；向 I 交待包装资源 |
| I4 集成/共享 | src/etalon/research/schema.py、journal.py、__init__.py；src/etalon/skills.py、host.py；src/etalon/mcp/server.py、host.py 及新 evidence.py；pyproject.toml；docs/thin-harness/**；tests/test_evolution_integration.py | 唯一公共契约、MCP注册、打包/导出、旧路径兼容接点负责人；不重写科学算法；集成检查、READY、最终交付 |
| V4 独立验收 | tests/test_evolution_acceptance.py（如确有必要）；本轮 integration/v/** | 事前真实任务与评分、外部宿主调用、独立证据检查；不修改产品实现或替实现线修代码 |
| R 审查 | 本轮 integration/r/**；启动前本三份契约和入口增补 | 启动后不与 I 同时写公共文档；查差异与原生证据，明确科学/工程/收益边界 |

A 的 evidence_refs 为无外部派发的身份/定位辅助；A 的 evidence 组合它与既有读入口。B 的 method_revision 消费已有 Journal 与 A 的引用格式，保持现有 method schemas。I 仅在共享接点适配上述函数。不要为 ownership 再加包层级、通用 registry 或管理数据库。

C4 新增专项文件已由 I4 登记：`src/etalon/host-guides/research.md`、`method-improvement.md`、`engineering.md`、`project-entry.md`。I4 负责将其以独立按需文件打包、导出；不拼入默认宿主指引。

B4 的新增 bundle references 登记为 `.claude/skills/etalon-skill-improvement/references/selection-composition.md` 与 `references/self-revision.md`，仍由 B4 单写。

受保护：其余 src/etalon 科学算法、asset/**、PRISM 全仓、历史 integration 证据和 R4b。确需修改其他文件时由 I 登记理由、兼容方式和验证，再交唯一负责人；不得清除已有 dirty/untracked/staged。不能用只含 HEAD 的 worktree 当本地科学基线。

## 2. 共享接口负责人

| 共享面 | 语义/最终签名唯一负责人 | 实现生产端 | 消费端 |
| --- | --- | --- | --- |
| EvidenceRef/workspace/query/read/错误/分页 | I4 | A4 | B4/C4、MCP、V4 |
| 旧 Artifact/快照/MethodUse scope 定位兼容 | I4 | I4 接 A4 resolver | Journal、skills、外部已安装调用 |
| revision/claim JSON 内容及引用 | I4 | B4 revision、A4 claim/query | 元 skill、研究 skill、V4 |
| HOST 专项导出与项目入口 | I4 | C4 文本、I4 导出 | 两宿主、新 chat |
| G 与后代真实评价 | V4（独立评分），I4（执行版本） | B4 候选、I4 安装 | V4 原生运行与 R 审查 |

I 通过本轮 integration 的短接口说明给各线反馈签名，不让公共文档变成逐小时广播。接口调整必须包含生产端、消费端、旧调用兼容和必要检查。B/C 可以先写标明 planned 的例与反例，拿到 A/I 实际签名后校对。

## 3. 启动、保全、合并

R 已先保存含未跟踪文件的真实基线与 index/staged/refs，再落契约。I 在各线开始修改前发布基线和写入权回执；各线先读取本任务契约/本机指引并写 ACK，随后仅改所属文件。共享 checkout 可按唯一文件归属并行；若选择隔离快照，必须包含该真实基线并核对哈希，不能仅 checkout HEAD。不要新建工作树来假装已有本地改动在其中。

各线不得启动重复 A/B/C/V。独立 chat 列表由本轮 COORDINATION.json 给出；I 负责接续与信息传递。真实验收需在 checkout 外以固定源/非 editable wheel 执行，不修改共享环境。中断、失败、旧候选均保留；不自动提交/推送。工程重构 skill 仅用于开发保全，不进入产品 catalog/wheel/导出。

## 4. 各线可直接执行的任务

**A4**：实现清单、EvidenceRef、query/read 与可迁移 resolver；先用已释放资料验证只读、分页、损坏/缺失反例，再处理真实旧 POSIX 路径在 Windows 的兼容。只改 A 文件，共享接点给 I 具体补丁建议。交付 SDK 签名、调用例、EV-REF/READ/CLAIM/CROSS/VISIBILITY 证据及限制。

**B4**：沿用完整快照/MethodVersion/Use/Evaluation，做小型修订产物 helper 和可选择的 S/U/G 方法内容。用真实已释放开发问题实际使用 G0 产生 G1，保存原生生成与修改痕迹及成本；不得读取 V 未释放任务。不要只写“允许 RSI”。交付 G0/G1、生成关联、局部使用/no-change/撤回/hook 检查。先与 V 对齐开发资料边界，不等待 V 完成全部评价。

**C4**：精简 HOST 共用约定，提供按需专项 references、research evidence-reuse 和 SDK 例。清单不是 router；新 chat 可直接 SDK 或跳过 skill。用实际安装内容校对入口可见性、引用完整性与调用样例，打包改动交 I。可在 A 签名暂缺时先处理独立文本/用例，不把所有契约塞进默认提示。

**V4**：立即独立制定验收及保全检查；选择真实待解决任务和已释放证据，不为新接口编造答案。记录 G1 开发暴露范围，准备未用于开发的后代生成/后续使用任务。源码未 READY 前不派发正式评价。负责本批至多十二次原生任务与原件，负结果不补跑；独立报告工程、自由行为、效率、G 自身改进的证据边界。

**I4**：发布共同基线和共享接口、接收各线 ACK 后推进自身共享接点；唯一负责合同/MCP/打包/兼容。不要重做 A/B/C 功能或占用 V 裁判。检查各线实际工具与改动，并整合必要测试、非 editable 安装和双导出；与 V 冻结版本/任务后开展有界真实验收。最后把 final.diff、安装包/来源映射、保全、原生结果、限制与一次性收据留给 R，不要求用户转交。

## 5. 完成与交付

每线交付自己的 REPORT.md/RESULTS.json/改动清单/测试或原生证据引用。I 汇总最终源码来源、产品差异、安装与导出、共享文件归属、索引与历史证据保全；V 独立结论不由 I 改写。R 评审前不称整个 RSI 已完成。旧失败、unknown 与未运行宿主保留。本轮可以在负收益结果下完成工程交付，不能把收益未成立转成不断增加固定 workflow 的理由。
