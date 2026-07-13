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
          <div><h2>Settings</h2><p>Models, permissions, and personal memory.</p></div>
          <button className="icon-button" onClick={props.onClose}><X size={18} /></button>
        </header>

        <div className="settings-content">
          <div className="settings-group">
            <h3>DeepSeek</h3>
            <label>Model<input value={model} onChange={(e) => setModel(e.target.value)} /></label>
            <label>API base URL<input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
            <label>Timeout (seconds)<input type="number" value={timeout} onChange={(e) => setTimeoutValue(Number(e.target.value))} /></label>
            <button className="secondary-button" onClick={() => props.onSaveSettings({ model, base_url: baseUrl, timeout })}>Save model settings</button>
            <div className="key-row">
              <KeyRound size={17} />
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={props.settings.api_key_configured ? "API key configured" : "Enter DeepSeek API key"} />
              <button onClick={saveKey} disabled={!apiKey.trim() || savingKey}>{savingKey ? "Saving…" : "Save key"}</button>
            </div>
            {props.settings.api_key_configured && (
              <button
                className="text-danger-button"
                disabled={deletingKey}
                onClick={async () => {
                  setDeletingKey(true);
                  setKeyError("");
                  try {
                    await props.onDeleteApiKey();
                  } catch (reason) {
                    setKeyError(reason instanceof Error ? reason.message : String(reason));
                  } finally {
                    setDeletingKey(false);
                  }
                }}
              >
                <Trash2 size={15} /> {deletingKey ? "Removing…" : "Remove key from Keychain"}
              </button>
            )}
            {keyError && <div className="inline-error key-error">{keyError}</div>}
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>Authorized folders</h3><p>Poppy can only access folders listed here.</p></div><button className="icon-button" onClick={props.onAddFolder}><Plus size={17} /></button></div>
            <div className="settings-list">
              {props.grants.map((grant) => (
                <div className="settings-list-item" key={grant.id}>
                  <div><strong>{grant.path.split("/").pop()}</strong><span>{grant.path}</span><small>{grant.can_write ? "Read & write" : "Read only"}{grant.can_shell ? " · Shell" : ""}</small></div>
                  <button className="icon-button danger" onClick={() => props.onDeleteGrant(grant.id)}><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>Personal memory</h3><p>Visible, editable facts Poppy may recall.</p></div></div>
            <form className="memory-form" onSubmit={async (event) => { event.preventDefault(); if (memory.trim()) { await props.onAddMemory(memory); setMemory(""); } }}>
              <input value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="e.g. Prefer concise Chinese answers" />
              <button disabled={!memory.trim()}><Plus size={16} /> Add</button>
            </form>
            <div className="settings-list">
              {props.memories.map((item) => (
                <div className="settings-list-item" key={item.id}>
                  <div className="memory-content"><strong>{item.category}</strong>{editingMemory === item.id ? (
                    <input value={editingContent} onChange={(event) => setEditingContent(event.target.value)} autoFocus />
                  ) : <span>{item.content}</span>}</div>
                  <div className="item-actions">
                    {editingMemory === item.id ? (
                      <button className="icon-button" aria-label="Save memory" onClick={async () => { if (editingContent.trim()) await props.onUpdateMemory(item.id, editingContent.trim()); setEditingMemory(undefined); }}><Check size={16} /></button>
                    ) : (
                      <button className="icon-button" aria-label="Edit memory" onClick={() => { setEditingMemory(item.id); setEditingContent(item.content); }}><Pencil size={15} /></button>
                    )}
                    <button className="icon-button danger" aria-label="Delete memory" onClick={() => props.onDeleteMemory(item.id)}><Trash2 size={16} /></button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-group">
            <div className="group-heading"><div><h3>Always-allow rules</h3><p>Rules are limited to one tool and one exact file.</p></div></div>
            <div className="settings-list">
              {props.approvalRules.map((rule) => (
                <div className="settings-list-item" key={rule.id}>
                  <div><strong>{rule.tool_name}</strong><span>{rule.path_scope}</span><small>{rule.operation}</small></div>
                  <button className="icon-button danger" aria-label="Delete approval rule" onClick={() => props.onDeleteApprovalRule(rule.id)}><Trash2 size={16} /></button>
                </div>
              ))}
              {!props.approvalRules.length && <p className="empty-copy">No permanent approval rules.</p>}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
