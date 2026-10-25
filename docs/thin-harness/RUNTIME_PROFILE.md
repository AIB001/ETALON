# 固定运行环境档案

`etalon.boundary.runtime_profile` 是显式的 capture → verify → bind → launch 边界。
它不安装依赖、不选择默认环境，也不接管任务调度。现有 `runtime`、MCP 和任务恢复接口未被改成自动选择档案；需要使用本边界的调用方显式采用它。

一个能力的状态分别记录：

| 状态 | 证据 | 不能推断 |
| --- | --- | --- |
| `available` | 所需模块实际导入，所需文件和检查程序可读 | 必需检查通过、真实工具执行或科学有效性 |
| `verified` | 固定身份匹配，而且该能力声明的全部检查本次成功 | 对接/MD 完成、收敛、亲和力或实验效果 |
| `real_run: not_established` | 档案从不替代任务原始收据 | 本次环境检查就是科学重复 |

检查失败或缺失保持为失败。某个依赖即使只是包安装的 optional extra，只要能力的检查要求它，该检查就必须成功；不能将 ImportError 转为跳过并继续声称 `verified`。模板中的 import/构造检查只验证所声明的入口条件，真实输出仍须由现有任务验证器验收。

## 档案记录什么

- 原样保存绝对 Python 可执行路径，调用时不做 `resolve()`。文件身份另外记录解析后的目标和 symlink 目标；Linux venv 的 Python 仍通过 venv 路径启动。
- 在指定解释器内导入声明模块；记录实际加载路径、版本字符串、已加载模块文件 SHA256/大小，包括该导入实际触发的子模块和原生扩展。资产模式直接调用现有 `boundary.infra.load`，保留其 pinned 路径检查、来源声明，并对相应 `MANIFEST.json` 另算实际摘要。
- 记录 Python 文件、`sys.prefix`、`sys.base_prefix`、存在时的 `pyvenv.cfg`、所选环境参数，以及显式声明的 native/weight/source/data 文件。外部 site-packages 和所声明模块位于环境目录之外时，列出实际路径并标为 `overlay_observed`。
- `-B` 不禁止读取既有 bytecode，因此还对已导入模块存在的 `__cached__` 文件记录摘要，标为候选缓存，不虚构实际使用了缓存还是重编译了源码。
- `manifest_digest` 覆盖档案全文；`identity_digest` 覆盖规范化配置和文件身份。检查输出只存 SHA256、长度和退出状态，不转储整个环境变量或输出文本。

摘要覆盖**本次实际加载文件及显式声明文件**，不覆盖整个包树、尚未触发的 lazy import、未声明的动态库或所有系统状态。`self_contained_environment` 始终为 `not_established`。仅固定 `__init__.py` 不意味着整个依赖固定。执行新路径前应将关键实现模块和必要原生程序/权重纳入本能力档案；使用 `expected_root` 可以拒绝实际导入到其他源码目录。资产清单的 `tree_sha256` 仍是现有 `infra` 的声明，不冒充本次全树重新验证。

## 可复查的命令

从 `examples/runtime_profile.example.json` 复制能力所需的最小配置，将所有 `/absolute/` 路径换成已核验路径，删去不需的能力、模块及检查。它是模板，不是已经验证的部署配置。`module.loader=asset` 可显式指定已有冻结资产目录 `asset_dir`，避免移动环境或复制依赖。所有引用于某个能力的模块、文件和检查都是该能力的必需项；其余声明项仅供盘点，但其身份同样属于整个档案，改动后需要新档案。

```bash
/absolute/controller-python /absolute/runtime_profile.py capture \
  --spec /absolute/task/runtime-spec.json --output /absolute/task/runtime-profile.json

/absolute/controller-python /absolute/runtime_profile.py verify \
  --profile /absolute/task/runtime-profile.json --capability data-query \
  --output /absolute/task/runtime-verification.json

/absolute/controller-python /absolute/runtime_profile.py bind \
  --profile /absolute/task/runtime-profile.json --capability data-query --task-id fixed-task \
  --command-file /absolute/task/input-config.json --output /absolute/task/runtime-binding.json \
  -- /absolute/fixed-entry.py --config /absolute/task/input-config.json

/absolute/controller-python /absolute/runtime_profile.py launch \
  --binding /absolute/task/runtime-binding.json --receipt /absolute/task/runtime-launch.json \
  --timeout 120
```

也支持 `python -m etalon.boundary.runtime_profile ...`。独立文件方式只有标准库依赖，适合目标解释器中的 ETALON 尚未安装新模块的情况；实际 probe 使用目标 Python 运行同一份档案工具，工具本身也被摘要绑定。

`capture --no-checks` 只盘点可用性，不能直接建立已验证绑定。`bind` 会重新核验，保存完整 profile、能力、精确 Python 参数及脚本/配置/输入文件摘要；直接脚本入口自动纳入，其他文件由重复的 `--command-file` 显式声明。`-c CODE` 的代码和参数直接进入绑定摘要；`-m MODULE` 要求该模块已在 profile 中显式捕获。入口不能以 `-E`、`-I`、`-S` 等改变预检导入方式的选项开头。

`launch` 没有替换命令或选择其他环境的参数。它先重新核验绑定全文、环境/模块身份、必需检查和所有 command files，随后调用原指定 Python 加 `-P -B` 与原参数，不经过 shell。`-P` 阻止脚本目录/当前目录成为额外隐式导入来源，`-B` 避免向旧源码写 bytecode。需要的源码目录必须显式放入 `pythonpath`。原生检查用绝对可执行路径；检查 argv 中的 `{python}` 使用同一指定 Python 及 `-P -B`。

输出文件使用独占创建；已有 profile、binding 和 receipt 不覆盖。CLI 在启动前先占用新 receipt 路径，已有 receipt 会在执行命令之前拒绝。调用方可使用原生任务的 idempotency/recovery 接口，不能将新 receipt 名称当作许可重复科学任务。环境漂移不自动重绑，任务超时标为 `unresolved` 且没有自动重试。

## 环境与旧任务

`pythonpath` 和 path/thread/device 环境键由配置显式声明。环境键只允许源码所列白名单；用户 Python 路径和用户 site 不继承，`PATH` 未配置时固定为本平台 `os.defpath`。依赖加载需要的 `LD_LIBRARY_PATH`、`GMXLIB`、`PATH` 等应显式写入配置。应用凭证和普通非查找变量可以继承，但不写入档案；不要将秘密放入 argv、配置或 command files。

完整 profile 嵌入每个 binding。另一个文件、应用或 MCP 默认指向新环境，不会改变已绑定任务。旧任务缺失原环境时失败并保留绑定，不能换成“看起来兼容”的解释器。可以对同一源码创建多个能力档案；data-query 环境可用不代表包含 PoseBusters 的 cascade-inspect 环境可用。

本边界没有修改现有计划语义。若调用方把它接入 `runtime`，应将 **binding digest 与完整固定 binding** 纳入原计划/预检输入，并在真正派发前调用同一核验；目前自动接入尚未实现，不把模块测试或独立 launch 示例描述为全 MCP 默认切换完成。调用方仍负责原生任务身份、输入/协议/成本与输出验证。核验与进程创建之间不是原子文件快照；本地可变源码需要只读快照和单一写入责任配合，不能把本机制当作对恶意并发文件替换的隔离设施。
