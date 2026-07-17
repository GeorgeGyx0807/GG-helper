# Poppy macOS 桌面版指南

## 第一阶段目标

第一阶段交付一条完整、可控的个人助手闭环：用户双击打开 Poppy，配置 DeepSeek API Key，明确选择可访问的文件夹，然后在桌面窗口中发起任务；Poppy 流式展示回答和工具过程，风险操作等待用户批准，任务可随时停止，会话和可管理的个人记忆会被保存。

第一阶段不包含 Ollama、本地模型、浏览器自动化、日历、邮件、语音或跨设备同步。

## 第一次使用

1. 双击 `Poppy.app`。首次启动时，打包的 Python Gateway 解压和启动可能需要十几秒。
2. 打开 Settings，输入 DeepSeek API Key。密钥只保存在 macOS Keychain，不写入 Poppy 的 SQLite 数据库。
3. 点击添加文件夹，选择授权范围：只读、读写，或读写并允许 Shell。
4. 新建会话并开始聊天。附件也必须位于当前会话已授权的文件夹内。
5. 写文件等风险操作会显示审批弹窗；“始终允许”只对同一个工具和同一个精确文件生效，Shell 不提供永久授权。

顶部太阳按钮可切换浅色与深色主题。关闭主窗口会把 Poppy 隐藏到系统托盘；点击托盘图标或按 `Command+Shift+Space` 可再次显示。需要完全退出时使用托盘菜单中的 Quit Poppy。

首次读取密钥时，macOS 可能弹出“Poppy 想要使用你储存在钥匙串中的机密信息”。这里应输入 Mac 的登录密码，而不是 DeepSeek API Key；个人自用可选择“始终允许”。开发者本地重新打包会改变临时签名，因此新构建第一次启动时可能再次询问，正式签名版本不会随每次构建改变身份。

## 数据与安全边界

- 应用数据：`~/Library/Application Support/Poppy/`
- Keychain 服务：`com.george.poppy`
- Gateway：只监听随机的 `127.0.0.1` 端口，并要求每次启动生成的随机令牌
- 默认模型：`deepseek-v4-pro`
- 默认接口：`https://api.deepseek.com/anthropic`
- 文件访问：只允许用户显式授权的目录，并在执行层再次检查真实路径和权限
- 个人记忆：可查看、编辑、删除；疑似 API Key 等秘密内容会被拒绝保存

## 本地构建

需要 Python 3.10+、uv、Node.js/npm、Rust stable 和 Xcode Command Line Tools。

```bash
uv sync --dev
cd desktop
npm install
cd ..
scripts/build_desktop_sidecar.sh
cd desktop
npm run tauri build -- --bundles app
```

构建产物位于：

```text
desktop/src-tauri/target/release/bundle/macos/Poppy.app
```

开发者本地构建默认没有 Apple Developer ID 签名和公证。若要分发给其他 Mac，需要补充正式签名、公证、版本更新和发布流程。

## 验证

```bash
uv run pytest tests -q
uv run ruff check poppy tests scripts
cd desktop && npm run build
cd desktop/src-tauri && cargo fmt --check && cargo check
```

如果窗口长时间停留在 Starting Poppy，先完全退出托盘中的 Poppy 后重开；如果模型调用失败，检查 API Key、模型名和 DeepSeek 账户状态。移除 Keychain 中的密钥后，Poppy 会重启本地 Gateway 并回到连接 DeepSeek 的引导状态。
