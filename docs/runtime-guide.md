# ETALON 运行与 LLM 接入指南

本指南对应 2026-09-18 的增量升级。完整代码审查与外部比较见
[评估报告](review-2026-09-18.md)。以下命令从完整 checkout 根目录执行。

## 安装与诊断

使用 Python 3.11 或更新版本建立独立环境；不要用安装成功代替能力检查。

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[cascade,active,mcp,llm]'
.venv/bin/python -m etalon doctor --require cascade --require active --require mcp
```

`doctor` 返回 JSON，分别报告数值学习、实际筛选、MCP 和 PRISM 构建工具的就绪状态。
`--require` 可重复；任何要求的能力未就绪，退出码为 1。不指定 `--require` 时是诊断报告模式。
诊断会检查包版本和真实 import，而不只是模块名是否存在。它不安装依赖，不请求 LLM API。

PRISM 使用自己的环境，按实际安装路径检查：

```bash
.venv/bin/python -m etalon doctor --prism-python /absolute/path/to/prism-env/bin/python
```

这里的 `prism-build-tools` 仅表示发现 GROMACS 和所需构建程序，不能证明力场、
参数化、MM/PBSA、PMF、FEP 或某一具体受体系统可运行。具体 docking 后端及权重需由
对应 `screen plan` 检查。完整 asset 文件核验使用 `python tools/verify_assets.py --deep`。

已有 Python MD 适配器现在支持 `Simulate.build(..., production_ns=chosen_ns)` 和
`PrismStage(..., production_ns=chosen_ns)`。时长应由具体体系和采样计划决定；
它会写入构建身份，改变时长后复用旧运行会被拒绝。用于 active endpoint 时也应登记新协议身份。
省略参数保留 PRISM 的 500 ns 默认值。`drive(stages=("em", "nvt", "npt"))` 仍会执行
包含 production 的完整脚本，`stages` 只控制检查清单，不能用来要求提前停止。

## 真实批量筛选

先跑包含物性门控的 CPU 示例：

```bash
.venv/bin/python examples/bulk_screen.py --workspace runs/my-bulk
.venv/bin/python -m etalon screen status --workspace runs/my-bulk --run-id cpu-example
```

该例真实执行 MolCascade 的读取、标准化、物性过滤和描述符计算；默认 5 条输入中，
阿司匹林和咖啡因保留，乙醇和长链烷烃被示例物性范围排除，无效 SMILES 不进入结果。
这只是执行示例；这些范围不适合不加判断地用于全部药物项目，也不测量靶标亲和力。

对已有 schema-2 cascade 或 flat pipeline：

```bash
.venv/bin/python -m etalon screen plan \
  --config /absolute/path/to/cascade.json \
  --library /absolute/path/to/library.csv \
  --workspace /absolute/path/to/run

.venv/bin/python -m etalon screen submit \
  --config /absolute/path/to/cascade.json \
  --library /absolute/path/to/library.csv \
  --workspace /absolute/path/to/run \
  --run-id project-screen-001 --plan-id PLAN_ID_FROM_PLAN \
  --workers 2 --device cpu

.venv/bin/python -m etalon screen status \
  --workspace /absolute/path/to/run --run-id project-screen-001
