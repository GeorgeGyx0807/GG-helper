import { Check, Copy, KeyRound, Link2, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ApprovalRule, AuditEvent, FeishuSettings, Grant, LibrarySource, MemoryItem, Settings } from "../types";

type Props = {
  settings: Settings;
  feishu: FeishuSettings;
  grants: Grant[];
  memories: MemoryItem[];
  approvalRules: ApprovalRule[];
  librarySources: LibrarySource[];
  auditEvents: AuditEvent[];
  onClose: () => void;
  onSaveSettings: (values: Partial<Settings>) => Promise<void>;
  onSaveApiKey: (apiKey: string, provider: "deepseek" | "dashscope" | "feishu") => Promise<void>;
  onDeleteApiKey: (provider: "deepseek" | "dashscope" | "feishu") => Promise<void>;
  onSaveFeishuSettings: (values: Partial<FeishuSettings>) => Promise<void>;
  onSaveFeishuSecret: (secret: string) => Promise<void>;
  onDeleteFeishuSecret: () => Promise<void>;
  onRestartFeishu: () => Promise<void>;
  onDeleteFeishuSession: (id: string) => Promise<void>;
  onTestConnection: () => Promise<{ status: string; model: string }>;
  onAddFolder: () => void;
  onDeleteGrant: (id: string) => Promise<void>;
  onAddMemory: (content: string) => Promise<void>;
  onDeleteMemory: (id: string) => Promise<void>;
  onUpdateMemory: (id: string, content: string) => Promise<void>;
  onDeleteApprovalRule: (id: string) => Promise<void>;
  onAddLibrarySource: () => void;
  onDeleteLibrarySource: (id: string) => Promise<void>;
  onReindexLibrary: (id?: string) => Promise<void>;
};

