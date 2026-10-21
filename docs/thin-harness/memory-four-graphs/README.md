# ETALON 四图研究架构

研究沿已有证据、科学判断、实验、新结果形成可选的关系网络，四图共享研究历史 H。节点标明已有接口、待新增能力或需要扩展的部分。本目录是设计和图示，不是新增科学实现。

![四图连接](four-graphs/preview.png)

## 阅读入口

- [具体设计](DESIGN.md)：四图关系、memory 契约、检索与更新、新 chat 接续、S/U RSI 触发及代码对应。
- [交互架构图总入口](index.html)：下载本目录或 checkout 本分支后，用浏览器打开 index.html；GitHub 文件页本身不会运行 HTML。
- [逐步 memory 读写图](memory/preview.png)。
- [新 chat 与 RSI 关系图](continuation-rsi/preview.png)。
- 总入口第四个视图保留之前的 Skills / SDK 能力图。

E/J/X/R 是拟新增关系视图，不是四套数据库或必经流水线。每层可以只读已有证据，分支可以停止。RSI 限于方法 S 与使用/分支选择 U；本轮不纳入资源分配优化。

## 来源与校验

[SOURCE_MANIFEST.json](SOURCE_MANIFEST.json) 固定现有实现的源码字节身份；[VALIDATION.json](VALIDATION.json) 记录图与候选 JSON 的哈希、源码提交、自动校验及浏览器检查。图内源码链接固定到对应 Git 提交。

四幅图均通过 Archify 的 schema、交付、完整性与真实浏览器检查。三个新增图已查看 1440×900 浅色截图；入口四个标签的加载、切换与横向溢出检查通过。这些检查不替代科学试验或完整 RSI 效果验证。

每个子目录的 candidate.json 是可编辑图源。重新生成时使用 Archify 3.1 的 finalize architecture 命令，以仓库根目录作为工作目录、当前仓库作为 --repo-root，并采用候选内 meta.output 指定的相对输出路径。先确保 source_revision 对应的提交可用；输出后重新校验，不直接编辑已生成 HTML。
