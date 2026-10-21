# 动作契约：etalon-action/1

状态：后端、MCP 与 CLI 已实现；实际验收范围见 DELIVERY_A/I/V，不由本文定义推断通过。原则与源码基线见 [README](README.md)，用例见 [ACCEPTANCE](ACCEPTANCE.md)。

## 1. 职责

动作请求表达“当前决定执行哪个已支持操作”。不要求完整研究 DAG、数值策略评分、必选 skill 或后继节点。它不是宿主全部行为的统一拦截层。

新增 `ActionRequest` 是公共封装；既有 [active/schema.py](../../src/etalon/active/schema.py) 中的 `Action` 继续表示分子—端点执行。准备、导入、筛选等操作不伪造分子端点或模型特征来套用 `Action`。

## 2. 请求字段

| 字段 | 类型／必需性 | 语义 |
| --- | --- | --- |
| `schema` | string，必需 | 固定为 `etalon-action/1` |
| `request_id` | string，必需 | 宿主生成，沿用 runtime 的 identifier 校验；在目标 workspace 内唯一 |
| `operation` | string，必需 | 已安装操作名称；首要新增操作为 `active.evaluate` |
| `arguments` | object，必需 | 按操作自己的参数契约校验，禁止未知参数悄悄改变行为 |
| `limits` | object，必需 | 显式单位到非负数的映射；无资源要求可为 `{}`，付费操作不得借此绕过预留 |
| `max_seconds` | positive number，可选 | 省略沿用既有 runtime 默认；限制可执行到什么程度须在 prepare 返回说明 |
| `method_use_refs` | reference[]，可选，默认 `[]` | 方法归因；允许不选 skill，不作为执行能力的白名单 |
| `evidence_refs` | reference[]，可选，默认 `[]` | 可见决策依据，不要求完整私有思考过程 |

`arguments` 中的 `database`、`workspace`、文件路径沿用已有绝对路径约束。可变 SQLite 文件不按整文件哈希绑定；绑定它所指的相关配置、协议、候选和执行条件。跨机器的路径只是 locator，科学身份依赖内容／协议引用。

示例仅说明字段。`<...>` 是占位符，不是合法哈希或真实运行记录：

```json
{
  "schema": "etalon-action/1",
  "request_id": "assess-C1-001",
  "operation": "active.evaluate",
  "arguments": {
    "database": "<existing-database-absolute-path>",
    "candidate_id": "C1",
    "endpoint_id": "endpoint-vina-r0"
  },
  "limits": {"campaign:wall_second": 60},
  "max_seconds": 120,
  "method_use_refs": [],
  "evidence_refs": []
}
```

`active.evaluate` 的首个参数契约为必需 `database`、`candidate_id`、`endpoint_id`。端点必须已经注册且可执行；重复次数由现有账本分配和限制，不让客户端任意填写内部 `replicate`。新尝试的关系记录可以随正式适配补充，不能用更换 request ID 绕过重复和 pending 检查。

## 3. 最小公共接口与生产消费关系

以下 SDK 位于 `etalon.runtime.api`，由 A 实现后端，I 提供 MCP 薄封装；没有新增 SDK 包。

| 接口 | 返回／用途 | 生产端 → 消费端 |
| --- | --- | --- |
| `prepare_action(request)` | `PreparedAction`，只读检查、冻结执行绑定 | A → 宿主、C、I、V |
| `submit_action(request, *, workspace, plan_id)` | `ActionHandle`，提交或返回同一已存在执行 | A → 宿主、C、I、V |
| `read_action_result(handle)` | [ActionResult](RESULT_CONTRACT.md) | A，使用 B 的科学准入解释 → 宿主、C、V |

MCP 对应目标名称为 `etalon_action_prepare`、`etalon_action_submit`、`etalon_action_result`。状态、产物读取、cancel、reconcile 优先返回并复用现有 workflow／screen 操作句柄；不要再复制一套恢复状态机。

`PreparedAction` 至少返回：

- 规范化请求及 `plan_id`；绑定的 operation、语义参数、资源／时间范围。
- 相关不可变输入哈希、候选／端点／协议和执行器版本；存在性与支持范围检查。
- 可预留的资源报价及依据；哪些限额只能在派发时检查、哪些能在运行时强制执行。
- 结果验证规则的版本引用、恢复能力与具体不支持理由。

