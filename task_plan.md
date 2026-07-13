# 任务计划：Poppy macOS 桌面个人助手

## 目标
交付一个可在 macOS 上双击启动的 Poppy 桌面应用：用户配置 DeepSeek API Key 后，可创建和恢复会话，在明确授权的目录中对话、查看流式回复与工具执行、审批风险操作，并随时停止当前任务。

## 当前阶段
阶段 6

## 各阶段

### 阶段 1：基线审计与可复用 Runtime 边界
- [x] 核对当前代码、测试、开发依赖和 macOS 打包工具链
- [x] 抽取不依赖 CLI 的应用装配入口
- [x] 定义 Run、事件、审批和取消协议
- [x] 用测试证明 CLI 行为保持兼容
- **状态：** complete

### 阶段 2：流式 DeepSeek Runtime 与任务控制
- [x] 为 Anthropic-compatible Provider 增加真正的 SSE 增量读取
- [x] 为 Runtime 增加事件输出
- [x] 支持暂停等待审批、停止生成和取消工具
- [x] 覆盖异常、断流、取消和恢复测试
- **状态：** complete

### 阶段 3：Local Gateway 与桌面数据层
- [x] 实现只监听回环地址的 HTTP/WebSocket Gateway
- [x] 实现会话、Run、审批、设置、目录授权和记忆 API
- [x] 建立应用数据目录、SQLite 索引和连接令牌
- [x] 完成 API、安全和并发集成测试
- **状态：** complete

### 阶段 4：Tauri 桌面界面
- [x] 创建 Tauri 2 + React + TypeScript 工程
- [x] 实现聊天、会话、设置、Markdown/代码/附件界面
- [x] 实现工具卡片、审批弹窗、停止和错误重试
- [x] 实现托盘、全局快捷键和明暗主题
- **状态：** complete

### 阶段 5：目录授权、个人记忆与系统密钥
- [x] 使用系统目录选择器管理授权目录
- [x] 在所有工具执行路径强制授权范围
- [x] 使用 macOS Keychain 保存和删除 DeepSeek API Key
- [x] 提供个人记忆的查看、编辑和删除
- [x] 验证路径逃逸、符号链接、规则越权和密钥泄漏防护
- **状态：** complete

### 阶段 6：sidecar 打包与完整验收
- [x] 打包 Python Gateway 并由 Tauri 管理生命周期
- [ ] 生成可双击启动的 macOS `.app`
- [ ] 执行真实 DeepSeek 冒烟测试和桌面端端到端场景
- [ ] 按设计文档逐项完成验收审计
- [x] 更新使用、构建和故障排查文档
- **状态：** in_progress

## 关键问题
1. 当前机器是否具备 Node、Rust、Tauri 和 macOS 打包所需环境？
2. 现有同步 `Pico.ask()` 如何在保持 CLI 兼容的同时演进为可流式、可暂停和可取消的接口？
3. 第一阶段采用何种最小但可验证的 macOS Keychain 与目录授权实现？

## 已做决策
| 决策 | 理由 |
|------|------|
| DeepSeek 云 API 是第一阶段唯一模型入口 | 用户明确不使用 Ollama，缩小模型管理范围 |
| 保留现有 Python Pico Runtime | Poppy 复用已有模型、工具、会话、记忆和安全测试，不重写稳定内核 |
| Tauri 2 + React/TypeScript 作为桌面端 | 满足 macOS 桌面、托盘、快捷键与后续跨平台需求 |
| Python Gateway 作为 Tauri sidecar | UI 与 Agent Runtime 解耦，CLI 可继续复用 Runtime |
| 第一阶段在 main 工作树持续实现 | 当前仓库已由用户创建并推送，目标是直接交付产品 |
| `AssistantService` 在线程中运行同步 Runtime | Gateway 可立即获得 run_id、补发事件、响应审批和取消，同时保持 CLI 同步 API |

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| 暂无 | 0 | - |

## 备注
- 权威需求与验收标准见 `docs/architecture/desktop-personal-assistant-phase-1.md`。
- 每完成一个阶段更新状态和 `progress.md`，不以局部演示替代完整交付。
