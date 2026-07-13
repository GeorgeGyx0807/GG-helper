export type SessionSummary = {
  id: string;
  title: string;
  workspace_root: string;
  session_type: "project" | "chat";
  created_at: string;
  updated_at: string;
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
