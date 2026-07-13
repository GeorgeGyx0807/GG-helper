import { FolderPlus, MessageSquarePlus, MoreHorizontal, Settings } from "lucide-react";
import type { Grant, SessionSummary } from "../types";

type Props = {
  sessions: SessionSummary[];
  grants: Grant[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onAddFolder: () => void;
  onSettings: () => void;
  onRename: (id: string, currentTitle: string) => void;
};

export function Sidebar({ sessions, grants, selectedId, onSelect, onNew, onAddFolder, onSettings, onRename }: Props) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">P</div>
        <div>
          <strong>Poppy</strong>
          <span>personal assistant</span>
        </div>
      </div>

      <button className="primary-action" onClick={onNew} disabled={!grants.length}>
        <MessageSquarePlus size={17} /> New conversation
      </button>

      <div className="sidebar-section">
        <div className="section-label">Conversations</div>
        <div className="session-list">
          {sessions.map((session) => (
            <button
              key={session.id}
              className={`session-item ${selectedId === session.id ? "active" : ""}`}
              onClick={() => onSelect(session.id)}
            >
              <span>{session.title}</span>
              <span
                className="session-menu"
                role="button"
                aria-label={`Rename ${session.title}`}
                onClick={(event) => { event.stopPropagation(); onRename(session.id, session.title); }}
              ><MoreHorizontal size={15} /></span>
            </button>
          ))}
          {!sessions.length && <p className="empty-copy">Your conversations will appear here.</p>}
        </div>
      </div>

      <div className="sidebar-spacer" />
      <div className="folder-summary">
        <div>
          <span className="section-label">Authorized folders</span>
          <strong>{grants.length}</strong>
        </div>
        <button className="icon-button" onClick={onAddFolder} aria-label="Add authorized folder">
          <FolderPlus size={17} />
        </button>
      </div>
      <button className="sidebar-link" onClick={onSettings}>
        <Settings size={17} /> Settings
      </button>
    </aside>
  );
}
