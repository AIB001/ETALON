# 2026-09-18 验证记录

本轮对当前工作树进行了环境核查、源码审查、实际 CPU 组件运行和增量修复。
开始前的未提交修改被保留；修改前 tracked diff 另存于
`runs/review-2026-09-18/preexisting-tracked.patch`。没有提交 Git 或修改 vendored asset。

## 环境

默认环境为 `/home/shizq/anaconda3/bin/python`。Pydantic 1.10.8、PyArrow 11.0.0
不满足 MolCascade 依赖，没有 MCP。默认环境测试结果：1297 passed、43 failed、
252 errors、10 skipped。它不能作为对代码全部功能的有效验收环境。

隔离验证使用 `/tmp/etalon-audit-20260918-venv`，以 `venv --system-site-packages`
建立，在该环境覆盖安装可选依赖，没有更换全局依赖。这是本轮验证环境，不能把它的临时路径
当作长期部署配置；重新安装见 [运行指南](runtime-guide.md)。

| 依赖 | 版本 |
| --- | --- |
| Python | 3.11.5 |
| NumPy / SciPy | 1.26.4 / 1.11.1 |
| scikit-learn / RDKit | 1.3.0 / 2025.3.2 |
| Pydantic / PyArrow | 2.13.5 / 25.0.1 |
| MCP / HTTPX | 1.30.0 / 0.28.1 |
| pytest / Ruff | 7.4.0 / 0.16.7 |
| ONNX Runtime / skl2onnx | 1.30.0 / 1.20.0 |

原始版本记录在 `runs/review-2026-09-18/versions.json`。
这些版本可用于解释本轮结果，不表示项目声明的全部版本组合均已验证。

## 回归与接口

- 修改前隔离环境基线：**1602 passed，0 failed / 0 skipped**，244.60 秒。
- LLM、后台筛选、真实 stdio、诊断、GP 分块及相关旧接口专项：**130 passed**，32.60 秒。
- 功能升级后全套：**1656 passed，0 failed / 0 skipped**，252.80 秒。
- 补充 NumPy 1.25 分层诊断回归后的全套：**1657 passed**，253.77 秒。
- production 时长控制和既有 MD/适配器相关专项：**180 passed**，1.88 秒。该专项使用
  模拟子进程核对参数传递、非有限值拒绝和协议改变后拒绝复用，不是真实 MD 结果。
- 含上述 MD 参数升级的最终全套：**1665 passed，0 failed / 0 errors / 0 skipped**，
  252.72 秒；原件为 `pytest-final-v3.log` / `pytest-final-v3.xml`。
- Ruff 检查范围为 `src tests tools examples`，通过。
- `tools/verify_assets.py --deep`：MolCascade、PRISM、reference 及 README commit 引用核验通过。

完整测试命令：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -q -p no:cacheprovider \
  --junitxml=runs/review-2026-09-18/pytest-final-v3.xml
