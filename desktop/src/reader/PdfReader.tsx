import { invoke } from "@tauri-apps/api/core";
import {
  ArrowLeft,
  ArrowRight,
  BookmarkPlus,
  Copy,
  Languages,
  LoaderCircle,
  MessageCircleQuestion,
  Minus,
  Plus,
  Send,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import {
  getDocument,
  GlobalWorkerOptions,
  Util,
  type PDFDocumentProxy,
} from "pdfjs-dist/legacy/build/pdf.js";
import workerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.js?url";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { GatewayClient } from "../lib/gateway";
import type { Citation, RunEvent } from "../types";
import "./PdfReader.css";

GlobalWorkerOptions.workerSrc = workerUrl;

type ReaderMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  page?: number;
  citations?: Citation[];
};

type ReaderSelection = {
  text: string;
  page: number;
};

type FloatingSelection = ReaderSelection & {
  x: number;
  y: number;
};

type SavedExcerpt = ReaderSelection & {
  id: string;
  createdAt: string;
};

type Props = {
  path: string;
  client: GatewayClient;
  onClose: () => void;
  onSessionCreated?: () => void;
  initialCitation?: Citation;
};

function cleanAnswer(value: string) {
  const text = String(value || "");
  const final = text.match(/<final>([\s\S]*?)(?:<\/final>|$)/i);
  if (final) return final[1].trim();
  if (/<tool\b/i.test(text)) return "";
  return text.replace(/<\/?final>/gi, "").trim();
}

function rawBytes(value: ArrayBuffer | Uint8Array | number[]) {
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (value instanceof Uint8Array) return value;
  return Uint8Array.from(value);
}

function citedPages(content: string, maximum: number) {
  const pages = Array.from(
    content.matchAll(/第\s*(\d+)\s*页/g),
    (match) => Number(match[1]),
  ).filter((page) => page >= 1 && page <= maximum);
  return [...new Set(pages)];
}

function excerptStorageKey(path: string) {
  return `poppy-pdf-excerpts:${path}`;
}

type PageViewport = ReturnType<
  Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["getViewport"]
>;

type PageTextContent = Awaited<
  ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["getTextContent"]>
>;

function renderSelectableTextLayer(
  container: HTMLDivElement,
  viewport: PageViewport,
  textContent: PageTextContent,
) {
  const measuringCanvas = document.createElement("canvas");
  const context = measuringCanvas.getContext("2d");

  for (const item of textContent.items) {
    if (!("str" in item) || !item.str) continue;
    const style = textContent.styles[item.fontName];
    const transform = Util.transform(viewport.transform, item.transform);
    let angle = Math.atan2(transform[1], transform[0]);
    if (style?.vertical) angle += Math.PI / 2;
    const fontHeight = Math.hypot(transform[2], transform[3]);
    const ascent = style?.ascent || (style?.descent ? 1 + style.descent : .8);
    const left = transform[4];
    const top = transform[5] - fontHeight * ascent;
    const fontFamily = style?.fontFamily || "sans-serif";
    let scaleX = 1;

    if (context && item.str.length > 1) {
      context.font = `${fontHeight}px ${fontFamily}`;
      const measuredWidth = context.measureText(item.str).width;
      const targetWidth = Math.abs(item.width * viewport.scale);
      if (measuredWidth > 0 && targetWidth > 0) scaleX = targetWidth / measuredWidth;
    }

    const span = document.createElement("span");
    span.textContent = item.str;
    span.dir = item.dir;
    span.style.left = `${left}px`;
    span.style.top = `${top}px`;
    span.style.fontFamily = fontFamily;
    span.style.fontSize = `${fontHeight}px`;
    span.style.transform = `rotate(${angle}rad) scaleX(${scaleX})`;
    container.append(span);

    if (item.hasEOL) {
      const br = document.createElement("br");
      br.style.left = `${left + Math.abs(item.width * viewport.scale)}px`;
      br.style.top = `${top}px`;
      container.append(br);
    }
  }
}