```

flat pipeline 自己绑定输入，省略 `--library`。Docking 的受体、box、后端路径等必须在
具体配置中提供；示例不会猜测它们。GPU lane 显式写成 `--device cuda:0`。

`plan` 检查插件、配置和依赖，产生绑定输入文件内容的 `plan_id`。它可能创建工作目录，
但不执行筛选。`submit` 核对计划后启动后台进程，立即返回；MCP 或提交命令退出后计算继续。
同一 `run_id` 加相同输入、计划及 worker/device 设置是幂等查询，不会重复启动。
文件改变或同一 ID 改设置会被拒绝。`status` 返回任务状态、MolCascade 各阶段状态、
artifact ID 和日志路径；CLI 查询失败任务的退出码为 1。

成功结果可通过既有 `Screen(workspace).read(artifact_id, contract_id=...)` 校验后读取。
无论某阶段是否留下部分工件，整个任务未成功都不会显示为 `succeeded`。

任务账本在 `workspace/etalon-screen-jobs.sqlite`。当前支持本地 POSIX 主机；
它不是 SLURM 调度器，没有心跳、取消命令、自动重试或主机宕机后的自动恢复。
`queued/running` 表示最后保存的状态。进程异常消失后，应先核实进程和日志再处理，
不要通过反复换 ID 来掩盖未完成工作。底层 MolCascade 的恢复 API 与此新提交入口是不同边界。

**批量筛选没有自动接入 active campaign 的预算账本。** 计划中的 cost 字段明确为未估计，
任务费用为 `not_metered`。应在既定算力授权下提交；若需要逐次查询和报价预算保护，
使用现有 `ActiveCampaign`，不要把 bulk job 的完成当作已入账的主动学习观察。

## 通过 MCP 驱动

在支持本地 stdio MCP 的客户端配置中使用绝对 Python 路径：

```json
{
  "mcpServers": {
    "etalon": {
      "command": "/absolute/path/to/ETALON/.venv/bin/python",
      "args": ["-m", "etalon.mcp"]
    }
  }
}
```

不同客户端的配置文件位置可能不同。新增四个工具：

| 工具 | 用途 |
| --- | --- |
| `etalon_doctor` | 诊断环境；不能用全局就绪替代具体计划的依赖检查 |
| `etalon_screen_plan` | 检查真实筛选配置，返回 `plan_id` |
| `etalon_screen_submit` | 提交该计划，返回任务；标记为 `SPENDS` |
| `etalon_screen_status` | 查询任务和工件，不启动或重试 |

一次完整调用链为 doctor → plan → 在已有授权内 submit → status。
本轮已用真实 MCP SDK 的 stdio 会话验证初始化、工具发现、workflow 资源读取和 CPU 筛选。
裸 GPT/Claude/DeepSeek HTTP API 不会自行启动本机 MCP：仍需要客户端/agent host 执行工具循环。
云端仅支持远程 MCP 的产品也不能直接连接这一本地 stdio 进程。

## 在已有 campaign 中切换 LLM advisor

`HttpAdvisor` 是已有 `Advisory` 的传输实现；它提供结构化建议，不启动科学工具。
OpenAI/DeepSeek 使用 Chat Completions，Anthropic 使用 Messages，保留 `ClaudeCli`。
模型名称必须由操作者填写为其账户实际可用、且支持相应 API 的模型。

```python
from etalon.judgment.advisor import Advisory
from etalon.judgment.proposal import Act
from etalon.judgment.providers import transport_from_env

# 通过进程环境提供 ETALON_LLM_PROVIDER、ETALON_LLM_MODEL 和对应 API key。
transport = transport_from_env()
advisor = Advisory(transport, identifier=transport.name, attempts=2)
proposal = advisor.ask(
    Act.SPEND,
    'Select at most two ids from ["a", "b", "c"]. Return JSON with parent_ids.',
    required=["parent_ids"],
    context="Only propose; the campaign validates membership, budget and scientific eligibility.",
)
```

| `ETALON_LLM_PROVIDER` | 密钥变量 | 协议 |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `/v1/chat/completions` |
| `deepseek` | `DEEPSEEK_API_KEY` | `/chat/completions` |
| `anthropic` | `ANTHROPIC_API_KEY` | `/v1/messages` |
| `claude-cli` | 使用原 CLI 登录 | `claude -p` |

可选 `ETALON_LLM_BASE_URL` 指向实现相同 API 的网关；URL 不接受内嵌凭据或查询参数，
远端必须使用 HTTPS，本机 loopback 可使用 HTTP。

HTTP 请求有超时与 token 上限，暂时性错误按 `Advisory.attempts` 有限重试，并记录尝试次数；
认证/参数错误直接返回。截断、拒绝、工具调用、空输出、重复 JSON key、非有限数字和非字符串
reason 都不会进入建议。密钥只在发请求时读取，HTTP 错误正文不会写进建议。
这仍是结构校验：输出满足 JSON 格式不代表其科学判断正确。

本轮三家协议都经过模拟 HTTP 测试；环境没有三家的 API key，没有进行付费 API 实测，
不能保证每个模型/网关的兼容性、时延和实际任务完成率。原 `claude` 命令存在也不等于登录有效。

## 主动学习的适用规模

已有 `examples/component_learning.py` 演示真实组件的选择 → 执行 → 准入 → 重训。
默认最多 5000 个候选、1500 条 admitted 观察，KG 的候选上限为 512。
本轮 GP 版本更新为 `paired-task-gp/4`，预测与局部方差缩减默认按 256 个候选分块，
没有删减训练标签或候选，也没有改变观测噪声/核函数。精确 GP 的训练复杂度、
全局协方差计算和逐分子执行仍受限；批量初筛和小池精筛应分别使用合适的入口。

已有 replay 会绑定源码身份。升级后旧 replay 若拒绝混用新源码，这是预期行为；
保留原账本，在新目录运行新的对照实验，不要删除身份检查。

## API 依据

请求字段按 [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、
[Anthropic Messages](https://platform.claude.com/docs/en/build-with-claude/working-with-messages) 和
[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 核对。
OpenAI 使用 `max_completion_tokens`，DeepSeek JSON mode 使用 `max_tokens`；
Anthropic 处理 content blocks，不假定其响应与 OpenAI 相同。
