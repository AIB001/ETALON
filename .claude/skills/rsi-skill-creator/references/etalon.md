# 在 ETALON 中创建第二层 RSI skill

先在当前 ETALON checkout 或安装包中定位下列资源。此文件提供查找入口，
函数签名及可用性以实际版本为准；不依赖某台机器的路径，不要求联网或远端计算。

## 复用边界

- `.claude/skills/etalon-skill-improvement/SKILL.md` 已有具体方法、使用方式与改进方法
  的修订机制。新第二层 skill 可以引用其相关部分，重点写本问题类的候选形成和选择依据，
  无需复制所有记录步骤。
- `.claude/skills/etalon-research-decision/SKILL.md` 可在缺少原因解释或下一步区分性实验时使用。
  已有充分决策依据时直接复用，不重复建立诊断阶段。
- `.claude/skills/etalon-research-index/SKILL.md` 支持跨类型资源、适用性审阅与任务工作集。
  SND1 的靶点/系列和 PoseBusters 的数据集/评价任务不是同类标签；按具体目标、输入、
  方法、评价端点和决策阶段判断复用，允许继承通用部分、停用局部经验或回模板。

主线程正在修改的文件只读；未经本次任务授权，不安装远端配置、不替换激活工作集。
候选目录或本机 skill 可独立交付，不能把文件已创建写成已部署或已采用。

## 版本、评价与经验

现有方法约定的 S/U/G 是对象类型：科学方法、使用/组合方法、改进方法。
它们不是本文第一/二/三层的编号。第二层 RSI skill 和第三层生成器都可能以
improvement_method 记录，但二者的版本与实际使用身份不同。

需要机器可追溯时，读取现有 `etalon-skill-improvement/references/self-revision.md`，
复用 `etalon.research.method_revision` 的 `build_method_revision` / `record_method_revision`。
子 skill 的父版本是被修订的原 RSI skill；生成器及其调用单独进入
`producer_method_ref` / `producer_use_ref`，不能混为父版本。
首次创建没有真实父版本时，沿实际 API 支持的首次登记方式处理，不虚构历史。

改进上下文、机会与经验沿 `etalon.research.improvement` 的既有接口处理。
评价沿已有 evaluation contract / `etalon_method_evaluate`；按其明确的引用、协议和
评价覆盖判定。数值规则通过仍需范围判断；生成后补写的规则不能冒充预先冻结。
这些接口用于需要落档或对照的任务，不构成日常探索或局部试用的前置门槛。

生成后的 RSI skill 放在现有 skill 库；条件性经验保留在研究图的原记录里。
skill 写程序性判断方法，并引用必要经验，不复制整条研究历史成为通用指令。
hook 可以交付适用的工作集与增量，不能把一份新 skill 的存在解释为自动采用。

## 研究闭环的接口连接

实现前核对当前 checkout；以下是既有职责映射，不表示所有连接已经自动贯通。

| 环节 | 复用入口 | 需要核对的实际连接 |
| --- | --- | --- |
| 当前决策需要什么 | research-context / research-index；decision_reading、meta_context | 局部规则、适用条件、反证及未完成动作能否一并读到，避免全历史展开 |
| 状态与规则表示 | scientific_input / scientific_handoff；research_model | 化学状态、坐标与解析版本分别绑定；模型 manifest 引用函数并不执行它 |
| 预测和行动 | research-decision；research_decision、revision_guidance | 预测先于对应结果；选定动作实际消费指定方法/工作流，不能只保存关联文本 |
| 编译与执行 | method-configuration；method_contract、组件既有执行接口 | 候选可为算法代码或工作流；绑定输入、依赖、运行版本和收据，按身份复用结果 |
| 比较与修订 | evaluation_contract、composition、improvement、method_revision | 对齐预期与实际观测；区分规则失配、执行错误与研究质量，结果影响下一次选择 |
| 增量与恢复 | research_impact、publication、checkpoint、research_hooks | 发布新观测及当前选择；hook 交付变更和精确读取入口，判断及科学执行仍由宿主负责 |

将实际缺口限制到上述连接，例如从已有执行结果抽取规则观测、把局部模型加入决策读取，
或复用现有执行器运行一个有明确输入输出的预测方法。无需新建元数据库或统一调度器。
`research_model` / `research_impact` 是表示与读取能力；不能把记录/哈希核验说成
预测器已经运行、反事实已经测得或后续选择已经改善。

规则更新后先定位受影响的判断、方法和计划；未变化部分继续复用。
共享解析器、特征定义、依赖代码或评价目标变化，也可能改变规则含义，不能只看 SKILL.md。
自动化缓存采用明确的输入与传递依赖身份，不把申明的依赖图视作完整因果模型。

## 示例：如何看出产物确实是第二层

| 第三层收到的问题 | 应生成的第二层能力 | 下游作用 |
| --- | --- | --- |
| 一类重复计算在相同设置下开销偏高 | 从实际轨迹区分重复工作与求解瓶颈，提出局部算法或复用候选并比较 | 第一层方法、执行参数或缓存使用得到改进 |
| 部分任务昂贵排序无收益，另一些会救回结果 | 在运行前定义可见诊断，冻结选择候选，结合正常执行及有界备选对照检查漏救回 | 分支选择或 workflow 得到改进 |
| 当前 RSI 总是增加检查而拖慢探索 | 生成以决策需求和完整成本选择验证强度的 RSI skill，保留早期试用路径 | 改进程序更符合发现效率目标 |

以上是设计例子，不是已验证效果或必须执行的实验。
对某个具体失败只增加一个检查，是一次修改；能指导后续同类问题如何选择改进的
RSI skill，才是本第三层要求的产物。