`ActionHandle` 至少包含 workspace 定位、`request_id`、`plan_id`、原 job ID 和结果／状态读取所需的 backend 信息。内部 action/execution 尚未生成时引用为空，不能预造完成记录。

prepare 和 submit 可在已有授权内连续调用，不要求用户逐步确认。普通只读工具、宿主本地分析和既有专用工具入口继续可用。

## 4. 身份、幂等与状态变化

1. 以 `(workspace, request_id)` 为幂等键。相同冻结执行语义返回既有 job；同键不同 operation、参数、输入或资源范围返回 conflict。
2. `plan_id` 绑定执行语义，不把全局 skill 目录、无关方法备注、全局事件末尾序号当成执行输入。方法引用和依据另行持久化关联。
3. 重放返回原有归因；补充归因通过新增 `MethodUse` 关联记录完成，不静默覆盖原请求，也不重派科学动作。
4. 在同一事务内复核余额、占用、重复次数和实际协议约束。相关输入／执行器变化返回 stale；仅增加方法记录不导致 stale。
5. 决策使用的可见证据快照可保存，但不能把“有新日志”一概等同为决策不合法。操作真正依赖的读集由其验证器声明。
6. 未知外部状态先 reconcile。新 request ID 不证明新独立重复；新尝试须关联原尝试并保留全部实际费用。
7. 当前存储若不能同时执行同一 campaign 的两个动作，可以返回 busy／明确冲突，不承诺未实现的并发。不得清除旧 pending、放松锁或偷偷排出另一套队列。
8. 重放已完成请求先定位原 job，而非用已经变化的余额重新计算计划后误判它不能重放。

## 5. 与现有代码的接缝

| 当前文件／接口 | 直接复用 | 必须新增或拆开 |
| --- | --- | --- |
| [runtime/operations.py](../../src/etalon/runtime/operations.py) | 参数、input_hashes、preflight、execute、verify、accounting、recover | 增加实际支持的 `active.evaluate` 适配；结果视图 |
| [runtime/api.py](../../src/etalon/runtime/api.py)、[service.py](../../src/etalon/runtime/service.py) | 单节点 durable job、计划绑定、重放与恢复 | prepare/submit/read facade；不向宿主强制暴露 DAG |
| [active/runner.py](../../src/etalon/active/runner.py) | 执行器、检查、收据和准入过程，`ExecutionPending` | 分离选择与执行；新入口不调用 `choose()`，不伪造 GP 模型 |
| [active/store.py](../../src/etalon/active/store.py) | reserve/start/resolve、成本去重、协议配额 | 复用事务约束；无关方法事件不使宿主动作过期 |
| [mcp/runtime.py](../../src/etalon/mcp/runtime.py) | executor prepare/register、status/artifact/cancel/reconcile | I 接新后端；旧 `active.run` 和 `workflow_advance` 保持含义 |
| [runtime/executors.py](../../src/etalon/runtime/executors.py) | 实际执行器支持边界及版本绑定 | 不把当前 bounded bridge 宣称为任意分子通用执行 |

旧数值路径可以继续记录 round/model；宿主路径使用现有存储时可保留真实执行分组，但不可要求虚构模型或训练记录。

`reserve()` 中的通用预算／协议硬约束，与数值方法附带的 `confirmation_reserve` 选择逻辑需要明确分开。旧路径保持原语义；已明确承诺的 campaign 资源预留不因更换 skill 解除。A 在交付中说明两类约束的映射。

## 6. 错误与验收

沿用已有错误分类，增加清楚的机器可识别原因即可；不强制新建异常框架：

- unsupported：操作、输入域或执行器未支持。
- invalid：字段、科学身份或协议不合法。
- stale／conflict：相关绑定变化或幂等键内容冲突。
- busy／pending：真实占用或结果待核对，禁止直接自动重跑。
- budget／not-authorized：资源不足或超出实际授权；授权拒绝不得误标为可重试 I/O。

消费方不能凭 `retryable` 标签自动复制付费执行。A 必交 ACT-01—ACT-09；V 负责跨入口核验。接口缺能力时明确返回不支持，不用假结果填补。
