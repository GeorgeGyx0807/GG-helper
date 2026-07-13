# 进度日志

## 会话：2026-07-13

### 阶段 1：基线审计与可复用 Runtime 边界
- **状态：** complete
- **开始时间：** 2026-07-13
- 执行的操作：
  - 完成桌面助手实施设计和第一阶段验收标准。
  - 将当前 Pico 仓库完整历史推送到 GitHub。
  - 启动实现阶段并建立持久化任务计划。
  - 审计本机工具链与 Runtime/Provider/审批/TaskState 的桌面化阻塞点。
  - 新增稳定 `RunEvent` 信封、`CancellationToken` 和 cancelled 任务状态。
  - 为 Runtime 增加有序事件、可注入审批和外部 run_id。
  - 新增 `AssistantService`，支持后台 Run、事件补发、审批响应、同会话互斥和取消。
- 创建/修改的文件：
  - `pico/application/events.py`
  - `pico/application/cancellation.py`
  - `pico/application/service.py`
  - `pico/runtime.py`
  - `pico/agent_loop.py`
  - `pico/tool_executor.py`
  - `pico/task_state.py`
  - `tests/test_application_runtime.py`
  - `tests/test_assistant_service.py`
  - `docs/architecture/desktop-personal-assistant-phase-1.md`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### 阶段 2：流式 DeepSeek Runtime 与任务控制
- **状态：** complete
- 执行的操作：
  - 为 Anthropic-compatible Provider 增加 SSE 增量解析和 usage 统计。
  - 将模型增量转换为 `message.delta` 事件。
  - 过滤模型 `<final>/<tool>` 协议标记，只向桌面显示最终回答正文。
  - Shell 改为独立进程组执行，支持协作取消和超时终止。
  - 模型异常持久化为 failed，取消后允许同一会话重新启动 Run。
- 创建/修改的文件：
  - `pico/providers/clients.py`
  - `pico/application/streaming.py`
  - `pico/tools.py`
  - `pico/tool_context.py`

### 阶段 3：Local Gateway 与桌面数据层
- **状态：** complete
- 执行的操作：
  - 查阅 FastAPI 官方 WebSocket、断连和测试客户端方案。
  - 实现 SQLite 会话索引、设置、目录授权和个人记忆 CRUD。
  - 实现 DeepSeek 桌面 Agent 工厂与只读/写入/Shell 工具白名单。
  - 实现带随机令牌的 HTTP/WebSocket Gateway、事件补发、审批和取消 API。
  - 真实启动 Uvicorn，仅监听 `127.0.0.1:8876`，鉴权健康检查返回 200。
- 创建/修改的文件：
  - `pico/api/`
  - `pico/storage/`
  - `pico/application/controller.py`
  - `pico/application/factory.py`
  - `tests/test_desktop_gateway.py`

### 阶段 4：Tauri 桌面界面
- **状态：** complete
- 执行的操作：
  - 核对 Tauri 2 官方 macOS、Rust 和项目创建要求。
  - 安装并验证 Rust/Cargo 1.97，创建 React/TypeScript/Tauri 2 桌面工程。
  - 完成会话侧栏、流式聊天、Markdown/代码、附件、工具卡片、审批、停止、重试和设置界面。
  - 增加太阳按钮控制的浅色/深色主题，并持久化用户选择。
  - 完成系统托盘、关闭隐藏和 `Command+Shift+Space` 全局快捷键。
- 创建/修改的文件：
  - `desktop/`

### 阶段 5：权限、记忆与系统密钥
- **状态：** complete
- 执行的操作：
  - 使用系统文件夹选择器，授权时明确选择只读、读写或 Shell。
  - 将永久审批规则限制为精确工具和精确文件，Shell 不支持永久授权。
  - 使用 macOS Keychain 保存和删除 DeepSeek API Key，Gateway 重启时按需注入。
  - 完成个人记忆 CRUD、秘密形态拒绝、路径逃逸和附件边界测试。

