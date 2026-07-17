import { invoke } from "@tauri-apps/api/core";
import type {
  ApprovalRule,
  AuditEvent,
  FeishuSettings,
  Grant,
  LibraryDocument,
  LibrarySearchResult,
  LibrarySource,
  MemoryItem,
  QuickContextResult,
  QuickIntent,
  RunEvent,
  RunSnapshot,
  SessionDetail,
  SessionSummary,
  Settings,
} from "../types";

export type GatewayInfo = { base_url: string; token: string };

export async function resolveGatewayInfo(timeoutMs = 60_000): Promise<GatewayInfo> {
  const configuredUrl = import.meta.env.VITE_POPPY_GATEWAY_URL;
  if (configuredUrl) {
    return {
      base_url: configuredUrl,
      token: import.meta.env.VITE_POPPY_GATEWAY_TOKEN || "",
    };
  }

  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;
  do {
    try {
      return await invoke<GatewayInfo>("gateway_info");
    } catch (reason) {
      lastError = reason;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
  } while (Date.now() < deadline);
  throw lastError instanceof Error
    ? lastError
    : new Error(String(lastError || "Poppy local gateway did not start"));
}

export class GatewayClient {
  constructor(private info: GatewayInfo) {}

  async waitUntilHealthy(timeoutMs = 30_000) {
    const deadline = Date.now() + timeoutMs;
    let lastError: unknown;
    do {
      try {
        return await this.health();
      } catch (error) {
        lastError = error;
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
    } while (Date.now() < deadline);
    throw lastError instanceof Error ? lastError : new Error("Poppy local gateway did not start");
  }

  private async request<T>(path: string, init: RequestInit = {}, timeoutMs = 15_000): Promise<T> {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), Math.max(250, timeoutMs));
    try {
      const response = await fetch(`${this.info.base_url}${path}`, {
        ...init,
        signal: init.signal || controller.signal,
        headers: {
          "Content-Type": "application/json",
          "X-Poppy-Token": this.info.token,
          ...(init.headers || {}),
        },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(body.detail || `Gateway request failed (${response.status})`);
      }
      if (response.status === 204) return undefined as T;
      return response.json() as Promise<T>;
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") {
        throw new Error(`Poppy 本地服务请求超时（${path}）`);
      }
      throw reason;
    } finally {
      window.clearTimeout(timer);
    }
  }

  health(timeoutMs = 2_000) {
    return this.request<{ status: string }>("/health", {}, timeoutMs);
  }

  sessions(timeoutMs = 15_000) {
    return this.request<SessionSummary[]>("/sessions", {}, timeoutMs);
  }

  session(id: string) {
    return this.request<SessionDetail>(`/sessions/${id}`);
  }

  createSession(workspace_root?: string, title = "新对话", session_type: "project" | "chat" = "project") {
    return this.request<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify({ workspace_root: workspace_root || null, title, session_type }),
    });
  }

  renameSession(id: string, title: string) {
    return this.request<SessionSummary>(`/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  }

  deleteSession(id: string) {
    return this.request<void>(`/sessions/${id}`, { method: "DELETE" });
  }

  setDocumentLock(id: string, document_id = "") {
    return this.request<SessionSummary>(`/sessions/${id}/document-lock`, {
      method: "PATCH",
      body: JSON.stringify({ document_id }),
    });
  }

  startRun(
    session_id: string,
    message: string,
    attachments: string[] = [],
    quick?: { context_id?: string; intent?: QuickIntent; document_path?: string; full_document?: boolean },
  ) {
    return this.request<RunSnapshot>("/runs", {
      method: "POST",
      body: JSON.stringify({
        session_id,
        message,
        attachments,
        quick_context_id: quick?.context_id || "",
        quick_intent: quick?.intent || "ask",
        document_path: quick?.document_path || "",
        full_document: Boolean(quick?.full_document),
      }),
    });
  }

  resolveQuickContext(text: string, source_app = "", window_title = "") {
    return this.request<QuickContextResult>("/quick/context/resolve", {
      method: "POST",
      body: JSON.stringify({ text, source_app, window_title }),
    });
  }

  cancelRun(runId: string) {
    return this.request<RunSnapshot>(`/runs/${runId}/cancel`, { method: "POST" });
  }

  approve(runId: string, approvalId: string, decision: "allow_once" | "allow_always" | "deny") {
    return this.request(`/runs/${runId}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
  }

  connectEvents(runId: string, onEvent: (event: RunEvent) => void, onClose: () => void) {
    const wsBase = this.info.base_url.replace(/^http/, "ws");
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let stopped = false;
    let lastSequence = 0;

    const connect = () => {
      socket = new WebSocket(
        `${wsBase}/events?token=${encodeURIComponent(this.info.token)}&run_id=${encodeURIComponent(runId)}&after_sequence=${lastSequence}`,
      );
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as RunEvent;
        if (event.sequence <= lastSequence) return;
        lastSequence = event.sequence;
        onEvent(event);
      };
      socket.onclose = (event) => {
        if (stopped) return;
        if (event.code === 1000) {
          onClose();
          return;
        }
        retryTimer = window.setTimeout(connect, 350);
      };
    };

    connect();
    return () => {
      stopped = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }

  settings() {
    return this.request<Settings>("/settings");
  }

  updateSettings(values: Partial<Settings>) {
    return this.request<Settings>("/settings", {
      method: "PATCH",
      body: JSON.stringify(values),
    });
  }

  testConnection() {
    return this.request<{ status: string; model: string }>("/settings/test-connection", {
      method: "POST",
    });
  }

  feishuSettings() {
    return this.request<FeishuSettings>("/feishu/settings");
  }

  updateFeishuSettings(values: Partial<FeishuSettings>) {
    return this.request<FeishuSettings>("/feishu/settings", {
      method: "PATCH",
      body: JSON.stringify(values),
    });
  }

  restartFeishu() {
    return this.request<FeishuSettings>("/feishu/restart", { method: "POST" }, 30_000);
  }

  deleteFeishuSession(id: string) {
    return this.request<FeishuSettings>(`/feishu/sessions/${id}`, { method: "DELETE" });
  }

  grants() {
    return this.request<Grant[]>("/grants");
  }

  addGrant(path: string, can_write = false, can_shell = false) {
    return this.request<Grant>("/grants", {
      method: "POST",
      body: JSON.stringify({ path, can_read: true, can_write, can_shell }),
    });
  }

  deleteGrant(id: string) {
    return this.request<void>(`/grants/${id}`, { method: "DELETE" });
  }

  memories() {
    return this.request<MemoryItem[]>("/memories");
  }

  addMemory(content: string, category = "preference") {
    return this.request<MemoryItem>("/memories", {
      method: "POST",
      body: JSON.stringify({ content, category }),
    });
  }

  updateMemory(id: string, content: string) {
    return this.request<MemoryItem>(`/memories/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    });
  }

  deleteMemory(id: string) {
    return this.request<void>(`/memories/${id}`, { method: "DELETE" });
  }

  approvalRules() {
    return this.request<ApprovalRule[]>("/approval-rules");
  }

  deleteApprovalRule(id: string) {
    return this.request<void>(`/approval-rules/${id}`, { method: "DELETE" });
  }

  librarySources() {
    return this.request<LibrarySource[]>("/library/sources");
  }

  libraryDocuments(sessionId = "") {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return this.request<LibraryDocument[]>(`/library/documents${query}`);
  }

  addLibrarySource(path: string) {
    return this.request<LibrarySource>("/library/sources", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  }

  deleteLibrarySource(id: string) {
    return this.request<void>(`/library/sources/${id}`, { method: "DELETE" });
  }

  auditEvents(limit = 100) {
    return this.request<AuditEvent[]>(`/audit-events?limit=${limit}`);
  }

  reindexLibrary(sourceId = "") {
    const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : "";
    return this.request<{ source_id: string; path: string; documents: number }[]>(`/library/reindex${query}`, { method: "POST" });
  }

  searchLibrary(query: string, limit = 20) {
    return this.request<LibrarySearchResult[]>("/library/search", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    });
  }
}