export function SettingsPanel(props: Props) {
  const [model, setModel] = useState(props.settings.model);
  const [baseUrl, setBaseUrl] = useState(props.settings.base_url);
  const [timeout, setTimeoutValue] = useState(props.settings.timeout);
  const [apiKey, setApiKey] = useState("");
  const [memory, setMemory] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [deletingKey, setDeletingKey] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState("");
  const [keyError, setKeyError] = useState("");
  const [editingMemory, setEditingMemory] = useState<string>();
  const [editingContent, setEditingContent] = useState("");
  const [feishuEnabled, setFeishuEnabled] = useState(props.feishu.feishu_enabled);
  const [feishuAppId, setFeishuAppId] = useState(props.feishu.feishu_app_id);
  const [feishuSecret, setFeishuSecret] = useState("");
  const [feishuUsers, setFeishuUsers] = useState(props.feishu.feishu_allowed_users.join("\n"));
  const [feishuChats, setFeishuChats] = useState(props.feishu.feishu_allowed_chats.join("\n"));
  const [feishuRequireMention, setFeishuRequireMention] = useState(props.feishu.feishu_require_mention);
  const [feishuCloudEnabled, setFeishuCloudEnabled] = useState(props.feishu.feishu_cloud_enabled);
  const [feishuWorkspace, setFeishuWorkspace] = useState(props.feishu.feishu_workspace_root);
  const [feishuMaxFileMb, setFeishuMaxFileMb] = useState(props.feishu.feishu_max_file_mb);
  const [savingFeishu, setSavingFeishu] = useState(false);
  const [restartingFeishu, setRestartingFeishu] = useState(false);
  const [feishuError, setFeishuError] = useState("");
  const [pairingCopied, setPairingCopied] = useState(false);
  const [cloudScopesCopied, setCloudScopesCopied] = useState(false);
  const qwenConfigured = model.toLowerCase().startsWith("qwen") || baseUrl.includes("dashscope") || baseUrl.includes("maas.aliyuncs.com");

  useEffect(() => {
    setModel(props.settings.model);
    setBaseUrl(props.settings.base_url);
    setTimeoutValue(props.settings.timeout);
  }, [props.settings]);

  async function saveKey() {
    if (!apiKey.trim()) return;
    setSavingKey(true);
    setKeyError("");
    try {
      await props.onSaveApiKey(apiKey.trim(), qwenConfigured ? "dashscope" : "deepseek");
      setApiKey("");
    } catch (reason) {
      setKeyError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSavingKey(false);
    }
  }

  const splitIds = (value: string) => [...new Set(value.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean))];

  async function saveFeishu() {
    setSavingFeishu(true);
    setFeishuError("");
    try {
      await props.onSaveFeishuSettings({
        feishu_enabled: feishuEnabled,
        feishu_app_id: feishuAppId.trim(),
        feishu_allowed_users: splitIds(feishuUsers),
        feishu_allowed_chats: splitIds(feishuChats),
        feishu_require_mention: feishuRequireMention,
        feishu_cloud_enabled: feishuCloudEnabled,
        feishu_workspace_root: feishuWorkspace,
        feishu_max_file_mb: feishuMaxFileMb,
      });
      if (feishuSecret.trim()) {
        await props.onSaveFeishuSecret(feishuSecret.trim());
        setFeishuSecret("");
      }
    } catch (reason) {
      setFeishuError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSavingFeishu(false);
    }
  }

  const feishuStatusText: Record<FeishuSettings["feishu_status"], string> = {
    disabled: "未启用",
    not_configured: "待配置",
    connecting: "正在连接",
    connected: "已连接",
    reconnecting: "正在重连",
    error: "连接失败",
    stopped: "已停止",
  };

  return (
    <div className="settings-backdrop" onMouseDown={props.onClose}>
      <section className="settings-panel" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><h2>设置</h2><p>模型、权限和个人记忆</p></div>
          <button className="icon-button" onClick={props.onClose}><X size={18} /></button>
        </header>

        <div className="settings-content">
          <div className="settings-group">
            <h3>{qwenConfigured ? "Qwen / 阿里云百炼" : "模型服务"}</h3>
            <label><span>模型</span><input className="settings-field" value={model} onChange={(e) => setModel(e.target.value)} /></label>
            <label><span>API 地址</span><input className="settings-field" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
            <label><span>超时时间（秒）</span><input className="settings-field" type="number" value={timeout} onChange={(e) => setTimeoutValue(Number(e.target.value))} /></label>
            <button className="secondary-button" onClick={() => props.onSaveSettings({ model, base_url: baseUrl, timeout })}>保存模型设置</button>
            <div className="key-row">
              <KeyRound size={17} />
              <input className="settings-field" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={props.settings.api_key_configured ? "API 密钥已配置" : `输入${qwenConfigured ? " Qwen / DashScope" : "模型服务"} API 密钥`} />
              <button onClick={saveKey} disabled={!apiKey.trim() || savingKey}>{savingKey ? "保存中…" : "保存密钥"}</button>
            </div>
            {props.settings.api_key_configured && (
              <div className="key-actions">
                <span>API 密钥已保存在 macOS 钥匙串中。</span>
                <button
                  className="secondary-button"
                  disabled={testingConnection}
                  onClick={async () => {
                    setTestingConnection(true);
                    setKeyError("");
                    setConnectionStatus("");
                    try {
                      const result = await props.onTestConnection();
                      setConnectionStatus(`已连接到 ${result.model}`);
                    } catch (reason) {
                      setKeyError(reason instanceof Error ? reason.message : String(reason));
                    } finally {
                      setTestingConnection(false);
                    }
                  }}
                >{testingConnection ? "测试中…" : "测试连接"}</button>
                <button
                  className="text-danger-button"
                  disabled={deletingKey}
                  onClick={async () => {
                    setDeletingKey(true);
                    setKeyError("");
                    setConnectionStatus("");
                    try {
                      await props.onDeleteApiKey(qwenConfigured ? "dashscope" : "deepseek");
                    } catch (reason) {
                      setKeyError(reason instanceof Error ? reason.message : String(reason));
                    } finally {
                      setDeletingKey(false);
                    }
                  }}
                >
                  <Trash2 size={15} /> {deletingKey ? "移除中…" : "移除密钥"}
                </button>
              </div>
            )}
            {connectionStatus && <div className="inline-success">{connectionStatus}</div>}
            {keyError && <div className="inline-error key-error">{keyError}</div>}
          </div>

          <div className="settings-group feishu-settings">
            <div className="group-heading">
              <div>
                <h3>飞书接入</h3>
                <p>Poppy 运行时通过长连接接收消息，无需公网服务器。</p>
              </div>
              <span className={`feishu-status ${props.feishu.feishu_status}`}>
                <span />{feishuStatusText[props.feishu.feishu_status]}
              </span>
            </div>

            <div className="feishu-toggle-row">
              <div><strong>启用飞书机器人</strong><span>退出 Poppy 后机器人会离线。</span></div>
              <button
                type="button"
                className={`toggle-button ${feishuEnabled ? "active" : ""}`}
                aria-pressed={feishuEnabled}
                onClick={() => setFeishuEnabled((value) => !value)}
              ><span /></button>
            </div>

            <label><span>App ID</span><input className="settings-field" value={feishuAppId} onChange={(event) => setFeishuAppId(event.target.value)} placeholder="cli_xxxxxxxxxxxxxxxx" /></label>
            <label><span>App Secret</span><input className="settings-field" type="password" value={feishuSecret} onChange={(event) => setFeishuSecret(event.target.value)} placeholder={props.feishu.feishu_secret_configured ? "已保存在 macOS 钥匙串；留空则不修改" : "输入飞书 App Secret"} /></label>
            <label>
              <span>项目目录</span>
              <select className="settings-field" value={feishuWorkspace} onChange={(event) => setFeishuWorkspace(event.target.value)}>
                <option value="">仅使用当前飞书会话的附件</option>
                {props.grants.map((grant) => <option value={grant.path} key={grant.id}>{grant.path}</option>)}
              </select>
            </label>
            <label><span>单文件上限（MB）</span><input className="settings-field" type="number" min={1} max={50} value={feishuMaxFileMb} onChange={(event) => setFeishuMaxFileMb(Math.max(1, Math.min(50, Number(event.target.value) || 1)))} /></label>
            <div className="feishu-toggle-row">
              <div>
                <strong>读取飞书云内容</strong>
                <span>允许已绑定用户发送云文档、知识库、多维表格链接，或用“读取飞书日历”查询应用可见日程。</span>
              </div>
              <button
                type="button"
                className={`toggle-button ${feishuCloudEnabled ? "active" : ""}`}
                aria-pressed={feishuCloudEnabled}
                onClick={() => setFeishuCloudEnabled((value) => !value)}
              ><span /></button>
            </div>
            <div className="feishu-pairing">
              <div>
                <strong>云内容只读权限</strong>
                <span>
                  开通后要发布应用新版本；具体文档、知识库、多维表格和日历仍需共享给 Poppy 应用。
                </span>
                <small>{props.feishu.feishu_cloud_scope_ids.join(" · ")}</small>
              </div>
              <button
                className="secondary-button compact"
                onClick={async () => {
                  await navigator.clipboard.writeText(
                    props.feishu.feishu_cloud_permission_url
                    || props.feishu.feishu_cloud_scope_ids.join(","),
                  );
                  setCloudScopesCopied(true);
                  window.setTimeout(() => setCloudScopesCopied(false), 1_500);
                }}
              ><Copy size={14} /> {cloudScopesCopied ? "已复制" : "复制权限入口"}</button>
            </div>
            <label className="feishu-checkbox"><span>群聊策略</span><span><input type="checkbox" checked={feishuRequireMention} onChange={(event) => setFeishuRequireMention(event.target.checked)} /> 仅在群聊中 @Poppy 时回复</span></label>
            <label className="feishu-multiline"><span>允许的用户 ID</span><textarea value={feishuUsers} onChange={(event) => setFeishuUsers(event.target.value)} placeholder="每行一个 open_id；留空时可用下方绑定码完成首次绑定" /></label>
            <label className="feishu-multiline"><span>允许的群聊 ID</span><textarea value={feishuChats} onChange={(event) => setFeishuChats(event.target.value)} placeholder="每行一个 chat_id；默认不响应任何群聊" /></label>

            {!props.feishu.feishu_allowed_users.length && props.feishu.feishu_pairing_code && (
              <div className="feishu-pairing">
                <div><strong>首次绑定码</strong><span>私聊机器人发送“绑定 {props.feishu.feishu_pairing_code}”</span></div>
                <button
                  className="secondary-button compact"
                  onClick={async () => {
                    await navigator.clipboard.writeText(`绑定 ${props.feishu.feishu_pairing_code}`);
                    setPairingCopied(true);
                    window.setTimeout(() => setPairingCopied(false), 1_500);
                  }}
                ><Copy size={14} /> {pairingCopied ? "已复制" : props.feishu.feishu_pairing_code}</button>
              </div>
            )}

            <div className="feishu-actions">
              <button className="secondary-button" onClick={() => void saveFeishu()} disabled={savingFeishu || !feishuAppId.trim()}><Link2 size={15} /> {savingFeishu ? "保存中…" : "保存并连接"}</button>
              <button
                className="secondary-button"
                disabled={restartingFeishu}
                onClick={async () => {
                  setRestartingFeishu(true);
                  setFeishuError("");
                  try { await props.onRestartFeishu(); }
                  catch (reason) { setFeishuError(reason instanceof Error ? reason.message : String(reason)); }
                  finally { setRestartingFeishu(false); }
                }}
              ><RefreshCw size={15} /> {restartingFeishu ? "重连中…" : "重新连接"}</button>
              {props.feishu.feishu_secret_configured && <button className="text-danger-button" onClick={async () => {
                setFeishuError("");
                try { await props.onDeleteFeishuSecret(); }
                catch (reason) { setFeishuError(reason instanceof Error ? reason.message : String(reason)); }
              }}><Trash2 size={15} /> 移除密钥</button>}
            </div>
            {(feishuError || props.feishu.feishu_error) && <div className="inline-error key-error">{feishuError || props.feishu.feishu_error}</div>}
            {props.feishu.feishu_status === "connected" && <div className="inline-success">{props.feishu.feishu_bot_name ? `${props.feishu.feishu_bot_name} 已连接` : "飞书机器人已连接"}{props.feishu.feishu_connected_at ? ` · ${props.feishu.feishu_connected_at}` : ""}</div>}

            {!!props.feishu.feishu_sessions.length && (
              <div className="settings-list feishu-session-list">
                {props.feishu.feishu_sessions.map((session) => (
                  <div className="settings-list-item" key={session.id}>
                    <div><strong>{session.thread_id ? "群聊话题" : "飞书私聊"}</strong><span>{session.chat_id}{session.sender_open_id ? ` · ${session.sender_open_id}` : ""}</span><small>{session.workspace_root || "附件与资料库模式"} · {session.updated_at}</small></div>
                    <button className="icon-button danger" aria-label="清除飞书会话映射" onClick={() => void props.onDeleteFeishuSession(session.id)}><Trash2 size={16} /></button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>操作审计</h3><p>最近的工具调用、审批和结果都记录在本机。</p></div></div>
            <div className="settings-list">
              {props.auditEvents.slice(0, 20).map((event) => (
                <div className="settings-list-item" key={event.id}>
                  <div><strong>{event.tool_name || event.event_type}</strong><span>{event.scope || "无外部范围"}</span><small>{event.created_at}</small></div>
                </div>
              ))}
              {!props.auditEvents.length && <p className="empty-copy">还没有工具操作记录。</p>}
            </div>
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>个人资料库</h3><p>只索引已授权文件夹中的文本，撤销授权会立即移除对应索引。</p></div><button className="icon-button" onClick={props.onAddLibrarySource} aria-label="添加资料库目录"><Plus size={17} /></button></div>
            <div className="settings-list">
              {props.librarySources.map((source) => (
                <div className="settings-list-item" key={source.id}>
                  <div>
                    <strong>{source.path.split(/[\\/]/).filter(Boolean).pop()}</strong>
                    <span>{source.path}</span>
                    <small>
                      {source.index_status === "indexing"
                        ? `后台索引中 ${source.index_progress ?? 0}% · 已处理 ${source.indexed_count ?? 0} 个`
                        : source.index_status === "error"
                          ? `索引完成但有 ${source.failed_count ?? 0} 个失败文件`
                          : source.last_indexed_at
                            ? `实时监听中 · 已索引 ${source.document_count ?? 0} 个文档 · ${source.last_indexed_at}`
                            : "等待后台索引"}
                    </small>
                    {source.last_error && <small className="error-copy">{source.last_error}</small>}
                    {!!source.failures?.length && <details>
                      <summary>查看失败文件（{source.failures.length}）</summary>
                      {source.failures.map((failure) => <small key={failure.path}>{failure.path.split(/[\\/]/).pop()}：{failure.error}</small>)}
                    </details>}
                  </div>
                  <div className="item-actions"><button className="secondary-button compact" onClick={() => void props.onReindexLibrary(source.id)}>重建索引</button><button className="icon-button danger" onClick={() => void props.onDeleteLibrarySource(source.id)}><Trash2 size={16} /></button></div>
                </div>
              ))}
              {!props.librarySources.length && <p className="empty-copy">还没有资料库目录。授权文件夹后会自动建立本地索引。</p>}
            </div>
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>已授权文件夹</h3><p>Poppy 只能访问列在这里的文件夹。</p></div><button className="icon-button" onClick={props.onAddFolder} aria-label="添加文件夹"><Plus size={17} /></button></div>
            <div className="settings-list">
              {props.grants.map((grant) => (
                <div className="settings-list-item" key={grant.id}>
                  <div><strong>{grant.path.split("/").pop()}</strong><span>{grant.path}</span><small>{grant.can_write ? "读写" : "仅阅读"}{grant.can_shell ? " · 终端" : ""}</small></div>
                  <button className="icon-button danger" onClick={() => props.onDeleteGrant(grant.id)}><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>个人记忆</h3><p>Poppy 可以记住的可见、可编辑信息。</p></div></div>
            <form className="memory-form" onSubmit={async (event) => { event.preventDefault(); if (memory.trim()) { await props.onAddMemory(memory); setMemory(""); } }}>
              <input className="settings-field" value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="例如：用简洁的中文回答" />
              <button disabled={!memory.trim()}><Plus size={16} /> 添加</button>
            </form>
            <div className="settings-list">
              {props.memories.map((item) => (
                <div className="settings-list-item" key={item.id}>
                  <div className="memory-content"><strong>{item.category}</strong>{editingMemory === item.id ? (
                    <input value={editingContent} onChange={(event) => setEditingContent(event.target.value)} autoFocus />
                  ) : <span>{item.content}</span>}</div>
                  <div className="item-actions">
                    {editingMemory === item.id ? (
                      <button className="icon-button" aria-label="保存记忆" onClick={async () => { if (editingContent.trim()) await props.onUpdateMemory(item.id, editingContent.trim()); setEditingMemory(undefined); }}><Check size={16} /></button>
                    ) : (
                      <button className="icon-button" aria-label="编辑记忆" onClick={() => { setEditingMemory(item.id); setEditingContent(item.content); }}><Pencil size={15} /></button>
                    )}
                    <button className="icon-button danger" aria-label="删除记忆" onClick={() => props.onDeleteMemory(item.id)}><Trash2 size={16} /></button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>始终允许规则</h3><p>规则绑定具体工具和范围，可随时撤销。</p></div></div>
            <div className="settings-list">
              {props.approvalRules.map((rule) => (
                <div className="settings-list-item" key={rule.id}>
                  <div><strong>{rule.tool_name}</strong><span>{rule.path_scope}</span><small>{rule.operation}</small></div>
                  <button className="icon-button danger" aria-label="删除允许规则" onClick={() => props.onDeleteApprovalRule(rule.id)}><Trash2 size={16} /></button>
                </div>
              ))}
              {!props.approvalRules.length && <p className="empty-copy">暂无永久允许规则。</p>}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
