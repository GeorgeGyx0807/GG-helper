import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import {
  ArrowUp,
  BookOpenText,
  Copy,
  FilePlus2,
  Languages,
  LoaderCircle,
  MessageCircleQuestion,
  RefreshCw,
  Square,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import poppyMark from "../assets/poppy-mark.svg";
import { GatewayClient, resolveGatewayInfo, type GatewayInfo } from "../lib/gateway";
import type {
  QuickContextResult,
  QuickIntent,
  RunEvent,
  SelectionCapture,
  SessionSummary,
} from "../types";
import "./QuickWindow.css";

const EMPTY_CAPTURE: SelectionCapture = {
  text: "",
  source_app: "",
  bundle_id: "",
  window_title: "",
  capture_method: "manual",
  truncated: false,
  accessibility_trusted: false,
  error: "划选文献内容后按 ⌘⇧Space，或直接粘贴文字/拖入文件。",
};

const intentLabels: Record<QuickIntent, string> = {
  translate: "翻译",
  explain: "解释",
  summarize: "总结",
  ask: "结合全文问",
};

const intentQuestions: Record<QuickIntent, string> = {
  translate: "请翻译这段内容。",
  explain: "请解释这段内容的含义和关键概念。",
  summarize: "请总结这段内容的核心论点、方法和限制。",
  ask: "请结合全文说明这段内容。",
};

type ResizeDirection = "North" | "NorthEast" | "East" | "SouthEast" | "South" | "SouthWest" | "West" | "NorthWest";

const resizeHandles: { direction: ResizeDirection; edge: string }[] = [
  { direction: "North", edge: "north" },
  { direction: "NorthEast", edge: "north-east" },
  { direction: "East", edge: "east" },
  { direction: "SouthEast", edge: "south-east" },
  { direction: "South", edge: "south" },
  { direction: "SouthWest", edge: "south-west" },
  { direction: "West", edge: "west" },
  { direction: "NorthWest", edge: "north-west" },
];

function cleanAnswer(value: string) {
  const text = String(value || "");
  const final = text.match(/<final>([\s\S]*?)(?:<\/final>|$)/i);
  if (final) return final[1];
  if (/<tool\b/i.test(text)) return "";
  return text.replace(/<\/?final>/gi, "");
}

function contextLabel(context?: QuickContextResult) {
  if (!context) return "尚未匹配全文";
  if (context.mode !== "document" || !context.document) return "仅基于当前选区";
  const location = context.document.location || {};
  const confidence = `匹配 ${Math.round(context.confidence * 100)}%`;
  if (context.document.page) return `${context.document.display_name} · 第 ${context.document.page} 页 · ${confidence}`;
  if (location.kind === "spreadsheet") {
    return `${context.document.display_name} · ${String(location.sheet || "工作表")} · ${confidence}`;
  }
  return `${context.document.display_name} · ${confidence}`;
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));
}

