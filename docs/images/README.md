# ETALON 架构图与生成记录

2026-09-17 后续请求已生成 [PNG 成品](etalon-architecture-2026-09-17.png)，尺寸 **1536 × 1024**。
按 imagegen 技能先生成，再定向修正右侧显式 trial / promote / retire 的连线，
已检查主要文字、八个功能模块与共享执行闭环，并将原始 PNG 无损复制进工程。
**实际模型未核实**：服务仅返回 `image_url` 与 `output_hint`，没有 model 字段；PNG 元数据为空。
因此本文件不把成品标称为 GPT Image 2.5。

![ETALON 当前架构](etalon-architecture-2026-09-17.png)

- 初始 [提示词](etalon-architecture-2026-09-17.prompt.txt)。提交时额外要求输出一张完整 PNG，并保留八模块、十二条连接。
- 定向编辑的 [提示词](etalon-architecture-2026-09-17.edit.txt)。
- [机器可读生成记录](etalon-architecture-2026-09-17.generation.json)，包含最终文件 SHA-256。

## 历史失败与指定型号限制

- 用户指定 GPT Image 2.5；正式模型信息以 [OpenAI 模型页](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst) 为准。
- 已按当前代码核对八个模块、内外反馈环、共享执行路径与显式授权边界。
- [完整提示词](etalon-architecture-2026-09-17.prompt.txt) 已保存。
- 前一轮 90 分钟检查中的三次生成请求均返回 `network error: error sending request`；当时没有图像可供检查或保存。
  此入口也没有可指定或核实 GPT Image 2.5 的 model 参数。
- 使用 imagegen 技能提供的 CLI 对显式 `gpt-image-2.5-sunburst` 请求做了成功 dry-run。
  本地未配置 `OPENAI_API_KEY`，因此没有调用真实 Images API，没有可声称的生成模型或成功响应。
- 后续本次内置调用已成功生成和编辑，但不能核实型号；[Mermaid 架构说明](../architecture-current.md) 仍是连接规格的文字依据。

## 在本机明确指定 Image 2.5 重新生成

下列是本机技能脚本路径，不是 ETALON 的运行依赖。换一台机器需要替换脚本位置。
真实调用需要在本地安全配置 API key，并准备脚本所需 SDK；**不要把 API key 发到聊天或提交进仓库**。

```bash
python /home/shizq/.codex/skills/.system/imagegen/scripts/image_gen.py generate \
  --model gpt-image-2.5-sunburst \
  --prompt-file docs/images/etalon-architecture-2026-09-17.prompt.txt \
  --size 1536x1024 --quality high --output-format png \
  --out docs/images/etalon-architecture-2026-09-17-image25.png \
  --no-augment --dry-run
```

本轮仅执行了上面的 dry-run。凭据和账户访问就绪后，删除 `--dry-run` 才会实际提交生成请求。
采用新的输出文件名以保留当前成品。生成成功仍须检查文字、箭头方向和授权边界，再更新模型来源记录。
不得仅凭请求中的 model 字段把一次失败调用写成已由指定型号生成。
