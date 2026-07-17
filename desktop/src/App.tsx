import { open } from "@tauri-apps/plugin-dialog";
import { openPath } from "@tauri-apps/plugin-opener";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import {
  ArrowUp,
  BookOpenText,
  BrainCircuit,
  Bot,
  ChevronDown,
  FolderOpen,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Pencil,
  Plus,
  Puzzle,
  ShieldCheck,
  Square,
  Sun,
  UserRound,
  WifiOff,
  X,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";
import poppyMark from "./assets/poppy-mark.svg";
import { SettingsPanel } from "./components/SettingsPanel";
import { Sidebar } from "./components/Sidebar";
import { GatewayClient, resolveGatewayInfo } from "./lib/gateway";
import type { GatewayInfo } from "./lib/gateway";
import type { ApprovalRule, AuditEvent, Citation, FeishuSettings, Grant, HistoryItem, IndexJob, KnowledgeSpace, LibraryDocument, LibrarySource, MemoryItem, RunEvent, SessionSummary, Settings, ToolCall } from "./types";

const PdfReader = lazy(() => import("./reader/PdfReader").then((module) => ({ default: module.PdfReader })));

type ApprovalPrompt = { approvalId: string; toolName: string; arguments: Record<string, unknown> };
type RetryRequest = { content: string; attachments: string[] };
type DraftType = "chat" | "project";

const modelOptions = [
  { value: "deepseek-v4-pro", label: "DeepSeek V4 Pro", note: "高质量", baseUrl: "https://api.deepseek.com/anthropic" },
  { value: "deepseek-v4-flash", label: "DeepSeek V4 Flash", note: "更快", baseUrl: "https://api.deepseek.com/anthropic" },
  { value: "qwen3.7-plus", label: "Qwen 3.7 Plus", note: "通用能力", baseUrl: "https://dashscope.aliyuncs.com/apps/anthropic" },
  { value: "qwen3.6-flash", label: "Qwen 3.6 Flash", note: "更快", baseUrl: "https://dashscope.aliyuncs.com/apps/anthropic" },
];

const defaultSettings: Settings = {
  model: "deepseek-v4-pro",
  base_url: "https://api.deepseek.com/anthropic",
  timeout: 300,
  api_key_configured: false,
};

const defaultFeishuSettings: FeishuSettings = {
  feishu_enabled: false,
  feishu_app_id: "",
  feishu_secret_configured: false,
  feishu_allowed_users: [],
  feishu_allowed_chats: [],
  feishu_require_mention: true,
  feishu_cloud_enabled: true,
  feishu_workspace_root: "",
  feishu_max_file_mb: 50,
  feishu_pairing_code: "",
  feishu_status: "disabled",
  feishu_error: "",
  feishu_connected_at: "",
  feishu_bot_name: "",
  feishu_bot_open_id: "",
  feishu_cloud_scope_ids: [],
  feishu_cloud_permission_url: "",
  feishu_sessions: [],
};

function modelSecretProvider(settings: Pick<Settings, "model" | "base_url">): "deepseek" | "dashscope" {
  return settings.model.toLowerCase().startsWith("qwen")
    || settings.base_url.includes("dashscope")
    || settings.base_url.includes("maas.aliyuncs.com")
    ? "dashscope"
    : "deepseek";
}

function sanitizeAssistantContent(value: string, streaming = false) {
  const text = String(value || "");
  // Protocol blocks belong to the tool cards, never to the conversation.
  // If a model narrates before emitting <tool>, discard the whole streamed
  // attempt; a later message.completed event will supply the real answer.
  if (/<tool\b/i.test(text) || /<\/tool>/i.test(text)) return "";
  const finalStart = text.indexOf("<final>");
  if (finalStart >= 0) {
    const body = text.slice(finalStart + "<final>".length);
    const finalEnd = body.indexOf("</final>");
    return finalEnd >= 0 ? body.slice(0, finalEnd) : streaming ? body : body.trim();
  }
  return text.replace(/<\/?final>/g, "");
}

function visibleHistory(history: HistoryItem[]) {
  return history
    .filter((item) => item.role !== "tool")
    .map((item) => item.role === "assistant"
      ? { ...item, content: sanitizeAssistantContent(item.content) }
      : item)
    .filter((item) => item.role !== "assistant" || Boolean(item.content.trim()));
}

function App() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const saved = window.localStorage.getItem("poppy-theme");
    if (saved === "dark" || saved === "light") return saved;
    return "light";
  });
  const [client, setClient] = useState<GatewayClient | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [activeWorkspace, setActiveWorkspace] = useState<string>();
  const [messages, setMessages] = useState<HistoryItem[]>([]);
  const [tools, setTools] = useState<ToolCall[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [feishuSettings, setFeishuSettings] = useState<FeishuSettings>(defaultFeishuSettings);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [approvalRules, setApprovalRules] = useState<ApprovalRule[]>([]);
  const [librarySources, setLibrarySources] = useState<LibrarySource[]>([]);
  const [libraryDocuments, setLibraryDocuments] = useState<LibraryDocument[]>([]);
  const [knowledgeSpaces, setKnowledgeSpaces] = useState<KnowledgeSpace[]>([]);
  const [indexJobs, setIndexJobs] = useState<IndexJob[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [activeRun, setActiveRun] = useState<string>();
  const [approval, setApproval] = useState<ApprovalPrompt>();
  const [pendingFolder, setPendingFolder] = useState<string>();
  const [createSessionAfterGrant, setCreateSessionAfterGrant] = useState(false);
  const [choosingWorkspace, setChoosingWorkspace] = useState(false);
  const [taskDraft, setTaskDraft] = useState(false);
  const [draftType, setDraftType] = useState<DraftType>("chat");
  const [composerMenuOpen, setComposerMenuOpen] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [renamingSession, setRenamingSession] = useState<{ id: string; title: string }>();
  const [renameValue, setRenameValue] = useState("");
  const [notebookCreatorOpen, setNotebookCreatorOpen] = useState(false);
  const [notebookName, setNotebookName] = useState("");
  const [notebookSaving, setNotebookSaving] = useState(false);
  const [notebookDocumentIds, setNotebookDocumentIds] = useState<string[]>([]);
  const [lastRequest, setLastRequest] = useState<RetryRequest>();
  const [readerPath, setReaderPath] = useState("");
  const [readerCitation, setReaderCitation] = useState<Citation>();
  const disconnectRef = useRef<(() => void) | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerMenuRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const selected = useMemo(() => sessions.find((session) => session.id === selectedId), [sessions, selectedId]);
  const activeProjectName = activeWorkspace?.split(/[\\/]/).filter(Boolean).pop() || "项目";
  const selectedScopeValue = selected?.knowledge_scope
    ? `${selected.knowledge_scope.kind}:${selected.knowledge_scope.id || ""}`
    : "auto:";

  useEffect(() => {
    void initialize();
    return () => disconnectRef.current?.();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    const openFirstPdf = (paths: string[]) => {
      const pdf = paths.find((path) => path.toLowerCase().endsWith(".pdf"));
      if (pdf) setReaderPath(pdf);
    };
    void invoke<string[]>("take_opened_pdf_paths")
      .then((paths) => { if (!cancelled) openFirstPdf(paths); })
      .catch(() => undefined);
    void listen<string[]>("open-pdf", ({ payload }) => openFirstPdf(payload)).then((cleanup) => {
      if (cancelled) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("poppy-theme", theme);
  }, [theme]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, tools]);

  useEffect(() => {
    if (!composerMenuOpen) return;
    const close = (event: MouseEvent) => {
      if (!composerMenuRef.current?.contains(event.target as Node)) {
        setComposerMenuOpen(false);
        setModelMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [composerMenuOpen]);

  useEffect(() => {
    if (!settingsOpen || !client) return;
    let stopped = false;
    const refresh = () => {
      void client.feishuSettings().then((next) => {
        if (!stopped) setFeishuSettings(next);
      }).catch(() => undefined);
      void client.librarySources().then((next) => {
        if (!stopped) setLibrarySources(next);
      }).catch(() => undefined);
      void client.indexJobs().then((next) => {
        if (!stopped) setIndexJobs(next);
      }).catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, 2_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [settingsOpen, client]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void listen("quick-capture", () => {
      if (activeRun) return;
      clearTaskDraft("chat");
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }).then((cleanup) => { if (cancelled) cleanup(); else unlisten = cleanup; });
    return () => { cancelled = true; unlisten?.(); };
  }, [activeRun]);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    const isInsideComposer = (position: { x: number; y: number }) => {
      const rect = composerRef.current?.getBoundingClientRect();
      if (!rect) return false;
      const scale = window.devicePixelRatio || 1;
      const x = position.x / scale;
      const y = position.y / scale;
      return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
    };
    void getCurrentWebview().onDragDropEvent(({ payload }) => {
      if (payload.type === "enter" || payload.type === "over") {
        setIsDraggingOver(Boolean(selectedId || taskDraft) && isInsideComposer(payload.position));
        return;
      }
      if (payload.type === "leave") {
        setIsDraggingOver(false);
        return;
      }
      setIsDraggingOver(false);
      if ((selectedId || taskDraft) && isInsideComposer(payload.position) && payload.paths.length) {
        setAttachments((current) => [...new Set([...current, ...payload.paths])]);
        return;
      }
      const pdf = payload.paths.find((path) => path.toLowerCase().endsWith(".pdf"));
      if (pdf) setReaderPath(pdf);
    }).then((cleanup) => { if (cancelled) cleanup(); else unlisten = cleanup; });
    return () => { cancelled = true; unlisten?.(); setIsDraggingOver(false); };
  }, [selectedId, taskDraft]);

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
      let snapshot: [SessionSummary[], Grant[], Settings, FeishuSettings, MemoryItem[], ApprovalRule[], LibrarySource[], AuditEvent[], KnowledgeSpace[], IndexJob[]] | undefined;
      let lastError: unknown;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        try {
          snapshot = await Promise.all([
            gateway.sessions(), gateway.grants(), gateway.settings(), gateway.feishuSettings(), gateway.memories(), gateway.approvalRules(), gateway.librarySources(), gateway.auditEvents(), gateway.knowledgeSpaces(), gateway.indexJobs(),
          ]);
          break;
        } catch (reason) {
          lastError = reason;
          await new Promise((resolve) => window.setTimeout(resolve, 350));
        }
      }
      if (!snapshot) throw lastError instanceof Error ? lastError : new Error("Poppy 无法加载本地数据");
      const [sessionList, grantList, appSettings, feishu, memoryList, ruleList, sourceList, auditList, spaces, jobs] = snapshot;
      disconnectRef.current?.();
      setClient(gateway);
      setConnected(true);
      setSessions(sessionList);
      setGrants(grantList);
      setSettings(appSettings);
      setFeishuSettings(feishu);
      setMemories(memoryList);
      setApprovalRules(ruleList);
      setLibrarySources(sourceList);
      setAuditEvents(auditList);
      setKnowledgeSpaces(spaces);
      setIndexJobs(jobs);
      if (sessionList[0]) await selectSession(gateway, sessionList[0].id);
      else if (grantList[0]) setActiveWorkspace(grantList[0].path);
  }

  async function saveApiKey(apiKey: string, provider: "deepseek" | "dashscope" | "feishu") {
    setLoading(true);
    let keyStored = false;
    try {
      const info = await invoke<GatewayInfo>("set_api_key", { apiKey, provider });
      keyStored = true;
      await connectGateway(info);
      setError("");
    } catch (reason) {
      const failure = keyStored
        ? new Error("密钥已保存，但 Poppy 无法重新连接，请重试或重启应用。")
        : reason instanceof Error ? reason : new Error(String(reason));
      setError(failure.message);
      throw failure;
    } finally {
      setLoading(false);
    }
  }

  async function deleteApiKey(provider: "deepseek" | "dashscope" | "feishu") {
    setLoading(true);
    try {
      const info = await invoke<GatewayInfo>("delete_api_key", { provider });
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
    const [sessionList, grantList, appSettings, feishu, memoryList, ruleList, sourceList, auditList, spaces, jobs] = await Promise.all([
      client.sessions(), client.grants(), client.settings(), client.feishuSettings(), client.memories(), client.approvalRules(), client.librarySources(), client.auditEvents(), client.knowledgeSpaces(), client.indexJobs(),
    ]);
    setSessions(sessionList);
    setGrants(grantList);
    setSettings(appSettings);
    setFeishuSettings(feishu);
    setMemories(memoryList);
    setApprovalRules(ruleList);
    setLibrarySources(sourceList);
    setAuditEvents(auditList);
    setKnowledgeSpaces(spaces);
    setIndexJobs(jobs);
  }

  async function selectSession(gateway: GatewayClient, id: string) {
    disconnectRef.current?.();
    const [detail, documents] = await Promise.all([
      gateway.session(id),
      gateway.libraryDocuments(id),
    ]);
    setSelectedId(id);
    setSessions((current) => current.map((session) => session.id === id ? detail : session));
    setTaskDraft(false);
    setActiveWorkspace(detail.session_type === "chat" ? undefined : detail.workspace_root);
    setMessages(visibleHistory(detail.history));
    setLibraryDocuments(documents);
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

  async function changeKnowledgeScope(value: string) {
    if (!client || !selectedId || activeRun) return;
    try {
      const separator = value.indexOf(":");
      const kind = separator >= 0 ? value.slice(0, separator) : value;
      const scopeId = separator >= 0 ? value.slice(separator + 1) : "";
      const updated = await client.setKnowledgeScope(selectedId, kind, scopeId);
      setSessions((current) => current.map((session) => session.id === selectedId ? updated : session));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function openNotebookCreator() {
    if (activeRun) return;
    setNotebookName("");
    const scopedDocumentId = selected?.knowledge_scope?.kind === "document"
      ? selected.knowledge_scope.id
      : "";
    setNotebookDocumentIds(
      scopedDocumentId ? [scopedDocumentId] : libraryDocuments.map((document) => document.id),
    );
    setNotebookCreatorOpen(true);
  }

  async function createNotebookFromVisibleDocuments() {
    if (!client) return;
    const name = notebookName.trim();
    if (!name || notebookSaving) return;
    if (!notebookDocumentIds.length) {
      setError("当前范围还没有已索引文档，暂时无法创建 Notebook。");
      setNotebookCreatorOpen(false);
      return;
    }
    setNotebookSaving(true);
    try {
      const created = await client.createKnowledgeSpace(name);
      const configured = await client.updateKnowledgeSpace(created.id, {
        document_ids: notebookDocumentIds,
      });
      setKnowledgeSpaces((current) => [...current, configured].sort((a, b) => a.name.localeCompare(b.name)));
      if (selectedId) await changeKnowledgeScope(`notebook:${created.id}`);
      setNotebookCreatorOpen(false);
      setNotebookName("");
      setNotebookDocumentIds([]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setNotebookSaving(false);
    }
  }

  async function openCitation(citation: Citation) {
    if (citation.path.toLowerCase().endsWith(".pdf")) {
      setReaderCitation(citation);
      setReaderPath(citation.path);
      return;
    }
    try {
      await openPath(citation.path);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function chooseFolder(forNewSession = false) {
    if (!client) return;
    let selectedPath: string | null = null;
    try {
      const result = await open({ directory: true, multiple: false, title: "选择要授权的文件夹" });
      selectedPath = typeof result === "string" ? result : null;
    } catch {
      selectedPath = window.prompt("输入文件夹路径") || null;
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
      setActiveWorkspace(path);
      await refreshMeta();
      setPendingFolder(undefined);
      if (createSessionAfterGrant || taskDraft) {
        setCreateSessionAfterGrant(false);
        beginProjectTask(path);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function clearTaskDraft(type: DraftType = "chat", workspace?: string) {
    disconnectRef.current?.();
    setSelectedId(undefined);
    setMessages([]);
    setTools([]);
    setLastRequest(undefined);
    setAttachments([]);
    setInput("");
    setActiveRun(undefined);
    setActiveWorkspace(workspace);
    setDraftType(type);
    setTaskDraft(true);
    setChoosingWorkspace(false);
  }

  function newChat() {
    if (activeRun) {
      setError("请先停止当前任务，再切换对话。");
      return;
    }
    clearTaskDraft("chat");
  }

  function beginProjectTask(path: string) {
    if (activeRun) return;
    clearTaskDraft("project", path);
  }

  async function createDraftSession(): Promise<string | undefined> {
    if (selectedId) return selectedId;
    if (!client) return;
    if (!settings.api_key_configured) {
      setSettingsOpen(true);
      return undefined;
    }
    try {
      if (draftType === "project" && !activeWorkspace) {
        setChoosingWorkspace(true);
        setError("请先选择一个项目文件夹。");
        return undefined;
      }
      const session = draftType === "project"
        ? await client.createSession(activeWorkspace, "新任务", "project")
        : await client.createSession(undefined, "新任务", "chat");
      setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
      setTaskDraft(false);
      await selectSession(client, session.id);
      return session.id;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return undefined;
    }
  }

  function newSession() {
    if (!client) return;
    if (activeRun) {
      setError("请先停止当前任务，再切换对话。");
      return;
    }
    if (!settings.api_key_configured) {
      setSettingsOpen(true);
      return;
    }
    if (!grants.length) {
      void chooseFolder(true);
      return;
    }
    const projectPath = activeWorkspace || selected?.workspace_root;
    if (projectPath && grants.some((grant) => grant.path === projectPath)) {
      beginProjectTask(projectPath);
      return;
    }
    if (grants.length > 1) {
      setChoosingWorkspace(true);
      return;
    }
    beginProjectTask(grants[0].path);
  }

  async function chooseAttachments(directory = false) {
    try {
      const result = await open({ directory, multiple: true, title: directory ? "选择要附加的文件夹" : "选择要附加的文件" });
      const paths = Array.isArray(result) ? result : result ? [result] : [];
      setAttachments((current) => [...new Set([...current, ...paths])]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function choosePdfReader() {
    try {
      const result = await open({
        directory: false,
        multiple: false,
        title: "在 Poppy 中阅读 PDF",
        filters: [{ name: "PDF 文献", extensions: ["pdf"] }],
      });
      if (typeof result === "string") setReaderPath(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function chooseLibrarySource() {
    if (!client) return;
    try {
      const result = await open({ directory: true, multiple: false, title: "选择要加入个人资料库的文件夹" });
      const path = typeof result === "string" ? result : null;
      if (!path) return;
      await client.addLibrarySource(path);
      await refreshMeta();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function chooseModel(model: string) {
    if (!client) return;
    try {
      const option = modelOptions.find((item) => item.value === model);
      const next = await client.updateSettings({ model, ...(option ? { base_url: option.baseUrl } : {}) });
      setSettings(next);
      const info = await invoke<GatewayInfo>("configure_secret_usage", {
        provider: modelSecretProvider(next),
        feishuEnabled: feishuSettings.feishu_enabled,
      });
      await connectGateway(info);
      setModelMenuOpen(false);
      setComposerMenuOpen(false);
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
        const delta = String(payload.delta || "");
        if (last?.role === "assistant") last.content = sanitizeAssistantContent(last.content + delta, true);
        else next.push({ role: "assistant", content: sanitizeAssistantContent(delta, true) });
        return next;
      });
    } else if (event.event_type === "message.completed") {
      setMessages((current) => {
        const next = [...current];
        const last = next[next.length - 1];
        if (last?.role === "assistant") last.content = sanitizeAssistantContent(String(payload.content || ""));
        return next;
      });
    } else if (event.event_type === "retrieval.citations") {
      const citations = Array.isArray(payload.citations) ? payload.citations as Citation[] : [];
      setMessages((current) => {
        const next = [...current];
        const last = next[next.length - 1];
        if (last?.role === "assistant") last.citations = citations;
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
        setError(String(payload.error || "任务在 Poppy 完成前失败了。"));
      } else if (event.event_type === "run.completed") {
        setLastRequest(undefined);
      }
      setActiveRun(undefined);
      setApproval(undefined);
    }
  }

  async function runMessage(content: string, attached: string[], targetSessionId?: string) {
    const sessionId = targetSessionId || selectedId;
    if (!client || !sessionId || activeRun) return;
    setError("");
    setLastRequest({ content, attachments: attached });
    setMessages((current) => [...current, { role: "user", content, attachments: attached }, { role: "assistant", content: "" }]);
    try {
      const run = await client.startRun(sessionId, content, attached);
      setActiveRun(run.run_id);
      disconnectRef.current = client.connectEvents(run.run_id, handleEvent, async () => {
        setActiveRun(undefined);
        setTaskDraft(false);
        try {
          const [detail, documents] = await Promise.all([
            client.session(sessionId),
            client.libraryDocuments(sessionId),
          ]);
          setMessages(visibleHistory(detail.history));
          setSessions((current) => current.map((session) => session.id === sessionId ? detail : session));
          setLibraryDocuments(documents);
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
    const sessionId = selectedId || (taskDraft ? await createDraftSession() : undefined);
    if (!sessionId) return;
    setInput("");
    setAttachments([]);
    await runMessage(content, attached, sessionId);
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

  async function deleteSession(id: string, title: string) {
    if (!client || activeRun) return;
    if (!window.confirm(`确定删除对话“${title}”？删除后无法恢复。`)) return;
    try {
      await client.deleteSession(id);
      const remaining = sessions.filter((session) => session.id !== id);
      setSessions(remaining);
      if (selectedId === id) {
        disconnectRef.current?.();
        setSelectedId(undefined);
        setMessages([]);
        setTools([]);
        setActiveRun(undefined);
        const nextInProject = remaining.find((session) => session.session_type !== "chat" && session.workspace_root === activeWorkspace);
        if (nextInProject) await selectSession(client, nextInProject.id);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function openProject(path: string) {
    if (!client || activeRun) return;
    setActiveWorkspace(path);
    const existing = sessions.find((session) => session.workspace_root === path);
    if (existing) {
      await selectSession(client, existing.id);
      return;
    }
    beginProjectTask(path);
  }

  function beginRename(id: string, title: string) {
    setRenamingSession({ id, title });
    setRenameValue(title);
  }

  async function submitRename() {
    if (!client || !renamingSession) return;
    const title = renameValue.trim();
    if (!title || title === renamingSession.title) {
      setRenamingSession(undefined);
      return;
    }
    try {
      await client.renameSession(renamingSession.id, title);
      setRenamingSession(undefined);
      await refreshMeta();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const canSend = !!client && !!input.trim() && !activeRun && (!!selectedId || taskDraft);

  return (
    <div className="app-shell">
      {sidebarOpen && (
        <Sidebar
          sessions={sessions}
          chatSessions={sessions.filter((session) => session.session_type === "chat")}
          grants={grants}
          selectedId={selectedId}
          selectedWorkspace={activeWorkspace}
          busy={!!activeRun}
          onSelect={(id) => client && !activeRun && void selectSession(client, id)}
          onNew={() => void newChat()}
          onAddFolder={() => void chooseFolder()}
          onOpenProject={(path) => void openProject(path)}
          onSettings={() => setSettingsOpen(true)}
          onRename={beginRename}
          onDelete={(id, title) => void deleteSession(id, title)}
        />
      )}

      <main className="workspace">
        <header className="topbar">
          <button className="icon-button" onClick={() => setSidebarOpen(!sidebarOpen)}>
            {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
          </button>
          <div className="conversation-title">
            <strong>{selected?.title || (activeWorkspace ? `${activeProjectName} 项目` : "Poppy")}</strong>
            <span>{selected?.session_type === "chat" ? "" : selected?.workspace_root || activeWorkspace || "你的桌面个人助手"}</span>
          </div>
          <button className="icon-button" onClick={() => void choosePdfReader()} aria-label="打开 PDF 阅读器" title="打开 PDF 阅读器">
            <BookOpenText size={18} />
          </button>
          <button
            className="icon-button theme-toggle"
            onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
            title={theme === "dark" ? "浅色模式" : "深色模式"}
          >
            <Sun size={18} />
          </button>
          <div className={`connection-pill ${connected ? "online" : "offline"}`}>
            {connected ? <ShieldCheck size={14} /> : <WifiOff size={14} />}
            {connected ? "已连接" : "未连接"}
          </div>
        </header>

        <div className="conversation" ref={scrollRef}>
          {loading ? (
            <div className="center-state"><div className="poppy-orb pulse"><img src={poppyMark} alt="Poppy" /></div><p>正在启动 Poppy…</p></div>
          ) : !connected ? (
            <div className="center-state error-state">
              <div className="poppy-orb"><WifiOff size={25} /></div>
              <h2>助手暂时不可用</h2>
              <p>{error || "Poppy 无法连接本地运行服务。"}</p>
              <button className="secondary-button" onClick={() => void initialize()}>重试</button>
            </div>
          ) : !settings.api_key_configured ? (
            <div className="center-state welcome-state">
              <div className="poppy-orb"><img src={poppyMark} alt="Poppy" /></div>
              <h1>连接 DeepSeek</h1>
              <p>请在设置中填写 DeepSeek API 密钥。密钥只保存在 macOS 钥匙串中。</p>
              <button className="primary-action compact" onClick={() => setSettingsOpen(true)}>打开设置</button>
            </div>
          ) : !selectedId ? (
            taskDraft ? (
              <div className="center-state task-state">
                <div className="poppy-orb"><img src={poppyMark} alt="Poppy" /></div>
                <h1>我们开始做什么？</h1>
                <p>{draftType === "project" && activeWorkspace ? `将在“${activeProjectName}”项目中开始任务` : "描述你想完成的事情，发送后才会创建任务。"}</p>
                <div className="task-suggestions">
                  {[
                    ["探索并理解内容", "帮我梳理这份资料的重点"],
                    ["构建新功能", "帮我设计并实现一个功能"],
                    ["审查并提出建议", "请检查这段内容并给出改进建议"],
                    ["解决问题和失败", "帮我定位这个问题的原因"],
                  ].map(([title, prompt]) => (
                    <button key={title} className="task-suggestion" onClick={() => setInput(prompt)}>
                      <BrainCircuit size={16} /> <span>{title}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : <div className="center-state welcome-state">
              <div className="poppy-orb"><img src={poppyMark} alt="Poppy" /></div>
              <h1>{activeWorkspace ? `在“${activeProjectName}”项目中开始工作` : "今天想做什么？"}</h1>
              <p>{activeWorkspace ? "新建对话后，Poppy 只会在这个项目文件夹中工作。" : "Poppy 只会访问你明确授权的文件夹。"}</p>
              {!grants.length ? (
                <button className="primary-action compact" onClick={() => void chooseFolder()}><FolderOpen size={17} /> 授权一个文件夹</button>
              ) : (
                <button className="primary-action compact" onClick={newSession}>开始新任务</button>
              )}
            </div>
          ) : (
            <div className="message-column">
              {messages.map((message, index) => (
                <article className={`message ${message.role}`} key={`${message.created_at || "message"}-${index}`}>
                  <div className="avatar">{message.role === "user" ? <UserRound size={17} /> : <Bot size={18} />}</div>
                  <div className="message-body">
                    <div className="message-role">{message.role === "user" ? "你" : "Poppy"}</div>
                    {message.role === "assistant" ? (
                      message.content ? <>
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                        {!!message.citations?.length && <div className="answer-citations">
                          {message.citations.map((citation) => (
                            <button key={`${citation.chunk_id}-${citation.label}`} onClick={() => void openCitation(citation)} title={citation.quote}>
                              <BookOpenText size={13} />
                              <span>{citation.label} {citation.title}</span>
                              <small>{citation.location.page ? `第 ${citation.location.page} 页` : citation.location.sheet ? `${citation.location.sheet}` : `行 ${citation.location.line_start || "?"}-${citation.location.line_end || "?"}`}</small>
                            </button>
                          ))}
                        </div>}
                      </> : <div className="typing"><i /><i /><i /></div>
                    ) : <><p>{message.content}</p>{!!message.attachments?.length && <div className="message-attachments">{message.attachments.map((path) => (
                      path.toLowerCase().endsWith(".pdf")
                        ? <button key={path} onClick={() => setReaderPath(path)} title="在内置阅读器中打开"><BookOpenText size={12} />{path.split("/").pop()}</button>
                        : <span key={path}><Paperclip size={12} />{path.split("/").pop()}</span>
                    ))}</div>}</>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        {connected && (selectedId || taskDraft) && (
          <div className="composer-wrap">
            {error && <div className="inline-error"><span>{error}</span>{lastRequest && !activeRun && <button onClick={() => void retryLastRequest()}>重试</button>}</div>}
            {selectedId && <div className={`document-lock-bar ${selected?.knowledge_scope?.kind === "document" ? "locked" : ""}`}>
              <BookOpenText size={14} />
              <span>知识范围</span>
              <select
                value={selectedScopeValue}
                onChange={(event) => void changeKnowledgeScope(event.target.value)}
                disabled={Boolean(activeRun)}
                title="选择本轮回答允许检索的知识范围"
              >
                <option value="all:">全部知识库</option>
                {selected?.session_type === "project" && <option value="project:">当前项目 · {activeProjectName}</option>}
                {knowledgeSpaces.map((space) => (
                  <option value={`notebook:${space.id}`} key={space.id}>Notebook · {space.name}</option>
                ))}
                {libraryDocuments.map((document) => (
                  <option value={`document:${document.id}`} key={document.id}>单文档 · {document.display_name}</option>
                ))}
              </select>
              <button className="scope-add-button" onClick={openNotebookCreator} disabled={Boolean(activeRun)} title="把当前范围内的文档保存为一个专题 Notebook"><Plus size={13} /> Notebook</button>
              <small>本轮：{selected?.knowledge_scope?.label || "自动"}；写出完整文件名会切到对应文档</small>
            </div>}
            {taskDraft && <div className="draft-context-bar">
              <button className="draft-context-button" onClick={() => grants.length ? setChoosingWorkspace(true) : void chooseFolder(false)}>
                <FolderOpen size={14} /> {activeWorkspace ? activeProjectName : "选择项目"}<ChevronDown size={13} />
              </button>
              <span>{draftType === "project" ? "项目任务" : "普通任务"}</span>
            </div>}
            <div className="composer" ref={composerRef}>
              <div className="composer-menu-wrap" ref={composerMenuRef}>
                <button className="composer-plus" onClick={() => { setComposerMenuOpen((open) => !open); setModelMenuOpen(false); }} aria-label="添加"><Plus size={18} /></button>
                {composerMenuOpen && <div className="composer-menu">
                  <div className="composer-menu-header"><span className="composer-menu-title">添加</span><button className="composer-menu-close" onClick={() => { setComposerMenuOpen(false); setModelMenuOpen(false); }} aria-label="收起菜单"><ChevronDown size={16} /></button></div>
                  <button onClick={() => { setComposerMenuOpen(false); void chooseAttachments(false); }}><Paperclip size={15} />选择文件</button>
                  <button onClick={() => { setComposerMenuOpen(false); void chooseAttachments(true); }}><FolderOpen size={15} />选择文件夹</button>
                  <button onClick={() => { setComposerMenuOpen(false); void choosePdfReader(); }}><BookOpenText size={15} />打开 PDF 阅读器</button>
                  <button onClick={() => { setComposerMenuOpen(false); setError("插件入口已预留，插件中心将在后续版本接入。"); }}><Puzzle size={15} />插件</button>
                  <button onClick={() => setModelMenuOpen((open) => !open)}><BrainCircuit size={15} />选择模型 <ChevronDown size={13} /></button>
                  {modelMenuOpen && <div className="model-menu">
                    <div className="composer-menu-title">模型</div>
                    {modelOptions.map((option) => <button key={option.value} className={settings.model === option.value ? "selected" : ""} onClick={() => void chooseModel(option.value)}><span>{option.label}</span><small>{option.note}</small></button>)}
                  </div>}
                </div>}
              </div>
              <div
                className={`composer-input ${isDraggingOver ? "dragging" : ""}`}
                onDragEnter={(event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setIsDraggingOver(true); }}
                onDragOver={(event: DragEvent<HTMLDivElement>) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setIsDraggingOver(true); }}
                onDragLeave={(event: DragEvent<HTMLDivElement>) => { if (event.currentTarget === event.target) setIsDraggingOver(false); }}
                onDrop={(event: DragEvent<HTMLDivElement>) => {
                  event.preventDefault();
                  setIsDraggingOver(false);
                  const paths = Array.from(event.dataTransfer.files)
                    .map((file) => (file as File & { path?: string }).path || "")
                    .filter(Boolean);
                  if (!paths.length) {
                    setError("没有读取到拖入文件的路径，请从 Finder 或桌面直接拖入。");
                    return;
                  }
                  setAttachments((current) => [...new Set([...current, ...paths])]);
                }}
              >
                {!!attachments.length && <div className="composer-attachments">{attachments.map((path) => <span key={path}><Paperclip size={12} />{path.split("/").pop()}<button aria-label="移除附件" onClick={() => setAttachments((current) => current.filter((item) => item !== path))}><X size={12} /></button></span>)}</div>}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    // 中文输入法确认候选字时也会触发 Enter；组合输入期间不能提交消息。
                    // keyCode 229 是 Safari/macOS 输入法在 isComposing 不稳定时的兜底标记。
                    const composing = event.nativeEvent.isComposing || event.keyCode === 229;
                    if (event.key === "Enter" && !event.shiftKey && !composing) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  placeholder="输入消息…"
                  rows={1}
                />
              </div>
              <button className="composer-model-label" onClick={() => { setComposerMenuOpen(true); setModelMenuOpen(true); }} aria-label="选择模型">
                {modelOptions.find((option) => option.value === settings.model)?.label || settings.model}
                <ChevronDown size={13} />
              </button>
              {activeRun ? (
                <button className="send-button stop" onClick={() => void cancelActiveRun()} aria-label="停止生成"><Square size={15} fill="currentColor" /></button>
              ) : (
                <button className="send-button" disabled={!canSend} onClick={() => void sendMessage()} aria-label="发送"><ArrowUp size={19} /></button>
              )}
            </div>
          </div>
        )}
      </main>

      {approval && (
        <div className="approval-backdrop">
          <section className="approval-dialog">
            <div className="approval-icon"><ShieldCheck size={24} /></div>
            <h2>允许执行这个操作吗？</h2>
            <p>Poppy 请求运行 <strong>{approval.toolName}</strong>。</p>
            <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
            <div className="approval-actions">
              <button className="ghost-button" onClick={() => void resolveApproval("deny")}>拒绝</button>
              {approval.toolName !== "run_shell" && <button className="secondary-button" onClick={() => void resolveApproval("allow_always")}>始终允许此范围</button>}
              <button className="primary-button" onClick={() => void resolveApproval("allow_once")}>本次允许</button>
            </div>
          </section>
        </div>
      )}

      {pendingFolder && (
        <div className="approval-backdrop">
          <section className="approval-dialog folder-dialog">
            <div className="approval-icon"><FolderOpen size={24} /></div>
            <h2>授权这个文件夹？</h2>
            <p>请选择 Poppy 在这个文件夹中可以执行的操作。</p>
            <pre>{pendingFolder}</pre>
            <div className="folder-permission-actions">
              <button className="ghost-button" onClick={() => { setPendingFolder(undefined); setCreateSessionAfterGrant(false); }}>取消</button>
              <button className="secondary-button" onClick={() => void authorizeFolder(false, false)}>仅阅读</button>
              <button className="secondary-button" onClick={() => void authorizeFolder(true, false)}>读写</button>
              <button className="primary-button" onClick={() => void authorizeFolder(true, true)}>读写 + 终端</button>
            </div>
          </section>
        </div>
      )}

      {choosingWorkspace && (
        <div className="approval-backdrop">
          <section className="approval-dialog folder-dialog">
            <div className="approval-icon"><FolderOpen size={24} /></div>
            <h2>选择项目</h2>
            <p>新对话会在你选择的项目文件夹中工作。</p>
            <div className="workspace-choice-list">
              {grants.map((grant) => (
                <button className="workspace-choice" key={grant.id} onClick={() => beginProjectTask(grant.path)}>
                  <strong>{grant.path.split("/").pop()}</strong>
                  <span>{grant.path}</span>
                </button>
              ))}
            </div>
            <div className="approval-actions"><button className="ghost-button" onClick={() => setChoosingWorkspace(false)}>取消</button></div>
          </section>
        </div>
      )}

      {renamingSession && (
        <div className="approval-backdrop" onMouseDown={() => setRenamingSession(undefined)}>
          <section className="approval-dialog rename-dialog" onMouseDown={(event) => event.stopPropagation()}>
            <div className="approval-icon"><Pencil size={22} /></div>
            <h2>重命名对话</h2>
            <p>给这条对话设置一个容易识别的名称。</p>
            <input
              className="rename-input"
              value={renameValue}
              autoFocus
              onChange={(event) => setRenameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); void submitRename(); }
                if (event.key === "Escape") setRenamingSession(undefined);
              }}
            />
            <div className="approval-actions">
              <button className="ghost-button" onClick={() => setRenamingSession(undefined)}>取消</button>
              <button className="primary-button" disabled={!renameValue.trim()} onClick={() => void submitRename()}>保存</button>
            </div>
          </section>
        </div>
      )}

      {notebookCreatorOpen && (
        <div className="approval-backdrop" onMouseDown={() => !notebookSaving && setNotebookCreatorOpen(false)}>
          <section className="approval-dialog notebook-dialog" onMouseDown={(event) => event.stopPropagation()}>
            <div className="approval-icon"><BookOpenText size={22} /></div>
            <h2>创建 Notebook</h2>
            <p>
              Notebook 是一组固定的专题资料。创建后，在聊天中选择它，Poppy 只会从其中的文档取证。
            </p>
            <div className="notebook-source-summary">
              <strong>已选择 {notebookDocumentIds.length} / {libraryDocuments.length} 篇文档</strong>
              <span>{selected?.knowledge_scope?.label || (selected?.session_type === "project" ? activeProjectName : "当前知识范围")}</span>
            </div>
            <div className="notebook-document-actions">
              <button onClick={() => setNotebookDocumentIds(libraryDocuments.map((document) => document.id))}>全选</button>
              <button onClick={() => setNotebookDocumentIds([])}>清空</button>
            </div>
            <div className="notebook-document-list">
              {libraryDocuments.map((document) => (
                <label key={document.id}>
                  <input
                    type="checkbox"
                    checked={notebookDocumentIds.includes(document.id)}
                    onChange={(event) => setNotebookDocumentIds((current) => event.target.checked
                      ? [...new Set([...current, document.id])]
                      : current.filter((id) => id !== document.id))}
                  />
                  <span>{document.display_name}</span>
                </label>
              ))}
            </div>
            <input
              className="rename-input"
              value={notebookName}
              autoFocus
              placeholder="例如：Agent 记忆论文"
              onChange={(event) => setNotebookName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && notebookName.trim()) {
                  event.preventDefault();
                  void createNotebookFromVisibleDocuments();
                }
                if (event.key === "Escape" && !notebookSaving) setNotebookCreatorOpen(false);
              }}
            />
            {!libraryDocuments.length && <div className="inline-error">当前范围还没有已完成索引的文档。</div>}
            <div className="approval-actions">
              <button className="ghost-button" disabled={notebookSaving} onClick={() => setNotebookCreatorOpen(false)}>取消</button>
              <button className="primary-button" disabled={!notebookName.trim() || !notebookDocumentIds.length || notebookSaving} onClick={() => void createNotebookFromVisibleDocuments()}>
                {notebookSaving ? "创建中…" : "创建并切换"}
              </button>
            </div>
          </section>
        </div>
      )}

      {readerPath && client && (
        <Suspense fallback={<div className="reader-loading-overlay">正在加载 PDF 阅读器…</div>}>
          <PdfReader
            path={readerPath}
            client={client}
            initialCitation={readerCitation?.path === readerPath ? readerCitation : undefined}
            onClose={() => { setReaderPath(""); setReaderCitation(undefined); }}
            onSessionCreated={() => void refreshMeta()}
          />
        </Suspense>
      )}

      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          feishu={feishuSettings}
          grants={grants}
          memories={memories}
          approvalRules={approvalRules}
          librarySources={librarySources}
          knowledgeSpaces={knowledgeSpaces}
          indexJobs={indexJobs}
          auditEvents={auditEvents}
          onClose={() => setSettingsOpen(false)}
          onSaveSettings={async (values) => {
            if (!client) return;
            const next = await client.updateSettings(values);
            setSettings(next);
            const info = await invoke<GatewayInfo>("configure_secret_usage", {
              provider: modelSecretProvider(next),
              feishuEnabled: feishuSettings.feishu_enabled,
            });
            await connectGateway(info);
          }}
          onSaveApiKey={saveApiKey}
          onDeleteApiKey={deleteApiKey}
          onSaveFeishuSettings={async (values) => {
            if (!client) throw new Error("Poppy 本地服务不可用");
            const next = await client.updateFeishuSettings(values);
            setFeishuSettings(next);
            const info = await invoke<GatewayInfo>("configure_secret_usage", {
              provider: modelSecretProvider(settings),
              feishuEnabled: next.feishu_enabled,
            });
            await connectGateway(info);
          }}
          onSaveFeishuSecret={(secret) => saveApiKey(secret, "feishu")}
          onDeleteFeishuSecret={() => deleteApiKey("feishu")}
          onRestartFeishu={async () => {
            if (!client) throw new Error("Poppy 本地服务不可用");
            setFeishuSettings(await client.restartFeishu());
          }}
          onDeleteFeishuSession={async (id) => {
            if (!client) throw new Error("Poppy 本地服务不可用");
            setFeishuSettings(await client.deleteFeishuSession(id));
          }}
          onTestConnection={async () => {
            if (!client) throw new Error("Poppy 本地服务不可用");
            return client.testConnection();
          }}
          onAddFolder={() => void chooseFolder()}
          onDeleteGrant={async (id) => { if (client) { await client.deleteGrant(id); await refreshMeta(); } }}
          onAddMemory={async (content) => { if (client) { await client.addMemory(content); await refreshMeta(); } }}
          onDeleteMemory={async (id) => { if (client) { await client.deleteMemory(id); await refreshMeta(); } }}
          onUpdateMemory={async (id, content) => { if (client) { await client.updateMemory(id, content); await refreshMeta(); } }}
          onDeleteApprovalRule={async (id) => { if (client) { await client.deleteApprovalRule(id); await refreshMeta(); } }}
          onAddLibrarySource={() => void chooseLibrarySource()}
          onDeleteLibrarySource={async (id) => { if (client) { await client.deleteLibrarySource(id); await refreshMeta(); } }}
          onReindexLibrary={async (id) => { if (client) { await client.reindexLibrary(id); await refreshMeta(); } }}
          onCreateKnowledgeSpace={async () => { setSettingsOpen(false); openNotebookCreator(); }}
          onDeleteKnowledgeSpace={async (id) => { if (client) { await client.deleteKnowledgeSpace(id); await refreshMeta(); } }}
        />
      )}
    </div>
  );
}

export default App;