export function QuickWindow() {
  const [client, setClient] = useState<GatewayClient>();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [capture, setCapture] = useState<SelectionCapture>(EMPTY_CAPTURE);
  const [selection, setSelection] = useState("");
  const [question, setQuestion] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [context, setContext] = useState<QuickContextResult>();
  const [resolving, setResolving] = useState(false);
  const [activeIntent, setActiveIntent] = useState<QuickIntent>("ask");
  const [activeRun, setActiveRun] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [connectionState, setConnectionState] = useState<"connecting" | "retrying" | "connected">("connecting");
  const [connectionError, setConnectionError] = useState("");
  const [gatewayHint, setGatewayHint] = useState<GatewayInfo>();
  const [reconnectVersion, setReconnectVersion] = useState(0);
  const disconnectRef = useRef<(() => void) | undefined>(undefined);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const selectionRevision = useRef(0);

  const resolveContext = useCallback(async (gateway: GatewayClient, current: SelectionCapture, text: string) => {
    const cleaned = text.trim();
    if (!cleaned) {
      setContext(undefined);
      return undefined;
    }
    const revision = ++selectionRevision.current;
    setResolving(true);
    try {
      const result = await gateway.resolveQuickContext(cleaned, current.source_app, current.window_title);
      if (revision === selectionRevision.current) setContext(result);
      return result;
    } finally {
      if (revision === selectionRevision.current) setResolving(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let attempt = 0;
      setConnected(false);
      setClient(undefined);
      while (!cancelled) {
        try {
          setConnectionState(attempt === 0 ? "connecting" : "retrying");
          const info = gatewayHint || await resolveGatewayInfo(5_000);
          const gateway = new GatewayClient(info);
          await gateway.waitUntilHealthy(5_000);
          const sessionList = await gateway.sessions(5_000);
          if (cancelled) return;
          setClient(gateway);
          setSessions(sessionList);
          setConnected(true);
          setConnectionState("connected");
          setConnectionError("");
          return;
        } catch (reason) {
          if (cancelled) return;
          attempt += 1;
          setConnectionState("retrying");
          const message = reason instanceof Error ? reason.message : String(reason);
          setConnectionError(`本地服务暂时不可用，正在自动重试（${message}）`);
          await delay(Math.min(3_000, 500 + attempt * 250));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [gatewayHint, reconnectVersion]);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void listen<GatewayInfo>("gateway-ready", ({ payload }) => {
      setGatewayHint(payload);
      setReconnectVersion((value) => value + 1);
    }).then((cleanup) => {
      if (cancelled) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => () => disconnectRef.current?.(), []);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void listen<SelectionCapture>("quick-capture", ({ payload }) => {
      selectionRevision.current += 1;
      setCapture(payload);
      setSelection(payload.text || "");
      setQuestion("");
      setAttachments([]);
      setContext(undefined);
      setAnswer("");
      setError(payload.error || "");
      setActiveRun("");
      disconnectRef.current?.();
      window.setTimeout(() => questionRef.current?.focus(), 20);
    }).then((cleanup) => {
      if (cancelled) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (!client || !selection.trim()) return;
    const timer = window.setTimeout(() => {
      void resolveContext(client, capture, selection).catch(() => setContext(undefined));
    }, 120);
    return () => window.clearTimeout(timer);
  }, [capture, client, resolveContext, selection]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        void invoke("hide_quick_window");
      }
      if (event.key === "Enter" && event.metaKey) {
        event.preventDefault();
        void submit(activeIntent);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void getCurrentWebview().onDragDropEvent(({ payload }) => {
      if (payload.type === "drop" && payload.paths.length) {
        setAttachments((current) => [...new Set([...current, ...payload.paths])]);
        setError("");
      }
    }).then((cleanup) => {
      if (cancelled) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  async function quickSession(gateway: GatewayClient) {
    const existing = sessions.find((session) => session.session_type === "chat" && session.title === "文献快问");
    if (existing) return existing.id;
    const created = await gateway.createSession(undefined, "文献快问", "chat");
    setSessions((current) => [created, ...current]);
    return created.id;
  }

  function handleEvent(event: RunEvent) {
    const payload = event.payload;
    if (event.event_type === "message.delta") {
      setAnswer((current) => cleanAnswer(current + String(payload.delta || "")));
    } else if (event.event_type === "message.completed") {
      setAnswer(cleanAnswer(String(payload.content || "")));
    } else if (event.event_type === "run.failed") {
      setError(String(payload.error || "Poppy 回答失败。"));
      setActiveRun("");
    } else if (event.event_type === "run.completed" || event.event_type === "run.cancelled") {
      setActiveRun("");
    }
  }

  async function submit(intent: QuickIntent, fullDocument = false) {
    if (!client || activeRun) return;
    if (!selection.trim() && !attachments.length) {
      setError("请先划选或粘贴一段文字，也可以把文献拖进小窗。 ");
      return;
    }
    setActiveIntent(intent);
    setActiveRun("pending");
    setError("");
    setAnswer("");
    try {
      let resolved = context;
      if (selection.trim() && (!resolved || resolved.truncated !== capture.truncated)) {
        resolved = await resolveContext(client, capture, selection);
      }
      const sessionId = await quickSession(client);
      const prompt = question.trim() || intentQuestions[intent];
      const run = await client.startRun(sessionId, prompt, attachments, {
        context_id: resolved?.context_id,
        intent,
        full_document: fullDocument,
      });
      setActiveRun(run.run_id);
      disconnectRef.current?.();
      disconnectRef.current = client.connectEvents(run.run_id, handleEvent, () => setActiveRun(""));
    } catch (reason) {
      setActiveRun("");
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      if (/failed to fetch|network|本地服务请求超时|gateway request failed/i.test(message)) {
        setConnected(false);
        setClient(undefined);
        setReconnectVersion((value) => value + 1);
      }
    }
  }

  async function submitDocumentQuestion() {
    if (context?.mode !== "document" && !attachments.length) {
      setError("尚未获取当前文件。请点击左侧的添加文献按钮选择该文件，或先在主窗口授权并索引它所在的文件夹。");
      return;
    }
    await submit("ask", true);
  }

  async function cancelActiveRun() {
    if (!client || !activeRun || activeRun === "pending") return;
    try {
      await client.cancelRun(activeRun);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function chooseFile() {
    try {
      const result = await open({ multiple: true, directory: false, title: "选择要询问的文献" });
      const paths = Array.isArray(result) ? result : result ? [result] : [];
      setAttachments((current) => [...new Set([...current, ...paths])]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function recapture() {
    try {
      const next = await invoke<SelectionCapture>("capture_selection");
      setCapture(next);
      setSelection(next.text);
      setContext(undefined);
      setError(next.error || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <main className="quick-shell">
      {resizeHandles.map(({ direction, edge }) => (
        <div
          aria-hidden="true"
          className={`quick-resize-handle ${edge}`}
          key={direction}
          onMouseDown={(event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            void getCurrentWindow().startResizeDragging(direction);
          }}
        />
      ))}
      <header
        className="quick-titlebar"
        data-tauri-drag-region
        onMouseDown={(event) => {
          if (event.button !== 0 || (event.target as HTMLElement).closest("button")) return;
          void getCurrentWindow().startDragging();
        }}
      >
        <div className="quick-brand" data-tauri-drag-region>
          <img src={poppyMark} alt="" />
          <div>
            <strong>Poppy 文献快问</strong>
            <span>{connectionState === "connected" ? "本地服务已连接" : connectionState === "retrying" ? "连接中断，正在重试…" : "正在连接…"}</span>
          </div>
        </div>
        <button className="quick-icon-button" title="关闭" onClick={() => void invoke("hide_quick_window")}><X size={16} /></button>
      </header>

      <section className="quick-content">
        <div className="quick-source-row">
          <div>
            <span>{capture.source_app || "手动输入"}{capture.window_title ? ` · ${capture.window_title}` : ""}</span>
            <strong className={context?.mode === "document" ? "matched" : ""}>
              {resolving ? "正在匹配授权文献…" : contextLabel(context)}
            </strong>
          </div>
          <button className="quick-icon-button" title="重新读取选区" onClick={() => void recapture()}><RefreshCw size={14} /></button>
        </div>

        {!capture.accessibility_trusted && (
          <div className="quick-permission">
            <span>开启辅助功能权限后，可自动读取其他应用的选中文字。</span>
            <button onClick={() => void invoke("request_accessibility_permission")}>开启权限</button>
          </div>
        )}

        <label className="quick-selection">
          <span>当前选区</span>
          <textarea
            value={selection}
            onChange={(event) => { setSelection(event.target.value); setContext(undefined); }}
            placeholder="划选后按 ⌘⇧Space，或在这里粘贴文字…"
          />
        </label>

        {attachments.length > 0 && (
          <div className="quick-attachments">
            {attachments.map((path) => (
              <span key={path}>{path.split(/[\\/]/).pop()}<button onClick={() => setAttachments((items) => items.filter((item) => item !== path))}><X size={12} /></button></span>
            ))}
          </div>
        )}

        <div className="quick-actions">
          <button onClick={() => void submit("translate")} disabled={!client || Boolean(activeRun)}><Languages size={15} />翻译</button>
          <button onClick={() => void submit("explain")} disabled={!client || Boolean(activeRun)}><Sparkles size={15} />解释</button>
          <button onClick={() => void submit("summarize")} disabled={!client || Boolean(activeRun)}><BookOpenText size={15} />总结</button>
          <button
            className="primary"
            title={context?.mode === "document" || attachments.length ? "检索当前文献的相关内容后回答" : "请先添加文献，或授权并索引文献所在文件夹"}
            onClick={() => void submitDocumentQuestion()}
            disabled={!client || Boolean(activeRun)}
          ><MessageCircleQuestion size={15} />全文问</button>
        </div>

        <div className="quick-question-row">
          <button className="quick-icon-button" title="添加文献" onClick={() => void chooseFile()}><FilePlus2 size={17} /></button>
          <textarea
            ref={questionRef}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="继续追问…"
            rows={1}
          />
          <button
            className={`quick-send${activeRun ? " stop" : ""}`}
            title={activeRun ? "停止回答" : "发送（⌘Enter）"}
            onClick={() => activeRun ? void cancelActiveRun() : void submit("ask")}
            disabled={!client || activeRun === "pending"}
          >
            {activeRun ? (activeRun === "pending" ? <LoaderCircle className="spin" size={16} /> : <Square size={13} fill="currentColor" />) : <ArrowUp size={16} />}
          </button>
        </div>

        {connectionError && !connected && (
          <div className="quick-error">
            <span>{connectionError}</span>
            <button onClick={() => setReconnectVersion((value) => value + 1)}>立即重试</button>
          </div>
        )}
        {error && <div className="quick-error">{error}</div>}

        {(answer || activeRun) && (
          <article className="quick-answer">
            <header><span>{intentLabels[activeIntent]}</span>{answer && <button onClick={() => void navigator.clipboard.writeText(answer)}><Copy size={13} />复制</button>}</header>
            {answer ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown> : <div className="quick-thinking"><i /><i /><i />Poppy 正在阅读…</div>}
          </article>
        )}
      </section>
    </main>
  );
}