export function PdfReader({ path, client, onClose, onSessionCreated, initialCitation }: Props) {
  const fileName = useMemo(() => path.split(/[\\/]/).filter(Boolean).pop() || "文献.pdf", [path]);
  const [document, setDocument] = useState<PDFDocumentProxy>();
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.15);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState("");
  const [pageText, setPageText] = useState("");
  const [floatingSelection, setFloatingSelection] = useState<FloatingSelection>();
  const [questionSelection, setQuestionSelection] = useState<ReaderSelection>();
  const [messages, setMessages] = useState<ReaderMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [activeRun, setActiveRun] = useState("");
  const [fullDocumentMode, setFullDocumentMode] = useState(false);
  const [runProgress, setRunProgress] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [excerpts, setExcerpts] = useState<SavedExcerpt[]>([]);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const pageStageRef = useRef<HTMLDivElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const disconnectRef = useRef<(() => void) | undefined>(undefined);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(excerptStorageKey(path)) || "[]");
      setExcerpts(Array.isArray(saved) ? saved : []);
    } catch {
      setExcerpts([]);
    }
  }, [path]);

  useEffect(() => {
    let disposed = false;
    let loaded: PDFDocumentProxy | undefined;
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const response = await invoke<ArrayBuffer | Uint8Array | number[]>("read_pdf_bytes", { path });
        const task = getDocument({
          data: rawBytes(response),
          isEvalSupported: false,
          enableXfa: false,
        });
        loaded = await task.promise;
        if (disposed) {
          await loaded.destroy();
          return;
        }
        setDocument(loaded);
        setPageCount(loaded.numPages);
        setPageNumber(Math.min(loaded.numPages, Math.max(1, Number(initialCitation?.location.page) || 1)));
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => {
      disposed = true;
      disconnectRef.current?.();
      void loaded?.destroy();
    };
  }, [path, initialCitation?.location.page]);

  useEffect(() => {
    if (!document || !canvasRef.current || !textLayerRef.current || !pageStageRef.current) return;
    let cancelled = false;
    let renderTask: ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]> | undefined;
    setRendering(true);
    setFloatingSelection(undefined);
    window.getSelection()?.removeAllRanges();
    void (async () => {
      let renderPhase = "读取页面";
      try {
        const page = await document.getPage(pageNumber);
        if (cancelled) return;
        renderPhase = "计算页面尺寸";
        const viewport = page.getViewport({ scale });
        const canvas = canvasRef.current!;
        const layer = textLayerRef.current!;
        const stage = pageStageRef.current!;
        const pixelRatio = window.devicePixelRatio || 1;
        stage.style.width = `${viewport.width}px`;
        stage.style.height = `${viewport.height}px`;
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        const canvasContext = canvas.getContext("2d");
        if (!canvasContext) throw new Error("无法创建 PDF 画布");
        layer.replaceChildren();
        layer.style.width = `${viewport.width}px`;
        layer.style.height = `${viewport.height}px`;
        layer.style.setProperty("--total-scale-factor", String(viewport.scale));
        renderPhase = "创建画布渲染任务";
        renderTask = page.render({
          canvasContext,
          viewport,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        renderPhase = "提取页面文字";
        const textContent = await page.getTextContent();
        if (!cancelled) {
          renderPhase = "整理页面文字";
          setPageText(
            textContent.items
              .map((item) => ("str" in item ? item.str : ""))
              .filter(Boolean)
              .join(" ")
              .replace(/\s+/g, " ")
              .trim(),
          );
        }
        renderPhase = "等待页面画布";
        await renderTask.promise;
        if (cancelled) return;
        renderPhase = "创建可选择文字层";
        renderSelectableTextLayer(layer, viewport, textContent);
        const quote = String(initialCitation?.quote || "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
        if (quote && Number(initialCitation?.location.page || 0) === pageNumber) {
          layer.querySelectorAll("span").forEach((span) => {
            const value = (span.textContent || "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
            if (value.length >= 4 && quote.includes(value)) span.classList.add("citation-highlight");
          });
        }
      } catch (reason) {
        if (!cancelled && String(reason).toLowerCase().includes("cancel") === false) {
          const detail = reason instanceof Error ? reason.message : String(reason);
          setError(`${renderPhase}失败：${detail}`);
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    })();
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [document, pageNumber, scale, initialCitation]);

  const ensureSession = useCallback(async () => {
    if (sessionId) return sessionId;
    const created = await client.createSession(undefined, `阅读 ${fileName}`, "chat");
    setSessionId(created.id);
    onSessionCreated?.();
    return created.id;
  }, [client, fileName, onSessionCreated, sessionId]);

  function captureReaderSelection() {
    window.requestAnimationFrame(() => {
      const selection = window.getSelection();
      const layer = textLayerRef.current;
      if (!selection || selection.isCollapsed || !layer || selection.rangeCount === 0) {
        setFloatingSelection(undefined);
        return;
      }
      const range = selection.getRangeAt(0);
      const node = range.commonAncestorContainer.nodeType === Node.TEXT_NODE
        ? range.commonAncestorContainer.parentNode
        : range.commonAncestorContainer;
      if (!node || !layer.contains(node)) {
        setFloatingSelection(undefined);
        return;
      }
      const text = selection.toString().replace(/\s+/g, " ").trim().slice(0, 8_000);
      const rect = range.getBoundingClientRect();
      if (!text || rect.width === 0) {
        setFloatingSelection(undefined);
        return;
      }
      setFloatingSelection({
        text,
        page: pageNumber,
        x: Math.min(window.innerWidth - 190, Math.max(190, rect.left + rect.width / 2)),
        y: Math.max(76, rect.top - 10),
      });
    });
  }

  function persistExcerpts(next: SavedExcerpt[]) {
    setExcerpts(next);
    window.localStorage.setItem(excerptStorageKey(path), JSON.stringify(next));
  }

  function saveExcerpt(selection = floatingSelection) {
    if (!selection) return;
    const next = [
      {
        id: crypto.randomUUID(),
        text: selection.text,
        page: selection.page,
        createdAt: new Date().toISOString(),
      },
      ...excerpts,
    ];
    persistExcerpts(next);
    void client.addKnowledgeNote("PDF 阅读摘录", { path, quote: selection.text });
    setQuestionSelection({ text: selection.text, page: selection.page });
    setFloatingSelection(undefined);
    window.getSelection()?.removeAllRanges();
  }

  async function sendReaderQuestion(
    content: string,
    selection?: ReaderSelection,
    action: "question" | "translate" | "explain" | "summarize-page" = "question",
  ) {
    const trimmed = content.trim();
    if (!trimmed || activeRun) return;
    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    const source = selection?.text.trim();
    const sourcePage = selection?.page || pageNumber;
    let prompt = trimmed;
    if (source) {
      prompt = `${trimmed}\n\n当前选区来自《${fileName}》第 ${sourcePage} 页：\n${source}`;
    }
    prompt += `\n\n请只依据当前文档《${fileName}》回答。引用证据时使用“《${fileName}》第 X 页”的格式；证据不足请明确说明。`;
    setMessages((current) => [
      ...current,
      { id: userId, role: "user", content: trimmed, page: selection?.page },
      { id: assistantId, role: "assistant", content: "" },
    ]);
    setQuestion("");
    setQuestionSelection(undefined);
    setFloatingSelection(undefined);
    window.getSelection()?.removeAllRanges();
    setActiveRun("pending");
    setError("");
    try {
      const readerSession = await ensureSession();
      const run = await client.startRun(readerSession, prompt, [path], {
        intent: action === "translate" ? "translate" : action === "explain" ? "explain" : action === "summarize-page" ? "summarize" : "ask",
        document_path: path,
        full_document: fullDocumentMode && action === "question" && !source,
      });
      setActiveRun(run.run_id);
      disconnectRef.current?.();
      disconnectRef.current = client.connectEvents(
        run.run_id,
        (event: RunEvent) => {
          const payload = event.payload;
          if (event.event_type === "message.delta") {
            setMessages((current) => current.map((message) => (
              message.id === assistantId
                ? { ...message, content: cleanAnswer(message.content + String(payload.delta || "")) }
                : message
            )));
          } else if (event.event_type === "message.completed") {
            setMessages((current) => current.map((message) => (
              message.id === assistantId
                ? { ...message, content: cleanAnswer(String(payload.content || "")) }
                : message
            )));
          } else if (event.event_type === "retrieval.citations") {
            const citations = Array.isArray(payload.citations) ? payload.citations as Citation[] : [];
            setMessages((current) => current.map((message) => (
              message.id === assistantId ? { ...message, citations } : message
            )));
          } else if (event.event_type === "run.failed") {
            setError(String(payload.error || "Poppy 阅读当前文档失败。"));
            setActiveRun("");
            setRunProgress("");
          } else if (event.event_type === "run.progress") {
            const completed = Number(payload.completed || 0);
            const total = Number(payload.total || 0);
            setRunProgress(total ? `正在通读全文 ${completed}/${total}` : "正在通读全文");
          } else if (event.event_type === "run.completed" || event.event_type === "run.cancelled") {
            setActiveRun("");
            setRunProgress("");
          }
        },
        () => setActiveRun(""),
      );
    } catch (reason) {
      setActiveRun("");
      setRunProgress("");
      setMessages((current) => current.filter((message) => message.id !== assistantId));
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function cancelRun() {
    if (!activeRun || activeRun === "pending") return;
    try {
      await client.cancelRun(activeRun);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <section className="pdf-reader">
      <header className="pdf-reader-header">
        <div className="pdf-reader-title">
          <strong>{fileName}</strong>
          <span>{pageCount ? `${pageCount} 页 · 当前文档专属问答` : "正在读取 PDF…"}</span>
        </div>
        <div className="pdf-reader-controls">
          <button disabled={pageNumber <= 1} onClick={() => setPageNumber((page) => Math.max(1, page - 1))}><ArrowLeft size={16} /></button>
          <label>
            <input
              type="number"
              min={1}
              max={pageCount || 1}
              value={pageNumber}
              onChange={(event) => setPageNumber(Math.min(pageCount || 1, Math.max(1, Number(event.target.value) || 1)))}
            />
            <span>/ {pageCount || "-"}</span>
          </label>
          <button disabled={!pageCount || pageNumber >= pageCount} onClick={() => setPageNumber((page) => Math.min(pageCount, page + 1))}><ArrowRight size={16} /></button>
          <div className="pdf-reader-zoom">
            <button onClick={() => setScale((value) => Math.max(.65, Math.round((value - .1) * 10) / 10))}><Minus size={15} /></button>
            <span>{Math.round(scale * 100)}%</span>
            <button onClick={() => setScale((value) => Math.min(2.5, Math.round((value + .1) * 10) / 10))}><Plus size={15} /></button>
          </div>
        </div>
        <button className="pdf-reader-close" onClick={onClose} title="关闭阅读器"><X size={20} /></button>
      </header>

      <div className="pdf-reader-body">
        <div className="pdf-viewer-pane" onMouseUp={captureReaderSelection}>
          {loading && <div className="pdf-reader-state"><LoaderCircle className="spin" size={28} />正在打开 PDF…</div>}
          {error && !document && <div className="pdf-reader-state error">{error}</div>}
          {document && (
            <div className="pdf-page-scroll">
              <div className="pdf-page-stage" ref={pageStageRef}>
                <canvas ref={canvasRef} />
                <div className="textLayer" ref={textLayerRef} />
                {rendering && <div className="pdf-page-rendering"><LoaderCircle className="spin" size={20} /></div>}
                {initialCitation?.quote && Number(initialCitation.location.page || 0) === pageNumber && (
                  <div className="pdf-citation-banner"><strong>{initialCitation.label}</strong>{initialCitation.quote}</div>
                )}
              </div>
            </div>
          )}
        </div>

        <aside className="pdf-ai-pane">
          <div className="pdf-ai-heading">
            <div>
              <strong>Poppy 文献助手</strong>
              <span>{fullDocumentMode ? "全文模式：跨章节与表格综合" : "回答只检索当前 PDF"}</span>
            </div>
            <button
              className={fullDocumentMode ? "active" : ""}
              disabled={Boolean(activeRun)}
              onClick={() => setFullDocumentMode((value) => !value)}
              title="开启后，未选中文字的提问会分层通读整篇文档"
            >全文模式</button>
            <button
              disabled={!pageText || Boolean(activeRun)}
              onClick={() => void sendReaderQuestion(
                "请总结当前这一页的核心内容、关键概念和需要注意的限制。",
                { text: pageText, page: pageNumber },
                "summarize-page",
              )}
            >总结本页</button>
          </div>

          <div className="pdf-ai-messages">
            {!messages.length && (
              <div className="pdf-ai-empty">
                <MessageCircleQuestion size={28} />
                <strong>边读边问</strong>
                <p>在左侧划选文字，会在选区旁出现翻译、解释、提问和保存摘录按钮。</p>
              </div>
            )}
            {messages.map((message) => {
              const pages = message.role === "assistant" ? citedPages(message.content, pageCount) : [];
              return (
                <article className={`pdf-ai-message ${message.role}`} key={message.id}>
                  <header>{message.role === "user" ? "你" : "Poppy"}{message.page ? ` · 第 ${message.page} 页` : ""}</header>
                  {message.role === "assistant" && !message.content
                    ? <div className="pdf-ai-thinking"><i /><i /><i />{runProgress || "正在阅读当前文档…"}</div>
                    : <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
                  {!!pages.length && (
                    <div className="pdf-page-citations">
                      {pages.map((page) => <button key={page} onClick={() => setPageNumber(page)}>跳到第 {page} 页</button>)}
                    </div>
                  )}
                  {!!message.citations?.length && (
                    <div className="pdf-page-citations">
                      {message.citations.map((citation) => (
                        <button key={`${citation.chunk_id}-${citation.label}`} onClick={() => citation.location.page && setPageNumber(citation.location.page)} title={citation.quote}>
                          {citation.label} {citation.location.page ? `第 ${citation.location.page} 页` : citation.title}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>

          {!!excerpts.length && (
            <details className="pdf-excerpts">
              <summary>已保存摘录（{excerpts.length}）</summary>
              <div>
                {excerpts.map((excerpt) => (
                  <article key={excerpt.id}>
                    <button className="pdf-excerpt-text" onClick={() => setPageNumber(excerpt.page)}>
                      <strong>第 {excerpt.page} 页</strong>
                      <span>{excerpt.text}</span>
                    </button>
                    <button
                      className="pdf-excerpt-delete"
                      title="删除摘录"
                      onClick={() => persistExcerpts(excerpts.filter((item) => item.id !== excerpt.id))}
                    ><Trash2 size={13} /></button>
                  </article>
                ))}
              </div>
            </details>
          )}

          {questionSelection && (
            <div className="pdf-question-selection">
              <div><strong>第 {questionSelection.page} 页选区</strong><span>{questionSelection.text}</span></div>
              <button onClick={() => setQuestionSelection(undefined)}><X size={14} /></button>
            </div>
          )}
          {error && document && <div className="pdf-ai-error">{error}</div>}
          <div className="pdf-ai-composer">
            <textarea
              ref={questionRef}
              value={question}
              rows={2}
              placeholder={questionSelection ? "询问这个选区…" : "询问当前文档…"}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void sendReaderQuestion(question, questionSelection);
                }
              }}
            />
            <button
              className={activeRun ? "stop" : ""}
              disabled={activeRun === "pending" || (!activeRun && !question.trim())}
              onClick={() => activeRun ? void cancelRun() : void sendReaderQuestion(question, questionSelection)}
            >
              {activeRun ? (activeRun === "pending" ? <LoaderCircle className="spin" size={16} /> : <Square size={13} fill="currentColor" />) : <Send size={16} />}
            </button>
          </div>
        </aside>
      </div>

      {floatingSelection && (
        <div
          className="pdf-selection-toolbar"
          style={{ left: floatingSelection.x, top: floatingSelection.y }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <button onClick={() => void sendReaderQuestion("请准确翻译这段选区，并保留专业术语。", floatingSelection, "translate")}><Languages size={14} />翻译</button>
          <button onClick={() => void sendReaderQuestion("请解释这段选区的含义、背景和关键概念。", floatingSelection, "explain")}><Sparkles size={14} />解释</button>
          <button onClick={() => {
            setQuestionSelection({ text: floatingSelection.text, page: floatingSelection.page });
            setFloatingSelection(undefined);
            window.setTimeout(() => questionRef.current?.focus(), 10);
          }}><MessageCircleQuestion size={14} />提问</button>
          <button onClick={() => saveExcerpt()}><BookmarkPlus size={14} />摘录</button>
          <button onClick={() => void navigator.clipboard.writeText(floatingSelection.text)} title="复制"><Copy size={14} /></button>
        </div>
      )}
    </section>
  );
}
