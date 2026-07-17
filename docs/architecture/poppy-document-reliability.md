# Poppy 文档可信检索、增量索引与全文问答

## 目标

让 Poppy 在个人资料库和内置阅读器中做到三件事：

1. 回答明确来自正确文档，不因相似关键词串文档。
2. 文件新增、修改、删除后自动更新索引，不阻塞聊天。
3. “全文模式”覆盖整篇解析文本，并能综合跨章节定义、实验表格和结论。

## 已实现方案

### 1. 文档范围与可信检索

文档选择优先级：

1. 内置阅读器或附件显式传入的文档路径。
2. 用户问题中出现的完整文件名或完整文件名主体。
3. 当前会话手动锁定的文档。
4. 未锁定时才允许在当前授权资料库中自动检索。

主聊天输入框上方会显示当前文档范围，可手动锁定、切换或解除锁定。锁定后，检索只查询该文档的块。

检索采用混合排序：

- SQLite FTS5/BM25 提供关键词召回。
- macOS NaturalLanguage 本地词向量提供语义召回。
- Reciprocal Rank Fusion 合并两路排名。
- 文件名精确命中提供额外优先级。

语义向量在本机生成并保存为归一化 int8 向量，不把文档发给额外的向量服务。

当锁定文档中没有可核验证据时，Poppy 直接返回“没有找到足够证据”，不会调用模型用常识补全答案。

### 2. 后台增量索引

Poppy 启动后为每个资料库目录启动递归监听：

- 新增或修改：仅重新解析对应文件。
- 删除：删除对应文档、文本块和语义向量。
- 移动：按“删除旧路径 + 索引新路径”处理。
- 应用关闭期间发生的变化：下次启动执行一次快速全目录核对，未变化文件按大小和修改时间跳过。

事件先去抖，再由独立后台线程处理。当前打包运行时使用 watchdog 的稳定轮询观察器，扫描间隔约 0.4 秒；未使用会在 Python 3.13 停止阶段崩溃的原生 FSEvents 扩展。

设置页显示：

- 当前状态和进度。
- 已处理文件数。
- 失败文件数。
- 失败文件及原因。

### 3. 分层全文问答

内置 PDF 阅读器增加“全文模式”；文献快问的“全文问”也会启用该模式。

- 文档不超过约 160,000 个解析字符：按顺序覆盖全部文本，最多拆成四批并行取证。
- 更长文档：先把全文划为 16 个连续区间；每个区间保留边界内容、问题相关内容和表格/数值密集内容，再并行生成证据摘要。
- 最终模型只根据各批证据综合，要求保留文件名、页码或工作表位置，并显式处理证据不足和冲突。

这不是把无限长原文一次性塞进模型，而是有覆盖说明的分层 Map/Reduce。运行时会显示通读进度，最终提示中记录本轮使用的覆盖方式、原始字符数和证据字符数。

## 已验证

- 相似关键词文档不会进入锁定文档的检索结果。
- 完整文件名会自动切换并锁定目标文档。
- 可手动切换和解除文档锁定。
- 无证据时不会请求模型。
- 新增、修改、删除文件可自动更新索引。
- 打包应用能够生成本地语义向量。
- 全文模式会执行“分批取证 → 最终综合”并发送进度事件。
- Python 测试、前端构建、Rust 检查和 macOS 打包签名均通过。

## 参考

- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [Apple Natural Language / NLEmbedding](https://developer.apple.com/documentation/naturallanguage/nlembedding)
- [watchdog observers](https://python-watchdog.readthedocs.io/en/stable/quickstart.html)
- [OpenSearch hybrid search](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/)
- [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- [Lost in the Middle](https://arxiv.org/abs/2307.03172)