### 阶段 6：打包与验收
- **状态：** in_progress
- 执行的操作：
  - 使用 PyInstaller 构建 `poppy-gateway` Tauri sidecar，并实现认证关闭接口。
  - 将产品名、Bundle ID、托盘和窗口统一为 Poppy。
  - 新增 Poppy 使用、构建和故障排查文档。
  - 完成第一轮 10 项需求逐条审计，修复目录授权撤销后缓存权限未立即失效的问题。
  - 新增多授权目录的新会话选择、模型失败重试和保存密钥后的 Gateway 重连重试。
  - 流式 Provider 在取消时主动关闭响应，避免没有新 token 时停止按钮长时间无响应。
  - 用户已成功将 DeepSeek API Key 写入 macOS Keychain，并完成新构建的钥匙串访问授权。
  - 修复 Keychain 弹窗阻塞启动时前端误回退到开发地址 `127.0.0.1:8765` 的竞态；生产应用现在持续等待 Tauri 返回真实随机端口和令牌。

## 测试结果
| 测试 | 输入 | 预期结果 | 实际结果 | 状态 |
|------|------|---------|---------|------|
| Python 全量测试 | `uv run pytest tests -q` | 全部通过 | 132 passed，6 warnings | 通过 |
| Ruff | `uv run ruff check pico tests scripts` | 无错误 | All checks passed | 通过 |
| 桌面 Runtime 定向测试 | `uv run pytest tests/test_application_runtime.py tests/test_assistant_service.py -q` | 事件/流式/审批/取消通过 | 8 passed | 通过 |
| 兼容性全量测试 | `uv run pytest tests -q` | CLI 与既有行为无回归 | 140 passed，6 warnings | 通过 |
| 阶段 2 全量回归 | `uv run pytest tests -q` | 流式/取消改造无回归 | 145 passed，6 warnings | 通过 |
| Gateway 定向测试 | `uv run pytest tests/test_desktop_gateway.py -q` | 鉴权/会话/WS/审批/取消通过 | 6 passed | 通过 |
| 阶段 3 全量回归 | `uv run pytest tests -q` | Gateway/SQLite 无回归 | 151 passed，6 warnings | 通过 |
| Poppy 最终 Python 回归 | `uv run pytest tests -q` | Runtime/Gateway/权限/附件无回归 | 155 passed，6 warnings | 通过 |
| Poppy 前端构建 | `npm run build` | TypeScript 和 Vite 构建通过 | 构建成功 | 通过 |
| Poppy Rust 检查 | `cargo fmt --check && cargo check` | 格式与 Tauri 壳编译通过 | 通过 | 通过 |
| Poppy 审计后全量回归 | `uv run pytest tests -q` | 授权撤销、流式停止与原有行为无回归 | 158 passed，6 warnings | 通过 |

## 错误日志
| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|--------|------|---------|---------|
| 2026-07-13 | 无 | 0 | - |
| 2026-07-13 | 未检测到 Rust/Cargo/Xcodebuild，暂时不能构建 Tauri `.app` | 1 | 先实现并验证 Python Runtime/Gateway；原生 UI 阶段安装所需工具链 |
| 2026-07-13 | Shell 取消测试 3 秒内仍为 running | 1 | 定位为工具注册表捕获旧令牌；新增 `configure_run_controls()` 并在替换令牌时重建工具上下文 |
| 2026-07-13 | 全量回归中 1 个测试仍 mock `subprocess.run` | 1 | 测试目标是验证模块委托，改为 mock `pico.tools.tool_run_shell`，不再绑定进程实现 |
| 2026-07-13 | 沙箱内 curl 无法连接已监听的 127.0.0.1 Gateway | 1 | `lsof` 已证明仅绑定 loopback；按环境要求在沙箱外重做健康检查 |
| 2026-07-13 | React 19 类型要求 `useRef` 提供初始值 | 1 | 将 WebSocket 清理引用初始化为 `undefined` |

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 阶段 6：sidecar 打包与完整验收 |
| 我要去哪里？ | 流式 Runtime → Gateway → Tauri → 权限/记忆 → sidecar 打包与验收 |
| 目标是什么？ | 交付可双击启动、使用 DeepSeek、具备授权/审批/取消的 macOS Poppy 桌面助手 |
| 我学到了什么？ | 见 `findings.md` |
| 我做了什么？ | 见上方记录 |

---
*每个阶段完成后或遇到错误时更新此文件*
