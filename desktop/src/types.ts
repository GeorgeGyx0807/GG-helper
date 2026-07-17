export type SessionSummary = {
  id: string;
  title: string;
  workspace_root: string;
  session_type: "project" | "chat";
  created_at: string;
  updated_at: string;
  locked_document_id?: string;
  locked_document?: {
    id: string;
    path: string;
    display_name: string;
  } | null;
};

export type HistoryItem = {
  role: "user" | "assistant" | "tool";
  content: string;
  created_at?: string;
  name?: string;
  args?: Record<string, unknown>;
  attachments?: string[];
};

export type SessionDetail = SessionSummary & { history: HistoryItem[] };

export type RunSnapshot = {
  run_id: string;
  session_id: string;
  status: "starting" | "running" | "completed" | "cancelled" | "failed";
  answer: string;
  error: string;
};

export type RunEvent = {
  event_id: string;
  event_type: string;
  run_id: string;
  session_id: string;
  sequence: number;
  created_at: string;
  payload: Record<string, any>;
};

export type ToolCall = {
  key: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "requested" | "waiting" | "running" | "completed" | "failed" | "cancelled";
  output?: string;
  approvalId?: string;
  affectedPaths?: string[];
  diffSummary?: string[];
};

export type Grant = {
  id: string;
  path: string;
  can_read: boolean;
  can_write: boolean;
  can_shell: boolean;
  created_at: string;
};

export type Settings = {
  model: string;
  base_url: string;
  timeout: number;
  max_steps?: number;
  max_new_tokens?: number;
  api_key_configured: boolean;
};

export type FeishuChannelSession = {
  id: string;
  channel: "feishu";
  tenant_key: string;
  chat_id: string;
  thread_id: string;
  sender_open_id: string;
  poppy_session_id: string;
  workspace_root: string;
  created_at: string;
  updated_at: string;
};

export type FeishuSettings = {
  feishu_enabled: boolean;
  feishu_app_id: string;
  feishu_secret_configured: boolean;
  feishu_allowed_users: string[];
  feishu_allowed_chats: string[];
  feishu_require_mention: boolean;
  feishu_cloud_enabled: boolean;
  feishu_workspace_root: string;
  feishu_max_file_mb: number;
  feishu_pairing_code: string;
  feishu_status: "disabled" | "not_configured" | "connecting" | "connected" | "reconnecting" | "error" | "stopped";
  feishu_error: string;
  feishu_connected_at: string;
  feishu_bot_name: string;
  feishu_bot_open_id: string;
  feishu_cloud_scope_ids: string[];
  feishu_cloud_permission_url: string;
  feishu_sessions: FeishuChannelSession[];
};

export type MemoryItem = {
  id: string;
  category: string;
  content: string;
  source_session_id: string;
  created_at: string;
  updated_at: string;
};

export type ApprovalRule = {
  id: string;
  tool_name: string;
  operation: string;
  path_scope: string;
  created_at: string;
};

export type LibrarySource = {
  id: string;
  path: string;
  kind: string;
  grant_id: string;
  enabled: boolean;
  last_indexed_at: string;
  document_count: number;
  index_status: "idle" | "indexing" | "error";
  index_progress: number;
  indexed_count: number;
  failed_count: number;
  last_error: string;
  failures?: IndexFailure[];
  created_at: string;
};

export type LibraryDocument = {
  id: string;
  source_id: string;
  path: string;
  display_name: string;
  mime_type: string;
  size: number;
  mtime_ns: number;
  updated_at: string;
  chunk_count: number;
};

export type IndexFailure = {
  source_id: string;
  path: string;
  error: string;
  updated_at: string;
};

export type LibrarySearchResult = {
  id: string;
  path: string;
  display_name: string;
  line_start: number;
  line_end: number;
  location?: Record<string, unknown>;
  content: string;
  rank?: number;
};

export type QuickIntent = "translate" | "explain" | "summarize" | "ask";

export type SelectionCapture = {
  text: string;
  source_app: string;
  bundle_id: string;
  window_title: string;
  capture_method: "accessibility" | "clipboard" | "manual";
  truncated: boolean;
  accessibility_trusted: boolean;
  error: string;
};

export type QuickContextResult = {
  context_id: string;
  mode: "document" | "selection";
  document: null | {
    display_name: string;
    page?: number | null;
    location?: Record<string, unknown>;
  };
  confidence: number;
  preview: string;
  truncated: boolean;
};

export type AuditEvent = {
  id: string;
  event_type: string;
  tool_name: string;
  session_id: string;
  run_id: string;
  scope: string;
  details_json: string;
  created_at: string;
};
