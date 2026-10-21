# E：G0/G1/G2 与官方 Laya 有界实测

2026-10-21。六次原生运行完成，两个已见工程fixture各三组；语义损失均0，保全审计通过。G0/G1/G2各两任务总耗时213.63/264.46/224.02秒，工具调用17/17/13。两次G2都自主跳过Laya，故没有模型增量收益证据；也未建立G1端到端速度收益。宿主累计输入1,130,586（缓存882,688、非缓存247,898）、输出12,337，真实金额未知。

真实历史盘点为13启动、12完成Codex会话、111唯一调用、6保守底层任务组；capabilities出现在10/12会话、6/6组。只证明选定工程验收日志的重复发生，不证明生产高频。正式六次不是六个独立科学样本。

Jev未测。官方Laya固定检查点、独立CPU2线程环境真实推理：六项语义评价5/6，开发2项1/2；其中将批内失败误判为全成功、证据缺失仍报告改进，选中类别概率分别0.8225、0.9834。两项超长拒绝和缺checkpoint回退符合预期。含smoke共12次尝试/9次forward，三次拒绝或故障不forward；两次语义错误没有被模型自动检测。没有训练、阈值拟合或事后补跑。

不建议默认启用快速模型；本轮不构成科学RSI、预测/实验收益或一般速度结论。R4b暂停保持，未读封存标签、未发PRISM新评分。没有改产品实现、公共契约或skill；仅本交付文档。

事前由I协调V通过冻结审查。FREEZE SHA256 `4dd17d485a08270e2157dfa8929e3d4d24c3d41451583f55b1e7378b1d9075e7`；G0源1186文件、I G1-fixed源1191文件，均包含旧未提交科学实现。运行绑定固定source导入，不边改边测。宿主requested model gpt-6.1-sol / medium；后端revision/推理往返数未知。模型revision `7b928d828b7b0e022f929d9bd2e44165aa270148`，SDK commit `ffc142012af63d015fa19f05f29f9c5ff329a2cc`。

本机完整证据根：`F:/life_healthy/integration/thin-harness-efficiency-20261021/e/`。其中REPORT.md给出完整表、冷/热与缓存、来源/预算、下载计量缺口、所有失败与边界；RESULTS.json为统计，FREEZE.json/V_GO.json为事前绑定，runs/为原件，semantic-review/与model-review.json为只读复核，FINAL_VERIFICATION.json及EVIDENCE_MANIFEST.json为最终检查。模型代码和独立环境留在e/试验材料，未作为产品依赖发布。
