import { Check, ChevronDown, CircleAlert, Clock3, LoaderCircle, TerminalSquare } from "lucide-react";
import { useState } from "react";
import type { ToolCall } from "../types";

const statusIcon = {
  requested: <Clock3 size={16} />,
  waiting: <Clock3 size={16} />,
  running: <LoaderCircle className="spin" size={16} />,
  completed: <Check size={16} />,
  failed: <CircleAlert size={16} />,
  cancelled: <CircleAlert size={16} />,
};

const statusText = {
  requested: "已请求",
  waiting: "等待确认",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function ToolCard({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(tool.status === "waiting" || tool.status === "failed");
  return (
    <div className={`tool-card status-${tool.status}`}>
      <button className="tool-header" onClick={() => setOpen(!open)}>
        <TerminalSquare size={17} />
        <div>
          <strong>{tool.name}</strong>
          <span>{statusText[tool.status]}</span>
        </div>
        <div className="tool-status">{statusIcon[tool.status]}</div>
        <ChevronDown className={open ? "rotated" : ""} size={16} />
      </button>
      {open && (
        <div className="tool-details">
          <div className="detail-label">参数</div>
          <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
          {tool.output && (
            <>
              <div className="detail-label">输出</div>
              <pre>{tool.output}</pre>
            </>
          )}
          {!!tool.affectedPaths?.length && (
            <div className="affected-paths">
              <span className="detail-label">文件变化</span>
              {tool.affectedPaths.map((path) => <code key={path}>{path}</code>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
