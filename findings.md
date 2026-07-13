# 发现与决策

## 需求
- macOS 可双击启动的 Poppy 桌面应用；Python 内核继续保留 `pico` 包名。
- 用户配置 DeepSeek 云 API Key，不使用 Ollama。
- 支持会话创建、重命名、恢复。
- 仅访问用户明确授权的目录。
- 实时展示模型回复、工具参数、状态、输出和文件变化。
- 风险操作支持本次允许、范围化始终允许和拒绝。
- 支持停止模型生成和取消工具执行。
- 支持 Markdown、代码块、附件、托盘、全局快捷键和可管理个人记忆。

## 研究发现
- 当前 Pico 已有同步 `Pico.ask()`、模型 Provider、工具审批、工作区路径边界、会话、Run 工件和分层记忆。
- 当前 Provider 接口统一为返回完整文本的 `complete()`；桌面实时输出需要新增增量接口。
- 当前会话默认位于工作区 `.pico/`，桌面版需要统一应用数据目录。
- 当前远程 `origin` 是 `GeorgeGyx0807/GG-helper`，`main` 已推送。
- 当前机器是 Apple Silicon macOS 26.5.1，具备 Python 3.13、uv、Node 24 和 npm 11。
- 当前机器已安装 Rust/Cargo 1.97，并具备可用的 Xcode Command Line Tools/clang；Tauri 原生构建条件已满足。
- `Pico.ask()` 只是 `AgentLoop.run()` 的同步薄封装，适合在 `AgentLoop` 上增加事件钩子并由 Application Service 管理线程与取消。
- `Pico.approve()` 当前直接调用终端 `input()`；桌面化必须改成可注入审批回调，同时保留 CLI 默认实现。
- Anthropic-compatible Provider 当前显式发送 `stream: false` 并一次性读取完整 JSON，是 DeepSeek 实时输出的直接阻塞点。
- 当前 `TaskState` 没有 cancelled 状态/停止原因，需要补充并贯穿工件和恢复逻辑。
- FastAPI 官方文档确认 WebSocket 路由可直接收发 JSON，并可通过 query/dependency 验证 token；断开连接会抛出 `WebSocketDisconnect`，适合桌面事件订阅清理。
- FastAPI 当前测试客户端基于 HTTPX，可同时覆盖 HTTP 和 WebSocket Gateway 行为。
- 当前 Starlette 1.3 已将测试客户端迁移到 HTTPX2，普通 `httpx` 仍兼容但会产生弃用警告；开发依赖改用 `httpx2>=2.5,<3`。
- Tauri 2 官方文档确认：仅开发 macOS 桌面应用时 Xcode Command Line Tools 足够；本机已安装在 `/Library/Developer/CommandLineTools`，clang 17 可用。
- Tauri 2 必须使用 Rust；本机尚无 rustup/cargo/rustc，需要按官方 rustup 方式安装。
- Tauri 官方提供 React + TypeScript 模板，也支持在现有 Vite 前端上手动初始化；本项目采用 `desktop/` 子目录隔离前端与 Rust 壳。
- Rust stable 1.97 和 Cargo 1.97 已通过 rustup minimal 安装；Command Line Tools + Rust 的桌面构建前提已满足。
- Tauri 官方生成器 4.6.2 已创建并完成 `desktop/`：React 19、TypeScript 5.8、Vite 7、Tauri 2，产品名为 Poppy，bundle id 为 `com.george.poppy`。
- DeepSeek 官方 Anthropic-compatible 入口为 `https://api.deepseek.com/anthropic`；截至 2026-07-13，`deepseek-v4-pro` 可用，旧 `deepseek-chat`/`deepseek-reasoner` 将于 2026-07-24 下线。
- Tauri 官方支持将外部二进制作为 sidecar 打包，并提供系统托盘和全局快捷键插件；当前实现由 Tauri 启停 PyInstaller Gateway。
- `keyring` 4.x 的 macOS 后端使用系统 Keychain，并提供删除凭据接口；Poppy 设置页支持写入和移除 DeepSeek API Key。
- 开发者本地 ad-hoc 重新签名会改变应用代码身份；访问上一构建保存的 Keychain 条目时，macOS 会要求用户重新授权。这不是 API Key 错误，输入的是 Mac 登录密码。
- 审计发现桌面 Agent 缓存必须把当前授权和模型设置纳入签名，并且每次 Run 前重新验证目录授权，否则撤销/降级授权后缓存能力可能继续存在；当前已修复并覆盖测试。

## 技术决策
| 决策 | 理由 |
|------|------|
| 新增 Application Service 和稳定事件协议 | 避免桌面端依赖 `Pico` 内部对象，也能保留 CLI |
| Gateway 只监听 `127.0.0.1` 并要求随机令牌 | 降低本机其他网页或进程调用文件工具的风险 |
| 开发期先分离启动 Gateway 与 Tauri | 先验证闭环，后集中解决 Python sidecar 打包 |
| 生成目录 `output/` 不入库 | 它是可再生成工件，不属于源码 |
| Runtime 先采用事件回调并保持同步 `ask()` | 可以最小化对现有 CLI 和 132 个测试的破坏，Gateway 再在线程中桥接异步 WebSocket |
| Gateway 使用 FastAPI + WebSocket，事件仍由 `AssistantService` 缓存 | 官方支持成熟，HTTP 控制与实时事件职责清晰，断线后可按 sequence 补发 |
| UI 通过 `gateway_info` Tauri command 获取本机 URL/令牌 | 不把连接令牌持久化到 localStorage，也不硬编码在前端资源中 |
| 产品名统一为 Poppy，内部 Python 包继续叫 `pico` | 避免破坏已有 CLI/API，同时让所有用户可见入口保持同一品牌 |

## 遇到的问题
| 问题 | 解决方案 |
|------|---------|
| 暂无 | - |

## 资源
- `docs/architecture/desktop-personal-assistant-phase-1.md`
- `docs/architecture/agent-harness-v1-overview.md`
- `pico/runtime.py`
- `pico/providers/clients.py`
- `pico/tool_executor.py`
- FastAPI WebSockets 官方文档：https://fastapi.tiangolo.com/advanced/websockets/
- FastAPI 官方站点：https://fastapi.tiangolo.com/
- Starlette TestClient 官方文档：https://www.starlette.io/testclient/
- Tauri prerequisites：https://v2.tauri.app/start/prerequisites/
- Tauri create project：https://v2.tauri.app/start/create-project/
- DeepSeek API 文档：https://api-docs.deepseek.com/
- DeepSeek Anthropic API：https://api-docs.deepseek.com/guides/anthropic_api
- Tauri sidecar：https://v2.tauri.app/develop/sidecar/
- Tauri global shortcut：https://v2.tauri.app/plugin/global-shortcut/
- Tauri system tray：https://v2.tauri.app/learn/system-tray/

## 视觉/浏览器发现
- 用户截图确认第一版关注 10 项：聊天流式输出、会话、目录授权、工具卡片、审批、设置、个人记忆、托盘快捷键、取消和 Markdown/附件。

---
*每执行2次查看/浏览器/搜索操作后更新此文件*
