# Poppy 第一阶段验收记录

更新时间：2026-07-13

本记录按最初界面需求逐项核对源码、自动化测试、构建产物和实际 macOS 进程。真实 DeepSeek 端到端对话已由用户在 Poppy 界面中完成确认。

| # | 需求 | 当前证据 | 状态 |
|---|------|----------|------|
| 1 | 桌面聊天窗口、实时输出 | `message.delta` 事件、WebSocket 补发、React 增量渲染；用户真实发送“你好”并收到回复 | 已验证 |
| 2 | 会话新建、重命名、恢复 | SQLite 会话索引 + SessionStore；Gateway 恢复测试；多授权目录可选择工作区 | 已验证 |
| 3 | 只访问明确授权文件夹 | 系统目录选择器、Grant API、每次 Run 重新校验授权；撤销/降级授权测试通过 | 已验证 |
| 4 | 工具调用卡片 | UI 展示参数、请求/等待/运行/完成/失败状态、输出、影响路径和 diff 摘要 | 已验证 |
| 5 | 本次允许、始终允许、拒绝 | 审批事件和弹窗；永久规则限定精确工具和精确文件；Shell 不允许永久规则 | 已验证 |
| 6 | 模型设置 | 模型、Base URL、超时设置可持久化；Keychain 密钥；真实连接检测按钮；生产环境只接受 Tauri 返回的真实随机端口/令牌 | 已验证 |
| 7 | 统一个人记忆 | SQLite CRUD、界面编辑/删除、模型上下文实时注入、秘密形态拒绝测试 | 已验证 |
| 8 | 托盘和全局快捷键 | Tauri 托盘菜单、关闭隐藏、`Command+Shift+Space`；实际启动/退出生命周期检查 | 已验证 |
| 9 | 停止生成、取消工具、错误重试 | CancellationToken、关闭阻塞流响应、Shell 进程组终止、UI Retry；相关测试通过 | 已验证 |
| 10 | Markdown、代码块、附件 | ReactMarkdown + GFM、代码样式、系统附件选择、授权目录边界和历史恢复测试 | 已验证 |

## 安全与打包证据

- `Poppy.app` Bundle ID：`com.george.poppy`
- 内置 `poppy-gateway` sidecar，仅监听随机 `127.0.0.1` 端口
- HTTP/WebSocket 均要求每次启动生成的随机令牌
- HTTP CORS 仅允许 Tauri 本地应用来源，浏览器预检不会再被 FastAPI 以 405 拒绝
- DeepSeek API Key 已写入 macOS Keychain，服务名为 `com.george.poppy`
- 正常退出 Poppy 后，Gateway parent/child 均退出，无残留进程
- 最终构建位置：`desktop/src-tauri/target/release/bundle/macos/Poppy.app`

## 自动化检查

- `uv run pytest tests -q`：159 passed，6 warnings
- `uv run ruff check pico tests scripts`：通过
- `npm run build`：通过
- `cargo fmt --check && cargo check`：通过
- `npm run tauri build -- --bundles app`：成功生成 `Poppy.app`

## 最终端到端证据

- 用户在 Poppy 中成功连接 DeepSeek，并在一个已授权目录的会话中发送“你好”后收到回复。
- 持久化报告 `run_20260713-165620-4ac1b9` 状态为 `completed`，停止原因为 `final_answer_returned`。
- SQLite 中存在 1 个会话和 1 个明确目录授权，证明首次桌面闭环实际完成。
- 写文件审批的允许/拒绝/精确永久规则由 Gateway 集成测试覆盖；取消阻塞模型流与 Shell 工具由 Runtime/Service 集成测试覆盖。
- 根目录 `Poppy.app` 与已实测 bundle 的主程序及 Gateway SHA-256 完全一致。
