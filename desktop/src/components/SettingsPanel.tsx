import { Check, KeyRound, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ApprovalRule, Grant, MemoryItem, Settings } from "../types";

type Props = {
  settings: Settings;
  grants: Grant[];
  memories: MemoryItem[];
  approvalRules: ApprovalRule[];
  onClose: () => void;
  onSaveSettings: (values: Partial<Settings>) => Promise<void>;
  onSaveApiKey: (apiKey: string) => Promise<void>;
  onDeleteApiKey: () => Promise<void>;
  onTestConnection: () => Promise<{ status: string; model: string }>;
  onAddFolder: () => void;
  onDeleteGrant: (id: string) => Promise<void>;
  onAddMemory: (content: string) => Promise<void>;
  onDeleteMemory: (id: string) => Promise<void>;
  onUpdateMemory: (id: string, content: string) => Promise<void>;
  onDeleteApprovalRule: (id: string) => Promise<void>;
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
      await props.onSaveApiKey(apiKey.trim());
      setApiKey("");
    } catch (reason) {
      setKeyError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSavingKey(false);
    }
  }

  return (
    <div className="settings-backdrop" onMouseDown={props.onClose}>
      <section className="settings-panel" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><h2>设置</h2><p>模型、权限和个人记忆</p></div>
          <button className="icon-button" onClick={props.onClose}><X size={18} /></button>
        </header>

        <div className="settings-content">
          <div className="settings-group">
            <h3>DeepSeek</h3>
            <label><span>模型</span><input className="settings-field" value={model} onChange={(e) => setModel(e.target.value)} /></label>
            <label><span>API 地址</span><input className="settings-field" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
            <label><span>超时时间（秒）</span><input className="settings-field" type="number" value={timeout} onChange={(e) => setTimeoutValue(Number(e.target.value))} /></label>
            <button className="secondary-button" onClick={() => props.onSaveSettings({ model, base_url: baseUrl, timeout })}>保存模型设置</button>
            <div className="key-row">
              <KeyRound size={17} />
              <input className="settings-field" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={props.settings.api_key_configured ? "API 密钥已配置" : "输入 DeepSeek API 密钥"} />
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
                      await props.onDeleteApiKey();
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
            <div className="group-heading"><div><h3>始终允许规则</h3><p>规则仅限一个工具和一个确切文件。</p></div></div>
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
