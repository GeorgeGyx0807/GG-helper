import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowUp,
  Bot,
  FolderOpen,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  ShieldCheck,
  Square,
  Sun,
  UserRound,
  WifiOff,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";
import { SettingsPanel } from "./components/SettingsPanel";
import { Sidebar } from "./components/Sidebar";
import { ToolCard } from "./components/ToolCard";
import { GatewayClient, resolveGatewayInfo } from "./lib/gateway";
import type { GatewayInfo } from "./lib/gateway";
import type { ApprovalRule, Grant, HistoryItem, MemoryItem, RunEvent, SessionSummary, Settings, ToolCall } from "./types";

type ApprovalPrompt = { approvalId: string; toolName: string; arguments: Record<string, unknown> };
type RetryRequest = { content: string; attachments: string[] };

const defaultSettings: Settings = {
  model: "deepseek-v4-pro",
  base_url: "https://api.deepseek.com/anthropic",
  timeout: 300,
  api_key_configured: false,
};

function App() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const saved = window.localStorage.getItem("poppy-theme");
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  const [client, setClient] = useState<GatewayClient | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [messages, setMessages] = useState<HistoryItem[]>([]);
  const [tools, setTools] = useState<ToolCall[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [approvalRules, setApprovalRules] = useState<ApprovalRule[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [activeRun, setActiveRun] = useState<string>();
  const [approval, setApproval] = useState<ApprovalPrompt>();
  const [pendingFolder, setPendingFolder] = useState<string>();
  const [createSessionAfterGrant, setCreateSessionAfterGrant] = useState(false);
  const [choosingWorkspace, setChoosingWorkspace] = useState(false);
  const [lastRequest, setLastRequest] = useState<RetryRequest>();
  const disconnectRef = useRef<(() => void) | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(() => sessions.find((session) => session.id === selectedId), [sessions, selectedId]);

  useEffect(() => {
    void initialize();
    return () => disconnectRef.current?.();
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("poppy-theme", theme);
  }, [theme]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, tools]);

  async function initialize() {
    setLoading(true);
    try {
      const info = await resolveGatewayInfo();
      await connectGateway(info);
      setError("");
    } catch (reason) {
      setConnected(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function connectGateway(info: GatewayInfo) {
      const gateway = new GatewayClient(info);
      await gateway.waitUntilHealthy(info.token ? 30_000 : 750);
      let snapshot: [SessionSummary[], Grant[], Settings, MemoryItem[], ApprovalRule[]] | undefined;
      let lastError: unknown;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        try {
          snapshot = await Promise.all([
            gateway.sessions(), gateway.grants(), gateway.settings(), gateway.memories(), gateway.approvalRules(),
          ]);
          break;
        } catch (reason) {
          lastError = reason;
          await new Promise((resolve) => window.setTimeout(resolve, 350));
        }
      }
      if (!snapshot) throw lastError instanceof Error ? lastError : new Error("Poppy could not load local data");
      const [sessionList, grantList, appSettings, memoryList, ruleList] = snapshot;
      disconnectRef.current?.();
      setClient(gateway);
      setConnected(true);
      setSessions(sessionList);
      setGrants(grantList);
      setSettings(appSettings);
      setMemories(memoryList);
      setApprovalRules(ruleList);
      if (sessionList[0]) await selectSession(gateway, sessionList[0].id);
  }

  async function saveApiKey(apiKey: string) {
    setLoading(true);
    let keyStored = false;
    try {
      const info = await invoke<GatewayInfo>("set_api_key", { apiKey });
      keyStored = true;
      await connectGateway(info);
      setError("");
    } catch (reason) {
      const failure = keyStored
        ? new Error("The API key was saved, but Poppy could not reconnect. Choose Try again or restart Poppy.")
        : reason instanceof Error ? reason : new Error(String(reason));
      setError(failure.message);
      throw failure;
    } finally {
      setLoading(false);
    }
  }

  async function deleteApiKey() {
    setLoading(true);
    try {
      const info = await invoke<GatewayInfo>("delete_api_key");
      await connectGateway(info);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setLoading(false);
    }
  }

  async function refreshMeta() {
    if (!client) return;
    const [sessionList, grantList, appSettings, memoryList, ruleList] = await Promise.all([
      client.sessions(), client.grants(), client.settings(), client.memories(), client.approvalRules(),
    ]);
    setSessions(sessionList);
    setGrants(grantList);
    setSettings(appSettings);
    setMemories(memoryList);
    setApprovalRules(ruleList);
  }

  async function selectSession(gateway: GatewayClient, id: string) {
    disconnectRef.current?.();
    const detail = await gateway.session(id);
    setSelectedId(id);
    setMessages(detail.history.filter((item) => item.role !== "tool"));
    setTools(
      detail.history.filter((item) => item.role === "tool").map((item, index) => ({
        key: `history-${index}`,
        name: item.name || "tool",
        arguments: item.args || {},
        status: "completed",
        output: item.content,
      })),
    );
    setActiveRun(undefined);
  }

  async function chooseFolder(forNewSession = false) {
    if (!client) return;
    let selectedPath: string | null = null;
    try {
      const result = await open({ directory: true, multiple: false, title: "Authorize a folder for Poppy" });
      selectedPath = typeof result === "string" ? result : null;
    } catch {
      selectedPath = window.prompt("Folder path") || null;
    }
    if (!selectedPath) return;
    setCreateSessionAfterGrant(forNewSession);
    setPendingFolder(selectedPath);
  }

  async function authorizeFolder(canWrite: boolean, canShell: boolean) {
    if (!client || !pendingFolder) return;
    try {
      const path = pendingFolder;
      await client.addGrant(path, canWrite, canShell);
      await refreshMeta();
      setPendingFolder(undefined);
      if (createSessionAfterGrant) {
        setCreateSessionAfterGrant(false);
        await createSession(path);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function createSession(workspaceRoot: string) {
    if (!client) return;
    const session = await client.createSession(workspaceRoot);
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
    setChoosingWorkspace(false);
    await selectSession(client, session.id);
  }

  async function newSession() {
    if (!client) return;
    if (activeRun) {
      setError("Stop the current task before switching conversations.");
      return;
    }
    if (!settings.api_key_configured) {
      setSettingsOpen(true);
      return;
    }
    if (!grants.length) {
      await chooseFolder(true);
      return;
    }
    if (grants.length > 1) {
      setChoosingWorkspace(true);
      return;
    }
    await createSession(grants[0].path);
  }

  async function chooseAttachments() {
    try {
      const result = await open({ directory: false, multiple: true, title: "Attach files for Poppy" });
      const paths = Array.isArray(result) ? result : result ? [result] : [];
      setAttachments((current) => [...new Set([...current, ...paths])]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function updateLatestTool(name: string, patch: Partial<ToolCall>) {
    setTools((current) => {
      const index = [...current].map((tool) => tool.name).lastIndexOf(name);
      if (index < 0) return current;
      return current.map((tool, itemIndex) => itemIndex === index ? { ...tool, ...patch } : tool);
    });
  }

  function handleEvent(event: RunEvent) {
    const payload = event.payload;
    if (event.event_type === "message.delta") {
      setMessages((current) => {
        const next = [...current];
        const last = next[next.length - 1];
        if (last?.role === "assistant") last.content += String(payload.delta || "");
        else next.push({ role: "assistant", content: String(payload.delta || "") });
        return next;
      });
    } else if (event.event_type === "message.completed") {
      setMessages((current) => {
        const next = [...current];
        const last = next[next.length - 1];
        if (last?.role === "assistant") last.content = String(payload.content || "");
        return next;
      });
    } else if (event.event_type === "tool.requested") {
      setTools((current) => [...current, {
        key: event.event_id,
        name: String(payload.tool_name),
        arguments: payload.arguments || {},
        status: "requested",
      }]);
    } else if (event.event_type === "tool.approval_required") {
      updateLatestTool(String(payload.tool_name), { status: "waiting", approvalId: String(payload.approval_id) });
      setApproval({ approvalId: String(payload.approval_id), toolName: String(payload.tool_name), arguments: payload.arguments || {} });
    } else if (event.event_type === "tool.started") {
      updateLatestTool(String(payload.tool_name), { status: "running" });
    } else if (event.event_type === "tool.completed" || event.event_type === "tool.failed") {
      updateLatestTool(String(payload.tool_name), {
        status: event.event_type === "tool.completed" ? "completed" : payload.status === "cancelled" ? "cancelled" : "failed",
        output: String(payload.output || ""),
        affectedPaths: payload.affected_paths || [],
        diffSummary: payload.diff_summary || [],
      });
    } else if (["run.completed", "run.cancelled", "run.failed"].includes(event.event_type)) {
      if (event.event_type === "run.failed") {
        setError(String(payload.error || "The task failed before Poppy could finish."));
      } else if (event.event_type === "run.completed") {
        setLastRequest(undefined);
      }
      setActiveRun(undefined);
      setApproval(undefined);
    }
  }

  async function runMessage(content: string, attached: string[]) {
    if (!client || !selectedId || activeRun) return;
    setError("");
    setLastRequest({ content, attachments: attached });
    setMessages((current) => [...current, { role: "user", content, attachments: attached }, { role: "assistant", content: "" }]);
    try {
      const run = await client.startRun(selectedId, content, attached);
      setActiveRun(run.run_id);
      disconnectRef.current = client.connectEvents(run.run_id, handleEvent, async () => {
        setActiveRun(undefined);
        try {
          const detail = await client.session(selectedId);
          setMessages(detail.history.filter((item) => item.role !== "tool"));
        } catch { /* keep streamed state */ }
      });
    } catch (reason) {
      setActiveRun(undefined);
      setError(reason instanceof Error ? reason.message : String(reason));
      setMessages((current) => current.slice(0, -2));
    }
  }

  async function sendMessage() {
    if (!input.trim()) return;
    const content = input.trim();
    const attached = [...attachments];
    setInput("");
    setAttachments([]);
    await runMessage(content, attached);
  }

  async function retryLastRequest() {
    if (!lastRequest || activeRun) return;
    await runMessage(lastRequest.content, lastRequest.attachments);
  }

  async function cancelActiveRun() {
    if (!client || !activeRun) return;
    try {
      await client.cancelRun(activeRun);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function resolveApproval(decision: "allow_once" | "allow_always" | "deny") {
    if (!client || !activeRun || !approval) return;
    try {
      await client.approve(activeRun, approval.approvalId, decision);
      setApproval(undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const canSend = !!client && !!selectedId && !!input.trim() && !activeRun;

  return (
    <div className="app-shell">
      {sidebarOpen && (
        <Sidebar
          sessions={sessions}
          grants={grants}
          selectedId={selectedId}
          busy={!!activeRun}
          onSelect={(id) => client && !activeRun && void selectSession(client, id)}
          onNew={() => void newSession()}
          onAddFolder={() => void chooseFolder()}
          onSettings={() => setSettingsOpen(true)}
          onRename={async (id, currentTitle) => {
            if (!client) return;
            const title = window.prompt("Rename conversation", currentTitle)?.trim();
            if (!title || title === currentTitle) return;
            await client.renameSession(id, title);
            await refreshMeta();
          }}
        />
      )}

      <main className="workspace">
        <header className="topbar">
          <button className="icon-button" onClick={() => setSidebarOpen(!sidebarOpen)}>
            {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
          </button>
          <div className="conversation-title">
            <strong>{selected?.title || "Poppy"}</strong>
            <span>{selected?.workspace_root || "Your private desktop assistant"}</span>
          </div>
          <button
            className="icon-button theme-toggle"
            onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Light theme" : "Dark theme"}
          >
            <Sun size={18} />
          </button>
          <div className={`connection-pill ${connected ? "online" : "offline"}`}>
            {connected ? <ShieldCheck size={14} /> : <WifiOff size={14} />}
            {connected ? "Local gateway" : "Disconnected"}
          </div>
        </header>

        <div className="conversation" ref={scrollRef}>
          {loading ? (
            <div className="center-state"><div className="pico-orb pulse">P</div><p>Starting Poppy…</p></div>
          ) : !connected ? (
            <div className="center-state error-state">
              <div className="pico-orb"><WifiOff size={25} /></div>
              <h2>Gateway unavailable</h2>
              <p>{error || "Poppy could not connect to its local runtime."}</p>
              <button className="secondary-button" onClick={() => void initialize()}>Try again</button>
            </div>
          ) : !settings.api_key_configured ? (
            <div className="center-state welcome-state">
              <div className="pico-orb">P</div>
              <h1>Connect DeepSeek</h1>
              <p>Your API key is stored in macOS Keychain and is never written to Poppy's database.</p>
              <button className="primary-action compact" onClick={() => setSettingsOpen(true)}>Open settings</button>
            </div>
          ) : !selectedId ? (
            <div className="center-state welcome-state">
              <div className="pico-orb">P</div>
              <h1>How can I help?</h1>
              <p>Poppy works only inside folders you explicitly authorize.</p>
              {!grants.length ? (
                <button className="primary-action compact" onClick={() => void chooseFolder()}><FolderOpen size={17} /> Authorize a folder</button>
              ) : (
                <button className="primary-action compact" onClick={() => void newSession()}>Start a conversation</button>
              )}
            </div>
          ) : (
            <div className="message-column">
              {messages.map((message, index) => (
                <article className={`message ${message.role}`} key={`${message.created_at || "message"}-${index}`}>
                  <div className="avatar">{message.role === "user" ? <UserRound size={17} /> : <Bot size={18} />}</div>
                  <div className="message-body">
                    <div className="message-role">{message.role === "user" ? "You" : "Poppy"}</div>
                    {message.role === "assistant" ? (
                      message.content ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : <div className="typing"><i /><i /><i /></div>
                    ) : <><p>{message.content}</p>{!!message.attachments?.length && <div className="message-attachments">{message.attachments.map((path) => <span key={path}><Paperclip size={12} />{path.split("/").pop()}</span>)}</div>}</>}
                  </div>
                </article>
              ))}
              {!!tools.length && <div className="tool-stack">{tools.map((tool) => <ToolCard key={tool.key} tool={tool} />)}</div>}
            </div>
          )}
        </div>

        {connected && selectedId && (
          <div className="composer-wrap">
            {error && <div className="inline-error"><span>{error}</span>{lastRequest && !activeRun && <button onClick={() => void retryLastRequest()}>Retry</button>}</div>}
            <div className="composer">
              <div className="composer-input">
                {!!attachments.length && <div className="composer-attachments">{attachments.map((path) => <span key={path}><Paperclip size={12} />{path.split("/").pop()}<button aria-label="Remove attachment" onClick={() => setAttachments((current) => current.filter((item) => item !== path))}><X size={12} /></button></span>)}</div>}
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); }
                  }}
                  placeholder="Ask Poppy to read, explain, or change something…"
                  rows={1}
                />
              </div>
              <button className="composer-tool" onClick={() => void chooseAttachments()} aria-label="Attach files"><Paperclip size={17} /></button>
              {activeRun ? (
                <button className="send-button stop" onClick={() => void cancelActiveRun()} aria-label="Stop"><Square size={15} fill="currentColor" /></button>
              ) : (
                <button className="send-button" disabled={!canSend} onClick={() => void sendMessage()} aria-label="Send"><ArrowUp size={19} /></button>
              )}
            </div>
            <span className="composer-hint">Poppy may make mistakes. Risky actions always require approval.</span>
          </div>
        )}
      </main>

      {approval && (
        <div className="approval-backdrop">
          <section className="approval-dialog">
            <div className="approval-icon"><ShieldCheck size={24} /></div>
            <h2>Allow this action?</h2>
            <p>Poppy wants to run <strong>{approval.toolName}</strong>.</p>
            <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
            <div className="approval-actions">
              <button className="ghost-button" onClick={() => void resolveApproval("deny")}>Deny</button>
              {approval.toolName !== "run_shell" && <button className="secondary-button" onClick={() => void resolveApproval("allow_always")}>Always allow this file</button>}
              <button className="primary-button" onClick={() => void resolveApproval("allow_once")}>Allow once</button>
            </div>
          </section>
        </div>
      )}

      {pendingFolder && (
        <div className="approval-backdrop">
          <section className="approval-dialog folder-dialog">
            <div className="approval-icon"><FolderOpen size={24} /></div>
            <h2>Authorize this folder?</h2>
            <p>Choose exactly what Poppy may do inside this folder.</p>
            <pre>{pendingFolder}</pre>
            <div className="folder-permission-actions">
              <button className="ghost-button" onClick={() => { setPendingFolder(undefined); setCreateSessionAfterGrant(false); }}>Cancel</button>
              <button className="secondary-button" onClick={() => void authorizeFolder(false, false)}>Read only</button>
              <button className="secondary-button" onClick={() => void authorizeFolder(true, false)}>Read &amp; write</button>
              <button className="primary-button" onClick={() => void authorizeFolder(true, true)}>Write + Shell</button>
            </div>
          </section>
        </div>
      )}

      {choosingWorkspace && (
        <div className="approval-backdrop">
          <section className="approval-dialog folder-dialog">
            <div className="approval-icon"><FolderOpen size={24} /></div>
            <h2>Choose a workspace</h2>
            <p>Each conversation is limited to one authorized folder.</p>
            <div className="workspace-choice-list">
              {grants.map((grant) => (
                <button className="workspace-choice" key={grant.id} onClick={() => void createSession(grant.path)}>
                  <strong>{grant.path.split("/").pop()}</strong>
                  <span>{grant.path}</span>
                </button>
              ))}
            </div>
            <div className="approval-actions"><button className="ghost-button" onClick={() => setChoosingWorkspace(false)}>Cancel</button></div>
          </section>
        </div>
      )}

      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          grants={grants}
          memories={memories}
          approvalRules={approvalRules}
          onClose={() => setSettingsOpen(false)}
          onSaveSettings={async (values) => { if (client) setSettings(await client.updateSettings(values)); }}
          onSaveApiKey={saveApiKey}
          onDeleteApiKey={deleteApiKey}
          onTestConnection={async () => {
            if (!client) throw new Error("Poppy local gateway is unavailable");
            return client.testConnection();
          }}
          onAddFolder={() => void chooseFolder()}
          onDeleteGrant={async (id) => { if (client) { await client.deleteGrant(id); await refreshMeta(); } }}
          onAddMemory={async (content) => { if (client) { await client.addMemory(content); await refreshMeta(); } }}
          onDeleteMemory={async (id) => { if (client) { await client.deleteMemory(id); await refreshMeta(); } }}
          onUpdateMemory={async (id, content) => { if (client) { await client.updateMemory(id, content); await refreshMeta(); } }}
          onDeleteApprovalRule={async (id) => { if (client) { await client.deleteApprovalRule(id); await refreshMeta(); } }}
        />
      )}
    </div>
  );
}

export default App;