python -m ruff check src tests tools examples
python tools/verify_assets.py --deep
```

唯一已见 warning 是 RDKit 的重复 converter 注册提示。原测试套件含已有 GPU parity 测试；
不能把全部回归称为纯 CPU 测试，以下新示例和接口计算才明确使用 CPU。

新增后台任务验证覆盖：真实提交进程退出后计算继续、同 ID 并发提交只有一个 worker、
重复查询已完成 job 不重新执行、修改输入使旧 plan 失效、启动失败有持久记录且不隐式重试、
错误路径/ID 拒绝、只读状态查询不创建缺失工作区。

真实 MCP SDK 验证覆盖 stdio 初始化、工具发现、workflow 资源读取、plan/submit/status，
最后取得真实 MolCascade 成功结果。这验证的是接口通信和计算路径，不是 LLM 自主规划表现。

## 真实 CPU 过滤与学习

```bash
python examples/bulk_screen.py --workspace runs/review-2026-09-18/bulk
python -m etalon screen status --workspace runs/review-2026-09-18/bulk --run-id cpu-example
python examples/component_learning.py --workspace runs/review-2026-09-18/components
```

批量示例真实运行 library → standardize → window → properties，4 个阶段均 `SUCCEEDED`。
5 条输入中，阿司匹林与咖啡因保留；对应实际 RDKit 分子量为 180.159、194.194 Da。
乙醇低于本例 MW 下限，长链烷烃超出示例物性范围，无效 SMILES 未进入结果。
工件通过 `Screen.read(..., contract_id="property/v1")` 的校验后读取。

组件学习示例包含 8 个候选，先注册 MW endpoint，再添加经 SA/描述符组件执行的 logP endpoint。
本次完成 8 轮、16 条观察，16 条 admitted；`spent=24`、`remaining=0`、`pending=[]`，
停止原因 `budget_exhausted`。费用单位是 `demonstration_quotes`，不是实测 GPU 小时。
物性测试证明执行和学习闭环，不证明提高药物筛选命中率。

## GP 分块的内存验证

用相同 seed、5000 个数值候选、16 维特征、800 条训练观察，在两个独立进程分别以
batch_size=5000 和 256 计算预测均值、标准差和局部方差缩减。

| 项目 | 整池查询 | 256 分块 |
| --- | --- | --- |
| 进程峰值 RSS | 185884 KiB（181.5 MiB） | 101504 KiB（99.1 MiB） |
| 查询耗时 | 0.336 s | 0.320 s |
| 相对整池输出的最大绝对差：均值 / SD / 缩减 | 基准 | 0 / 0 / 0 |

本次峰值 RSS 降低约 **45.4%**。这是单次合成数值内存探针，包含进程导入/训练的峰值，
不构成稳定加速比、不同 BLAS 下逐位相同、百万库可扩展性或科学效果证明。
训练仍是精确 GP，默认候选/观察上限不变。

可重跑脚本和结果位于 `runs/review-2026-09-18/gp-memory-probe.py`、
`gp-unchunked.json`、`gp-chunked.json`、`gp-comparison.json`。

## LLM 与安装包

OpenAI、DeepSeek、Anthropic 均用 HTTPX MockTransport 验证了实际 `Advisory` 调用链：
各家 envelope、token 参数、认证/限流/服务错误、有限重试、失败信息不携带错误响应正文，
以及截断、空输出、工具调用、重复 JSON key、非有限数值和非字符串 reason 的拒绝。

没有使用三家的真实 API：本机对应密钥均未配置。因此真实账户可用性、模型行为、费用、
时延和任务自动完成率未验证。Claude CLI 在 PATH 上，但本轮没有验证其登录或请求模型。

`pip wheel --no-deps` 构建成功；将 wheel 解包到独立临时目录后，用 `python -S`
从包内解析 workflow 资源成功，且字节与仓库单一来源相同。
wheel 仍不包含两个 asset，因此不是独立可运行的完整科学基础设施发行版。

## 证据位置与未完成的科学验证

原件保存在 `runs/review-2026-09-18/`，该目录被项目已有 `.gitignore` 忽略，未随源码提交。
关键文件：

- `baseline-default.log`、`baseline-isolated.log`、`targeted-v2.log`、`md-controls.log`、`pytest-final-v3.xml`；
- `doctor-default.json`、`doctor-isolated.json`、`doctor-prism.json`；
- `bulk-status.json`、`bulk-properties.json`、`components.json` 及各自 workspace/工件/账本；
- `gp-comparison.json`、`wheel-check.json`、`assets.log`、`ruff.log`；
- `evidence.json`：测试汇总、原件摘要及 Python 源码摘要，不等同于完整容器/环境锁。

本轮没有给定靶标/受体/对接网格和真实筛选库，因此没有实际执行 prospective docking、
MD、MM/PBSA、PMF 或 FEP；没有得到生物活性标签，也没有证明优于任何外部 agent。
下一步科学验证与工程优先级见 [评估报告](review-2026-09-18.md)。
