import { Folder, FolderPlus, MessageSquarePlus, MoreHorizontal, Pencil, Settings, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import poppyMark from "../assets/poppy-mark.svg";
import type { Grant, SessionSummary } from "../types";

type Props = {
  sessions: SessionSummary[];
  chatSessions: SessionSummary[];
  grants: Grant[];
  selectedId?: string;
  selectedWorkspace?: string;
  busy?: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onAddFolder: () => void;
  onOpenProject: (path: string) => void;
  onSettings: () => void;
  onRename: (id: string, currentTitle: string) => void;
  onDelete: (id: string, currentTitle: string) => void;
};

function folderName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

export function Sidebar({
  sessions,
  chatSessions,
  grants,
  selectedId,
  selectedWorkspace,
  busy,
  onSelect,
  onNew,
  onAddFolder,
  onOpenProject,
  onSettings,
  onRename,
  onDelete,
}: Props) {
  const [openMenu, setOpenMenu] = useState<string>();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!openMenu) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpenMenu(undefined);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [openMenu]);

  function renderSession(session: SessionSummary) {
    return (
      <div className={`session-row ${selectedId === session.id ? "active" : ""}`} key={session.id} ref={openMenu === session.id ? menuRef : undefined}>
        <button
          className="session-item"
          disabled={busy && selectedId !== session.id}
          onClick={() => { setOpenMenu(undefined); onSelect(session.id); }}
        >
          <span>{session.title}</span>
        </button>
        <button
          className="session-menu"
          aria-label={`管理对话：${session.title}`}
          onClick={(event) => { event.stopPropagation(); setOpenMenu((current) => current === session.id ? undefined : session.id); }}
          disabled={busy}
        ><MoreHorizontal size={16} /></button>
        {openMenu === session.id && (
          <div className="session-action-menu">
            <button onClick={() => { setOpenMenu(undefined); onRename(session.id, session.title); }}><Pencil size={14} />重命名</button>
            <button className="danger" onClick={() => { setOpenMenu(undefined); onDelete(session.id, session.title); }}><Trash2 size={14} />删除对话</button>
          </div>
        )}
      </div>
    );
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-mark" src={poppyMark} alt="Poppy" />
        <div>
          <strong>Poppy</strong>
          <span>你的桌面个人助手</span>
        </div>
      </div>

      <button className="primary-action" onClick={onNew} disabled={busy}>
        <MessageSquarePlus size={17} /> 新建任务
      </button>

      <div className="sidebar-section chat-section">
        <div className="section-heading">
          <span className="section-label">对话</span>
          <button className="section-add" onClick={onNew} aria-label="新建对话" title="新建对话"><MessageSquarePlus size={15} /></button>
        </div>
        <div className="chat-list">
          {chatSessions.length ? chatSessions.map(renderSession) : <p className="empty-copy">简单问题可以在这里直接聊天</p>}
        </div>
      </div>

      <div className="sidebar-section projects-section">
        <div className="section-heading">
          <span className="section-label">项目</span>
          <button className="section-add" onClick={onAddFolder} aria-label="添加项目" title="添加项目"><FolderPlus size={15} /></button>
        </div>
        <div className="project-list">
          {grants.map((grant) => {
            const projectSessions = sessions.filter((session) => session.session_type !== "chat" && session.workspace_root === grant.path);
            return (
              <section className={`project-block ${selectedWorkspace === grant.path ? "active" : ""}`} key={grant.id}>
                <button className="project-item" onClick={() => onOpenProject(grant.path)} disabled={busy}>
                  <Folder size={16} />
                  <span><strong>{folderName(grant.path)}</strong><small>{grant.path}</small></span>
                </button>
                <div className="project-conversations">
                  <span className="project-conversation-label">对话</span>
                  {projectSessions.length ? projectSessions.map(renderSession) : <p className="empty-copy">暂无对话</p>}
                </div>
              </section>
            );
          })}
          {!grants.length && <p className="empty-copy">点击右侧加号添加项目文件夹</p>}
        </div>
      </div>

      <div className="sidebar-spacer" />
      <button className="sidebar-link" onClick={onSettings}>
        <Settings size={17} /> 设置
      </button>
    </aside>
  );
}
