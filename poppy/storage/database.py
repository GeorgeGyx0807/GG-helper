"""Small SQLite index for desktop sessions, settings, grants, and memories."""

import json
import re
import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

CURRENT_SCHEMA_VERSION = 5
SEARCH_TERM_PATTERN = re.compile(r"[A-Za-z0-9_.+-]{2,}|[\u3400-\u9fff]{2,}")
CHINESE_SPAN_PATTERN = re.compile(r"[\u3400-\u9fff]{2,}")
SEARCH_STOP_TERMS = {"这个", "那个", "文件", "文档", "里面", "什么", "怎么", "如何", "请问", "一下", "的是", "设计的"}
SEARCH_SYNONYMS = {
    "记忆": ("memory",),
    "上下文": ("context",),
    "历史": ("history",),
    "会话": ("session",),
    "检查点": ("checkpoint",),
    "缓存": ("cache",),
    "索引": ("index", "search"),
    "检索": ("search", "retrieval"),
    "架构": ("architecture",),
}


def _search_terms(query):
    terms = []

    def add(value):
        normalized = str(value).casefold().strip()
        if len(normalized) >= 2 and normalized not in SEARCH_STOP_TERMS and normalized not in terms:
            terms.append(normalized)

    for value in SEARCH_TERM_PATTERN.findall(str(query)):
        if not CHINESE_SPAN_PATTERN.fullmatch(value):
            add(value)
    for span in CHINESE_SPAN_PATTERN.findall(str(query)):
        if len(span) <= 8:
            add(span)
        for keyword, synonyms in SEARCH_SYNONYMS.items():
            if keyword in span:
                add(keyword)
                for synonym in synonyms:
                    add(synonym)
        # SQLite's default tokenizer does not segment Chinese. Short n-grams
        # give the LIKE fallback useful words such as 记忆/上下文 without adding
        # a heavyweight tokenizer to the desktop bundle.
        for size in (2, 3, 4):
            for start in range(max(0, len(span) - size + 1)):
                add(span[start:start + size])
                if len(terms) >= 24:
                    return terms
    return terms or [str(query).casefold().strip()]


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DesktopDatabase:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self._backup_before_migration(path)
        self._migrate()

    def _backup_before_migration(self, path):
        if not path.exists() or path.stat().st_size == 0:
            return
        try:
            version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        except Exception:
            version = 0
        if version >= CURRENT_SCHEMA_VERSION:
            return
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{path.stem}-before-v{CURRENT_SCHEMA_VERSION}-{int(datetime.now(timezone.utc).timestamp())}.db"
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            # A migration must remain usable even if a read-only backup
            # location is unavailable; the failure is intentionally local.
            pass

    def _migrate(self):
        with self.lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    session_type TEXT NOT NULL DEFAULT 'project',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS grants (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    can_read INTEGER NOT NULL,
                    can_write INTEGER NOT NULL,
                    can_shell INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approval_rules (
                    id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    path_scope TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tool_name, operation, path_scope)
                );
                CREATE TABLE IF NOT EXISTS library_sources (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL DEFAULT 'folder',
                    grant_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_indexed_at TEXT NOT NULL DEFAULT '',
                    index_status TEXT NOT NULL DEFAULT 'idle',
                    index_progress INTEGER NOT NULL DEFAULT 0,
                    indexed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT 'text/plain',
                    size INTEGER NOT NULL DEFAULT 0,
                    mtime_ns INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    location_json TEXT NOT NULL DEFAULT '{}',
                    content TEXT NOT NULL,
                    embedding_language TEXT NOT NULL DEFAULT '',
                    embedding_base64 TEXT NOT NULL DEFAULT '',
                    UNIQUE(document_id, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS index_failures (
                    source_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    error TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, path)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    tool_name TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    tenant_key TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL DEFAULT '',
                    sender_open_id TEXT NOT NULL DEFAULT '',
                    poppy_session_id TEXT NOT NULL,
                    workspace_root TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel, tenant_key, chat_id, thread_id, sender_open_id)
                );
                CREATE TABLE IF NOT EXISTS channel_messages (
                    channel TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'processing',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(channel, message_id)
                );
                CREATE TABLE IF NOT EXISTS session_attachments (
                    session_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'desktop',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, path)
                );
                """
            )
            try:
                self.connection.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        title, path, content, tokenize='unicode61'
                    )"""
                )
                self.fts_available = True
            except sqlite3.OperationalError:
                # Some embedded SQLite builds omit FTS5.  The indexer falls
                # back to a bounded LIKE search, while exposing the flag so
                # the UI can explain the degraded mode.
                self.fts_available = False
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (CURRENT_SCHEMA_VERSION, utc_now()),
            )
            self.connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            session_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "session_type" not in session_columns:
                self.connection.execute(
                    "ALTER TABLE sessions ADD COLUMN session_type TEXT NOT NULL DEFAULT 'project'"
                )
            if "locked_document_id" not in session_columns:
                self.connection.execute(
                    "ALTER TABLE sessions ADD COLUMN locked_document_id TEXT NOT NULL DEFAULT ''"
                )
            source_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(library_sources)").fetchall()
            }
            for name, definition in (
                ("index_status", "TEXT NOT NULL DEFAULT 'idle'"),
                ("index_progress", "INTEGER NOT NULL DEFAULT 0"),
                ("indexed_count", "INTEGER NOT NULL DEFAULT 0"),
                ("failed_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in source_columns:
                    self.connection.execute(
                        f"ALTER TABLE library_sources ADD COLUMN {name} {definition}"
                    )
            chunk_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(document_chunks)").fetchall()
            }
            if "location_json" not in chunk_columns:
                self.connection.execute(
                    "ALTER TABLE document_chunks ADD COLUMN location_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "embedding_language" not in chunk_columns:
                self.connection.execute(
                    "ALTER TABLE document_chunks ADD COLUMN embedding_language TEXT NOT NULL DEFAULT ''"
                )
            if "embedding_base64" not in chunk_columns:
                self.connection.execute(
                    "ALTER TABLE document_chunks ADD COLUMN embedding_base64 TEXT NOT NULL DEFAULT ''"
                )
            # 早期版本的默认标题是英文，首次打开新版时统一为中文。
            self.connection.execute(
                "UPDATE sessions SET title = ? WHERE title = ?",
                ("新对话", "New conversation"),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (CURRENT_SCHEMA_VERSION, utc_now()),
            )
            self.connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def upsert_session(self, session_id, title, workspace_root, created_at=None, session_type="project"):
        timestamp = utc_now()
        title = str(title).strip() or "新对话"
        if title == "New conversation":
            title = "新对话"
        session_type = str(session_type).strip().lower() or "project"
        if session_type not in {"project", "chat"}:
            raise ValueError("invalid session type")
        resolved_root = str(Path(workspace_root).expanduser().resolve()) if workspace_root else ""
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO sessions(id, title, workspace_root, session_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    workspace_root=excluded.workspace_root,
                    session_type=excluded.session_type,
                    updated_at=excluded.updated_at
                """,
                (session_id, title, resolved_root, session_type, created_at or timestamp, timestamp),
            )
        return self.get_session(session_id)

    def list_sessions(self):
        return self._all("SELECT * FROM sessions ORDER BY updated_at DESC")

    def get_session(self, session_id):
        return self._one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def rename_session(self, session_id, title):
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), session_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown session: {session_id}")
        return self.get_session(session_id)

    def set_session_document_lock(self, session_id, document_id=""):
        if document_id and self.get_document(document_id) is None:
            raise KeyError(f"unknown document: {document_id}")
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE sessions SET locked_document_id = ?, updated_at = ? WHERE id = ?",
                (str(document_id or ""), utc_now(), str(session_id)),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown session: {session_id}")
        return self.get_session(session_id)

    def delete_session(self, session_id):
        with self.lock, self.connection:
            cursor = self.connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.connection.execute("DELETE FROM session_attachments WHERE session_id = ?", (session_id,))
            self.connection.execute("DELETE FROM channel_sessions WHERE poppy_session_id = ?", (session_id,))
        if cursor.rowcount == 0:
            raise KeyError(f"unknown session: {session_id}")

    def get_channel_session(self, channel, tenant_key, chat_id, thread_id="", sender_open_id=""):
        return self._one(
            """SELECT * FROM channel_sessions WHERE channel = ? AND tenant_key = ?
            AND chat_id = ? AND thread_id = ? AND sender_open_id = ?""",
            (str(channel), str(tenant_key), str(chat_id), str(thread_id), str(sender_open_id)),
        )

    def upsert_channel_session(
        self,
        channel,
        tenant_key,
        chat_id,
        poppy_session_id,
        thread_id="",
        sender_open_id="",
        workspace_root="",
    ):
        timestamp = utc_now()
        mapping_id = "channel_" + uuid4().hex
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO channel_sessions(
                    id, channel, tenant_key, chat_id, thread_id, sender_open_id,
                    poppy_session_id, workspace_root, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, tenant_key, chat_id, thread_id, sender_open_id)
                DO UPDATE SET poppy_session_id=excluded.poppy_session_id,
                    workspace_root=excluded.workspace_root, updated_at=excluded.updated_at""",
                (
                    mapping_id,
                    str(channel),
                    str(tenant_key),
                    str(chat_id),
                    str(thread_id),
                    str(sender_open_id),
                    str(poppy_session_id),
                    str(workspace_root),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_channel_session(channel, tenant_key, chat_id, thread_id, sender_open_id)

    def list_channel_sessions(self, channel=""):
        if channel:
            return self._all(
                "SELECT * FROM channel_sessions WHERE channel = ? ORDER BY updated_at DESC",
                (str(channel),),
            )
        return self._all("SELECT * FROM channel_sessions ORDER BY updated_at DESC")

    def delete_channel_session(self, mapping_id):
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM channel_sessions WHERE id = ?", (str(mapping_id),)
            )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown channel session: {mapping_id}")

    def claim_channel_message(self, channel, message_id):
        timestamp = utc_now()
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO channel_messages(
                    channel, message_id, status, created_at, updated_at
                ) VALUES (?, ?, 'processing', ?, ?)""",
                (str(channel), str(message_id), timestamp, timestamp),
            )
        return cursor.rowcount == 1

    def finish_channel_message(self, channel, message_id, status="completed"):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE channel_messages SET status = ?, updated_at = ? WHERE channel = ? AND message_id = ?",
                (str(status), utc_now(), str(channel), str(message_id)),
            )

    def add_session_attachment(self, session_id, path, source="desktop"):
        resolved = str(Path(path).expanduser().resolve())
        timestamp = utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO session_attachments(session_id, path, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, path) DO UPDATE SET
                    source=excluded.source, updated_at=excluded.updated_at""",
                (str(session_id), resolved, str(source), timestamp, timestamp),
            )
        return {"session_id": str(session_id), "path": resolved, "source": str(source)}

    def list_session_attachments(self, session_id):
        return self._all(
            "SELECT * FROM session_attachments WHERE session_id = ? ORDER BY created_at",
            (str(session_id),),
        )

    def set_setting(self, key, value):
        encoded = json.dumps(value, ensure_ascii=False)
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, encoded, utc_now()),
            )
        return value

    def get_settings(self):
        rows = self._all("SELECT key, value FROM settings ORDER BY key")
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def add_grant(self, path, can_read=True, can_write=False, can_shell=False):
        resolved = str(Path(path).expanduser().resolve())
        grant_id = "grant_" + uuid4().hex
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO grants(id, path, can_read, can_write, can_shell, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    can_read=excluded.can_read,
                    can_write=excluded.can_write,
                    can_shell=excluded.can_shell
                """,
                (grant_id, resolved, int(can_read), int(can_write), int(can_shell), utc_now()),
            )
        return self.get_grant_by_path(resolved)

    def list_grants(self):
        return [self._normalize_grant(row) for row in self._all("SELECT * FROM grants ORDER BY path")]

    def get_grant_by_path(self, path):
        row = self._one("SELECT * FROM grants WHERE path = ?", (str(Path(path).resolve()),))
        return self._normalize_grant(row) if row else None

    def delete_grant(self, grant_id):
        with self.lock, self.connection:
            cursor = self.connection.execute("DELETE FROM grants WHERE id = ?", (grant_id,))
        if cursor.rowcount == 0:
            raise KeyError(f"unknown grant: {grant_id}")

    def upsert_library_source(self, path, grant_id="", kind="folder"):
        resolved = str(Path(path).expanduser().resolve())
        source_id = "source_" + uuid4().hex
        timestamp = utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO library_sources(id, path, kind, grant_id, enabled, last_indexed_at, created_at)
                VALUES (?, ?, ?, ?, 1, '', ?)
                ON CONFLICT(path) DO UPDATE SET
                    grant_id=excluded.grant_id,
                    kind=excluded.kind,
                    enabled=1
                """,
                (source_id, resolved, str(kind), str(grant_id), timestamp),
            )
        return self.get_library_source_by_path(resolved)

    def list_library_sources(self, enabled_only=False):
        sql = """SELECT ls.*, COUNT(d.id) AS document_count
        FROM library_sources ls LEFT JOIN documents d ON d.source_id = ls.id"""
        if enabled_only:
            sql += " WHERE ls.enabled = 1"
        sql += " GROUP BY ls.id ORDER BY ls.path"
        return self._all(sql)

    def get_library_source(self, source_id):
        return self._one(
            """SELECT ls.*, COUNT(d.id) AS document_count FROM library_sources ls
            LEFT JOIN documents d ON d.source_id = ls.id WHERE ls.id = ? GROUP BY ls.id""",
            (str(source_id),),
        )

    def get_library_source_by_path(self, path):
        return self._one(
            """SELECT ls.*, COUNT(d.id) AS document_count FROM library_sources ls
            LEFT JOIN documents d ON d.source_id = ls.id WHERE ls.path = ? GROUP BY ls.id""",
            (str(Path(path).expanduser().resolve()),),
        )

    def mark_library_source_indexed(self, source_id):
        with self.lock, self.connection:
            self.connection.execute(
                """UPDATE library_sources SET last_indexed_at = ?, enabled = 1,
                index_status = 'idle', index_progress = 100, last_error = ''
                WHERE id = ?""",
                (utc_now(), str(source_id)),
            )

    def update_library_source_index_state(
        self,
        source_id,
        status,
        progress=0,
        indexed_count=None,
        failed_count=None,
        last_error="",
    ):
        fields = ["index_status = ?", "index_progress = ?", "last_error = ?"]
        values = [str(status), max(0, min(100, int(progress))), str(last_error or "")]
        if indexed_count is not None:
            fields.append("indexed_count = ?")
            values.append(max(0, int(indexed_count)))
        if failed_count is not None:
            fields.append("failed_count = ?")
            values.append(max(0, int(failed_count)))
        values.append(str(source_id))
        with self.lock, self.connection:
            self.connection.execute(
                f"UPDATE library_sources SET {', '.join(fields)} WHERE id = ?",
                tuple(values),
            )

    def replace_index_failures(self, source_id, failures):
        timestamp = utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                "DELETE FROM index_failures WHERE source_id = ?", (str(source_id),)
            )
            self.connection.executemany(
                "INSERT INTO index_failures(source_id, path, error, updated_at) VALUES (?, ?, ?, ?)",
                [
                    (str(source_id), str(item["path"]), str(item["error"]), timestamp)
                    for item in failures
                ],
            )

    def upsert_index_failure(self, source_id, path, error):
        resolved = str(Path(path).expanduser().resolve())
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO index_failures(source_id, path, error, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    error=excluded.error, updated_at=excluded.updated_at""",
                (str(source_id), resolved, str(error), utc_now()),
            )

    def clear_index_failure(self, source_id, path):
        resolved = str(Path(path).expanduser().resolve())
        with self.lock, self.connection:
            self.connection.execute(
                "DELETE FROM index_failures WHERE source_id = ? AND path = ?",
                (str(source_id), resolved),
            )

    def list_index_failures(self, source_id="", limit=200):
        if source_id:
            return self._all(
                """SELECT * FROM index_failures WHERE source_id = ?
                ORDER BY updated_at DESC, path LIMIT ?""",
                (str(source_id), max(1, min(int(limit), 1000))),
            )
        return self._all(
            "SELECT * FROM index_failures ORDER BY updated_at DESC, path LIMIT ?",
            (max(1, min(int(limit), 1000)),),
        )

    def delete_library_source(self, source_id):
        source = self.get_library_source(source_id)
        if source is None:
            raise KeyError(f"unknown library source: {source_id}")
        with self.lock, self.connection:
            document_ids = [
                row["id"]
                for row in self.connection.execute(
                    "SELECT id FROM documents WHERE source_id = ?", (str(source_id),)
                ).fetchall()
            ]
            for document_id in document_ids:
                self._delete_document_locked(document_id)
            self.connection.execute(
                "DELETE FROM index_failures WHERE source_id = ?", (str(source_id),)
            )
            self.connection.execute("DELETE FROM library_sources WHERE id = ?", (str(source_id),))

    def upsert_document(self, source_id, path, display_name, mime_type, size, mtime_ns, sha256, content):
        resolved = str(Path(path).expanduser().resolve())
        document_id = "doc_" + uuid4().hex
        timestamp = utc_now()
        with self.lock, self.connection:
            row = self.connection.execute("SELECT id FROM documents WHERE path = ?", (resolved,)).fetchone()
            if row:
                document_id = row["id"]
                self.connection.execute(
                    """UPDATE documents SET source_id=?, display_name=?, mime_type=?, size=?, mtime_ns=?,
                    sha256=?, content=?, updated_at=? WHERE id=?""",
                    (str(source_id), str(display_name), str(mime_type), int(size), int(mtime_ns), str(sha256), str(content), timestamp, document_id),
                )
                self._delete_chunks_locked(document_id)
            else:
                self.connection.execute(
                    """INSERT INTO documents(id, source_id, path, display_name, mime_type, size, mtime_ns,
                    sha256, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (document_id, str(source_id), resolved, str(display_name), str(mime_type), int(size), int(mtime_ns), str(sha256), str(content), timestamp, timestamp),
                )
            if getattr(self, "fts_available", False):
                self.connection.execute("DELETE FROM documents_fts WHERE rowid IN (SELECT id FROM document_chunks WHERE document_id = ?)", (document_id,))
            return self._one_locked("SELECT * FROM documents WHERE id = ?", (document_id,))

    def replace_document_chunks(self, document_id, chunks):
        with self.lock, self.connection:
            self._delete_chunks_locked(document_id)
            for chunk_index, item in enumerate(chunks):
                cursor = self.connection.execute(
                    """INSERT INTO document_chunks(
                        document_id, chunk_index, line_start, line_end, location_json, content,
                        embedding_language, embedding_base64
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(document_id),
                        chunk_index,
                        int(item["line_start"]),
                        int(item["line_end"]),
                        json.dumps(item.get("location") or {}, ensure_ascii=False, sort_keys=True),
                        str(item["content"]),
                        str(item.get("embedding_language") or ""),
                        str(item.get("embedding_base64") or ""),
                    ),
                )
                chunk_id = cursor.lastrowid
                if getattr(self, "fts_available", False):
                    document = self.connection.execute("SELECT display_name, path FROM documents WHERE id = ?", (str(document_id),)).fetchone()
                    self.connection.execute(
                        "INSERT INTO documents_fts(rowid, title, path, content) VALUES (?, ?, ?, ?)",
                        (chunk_id, document["display_name"], document["path"], str(item["content"])),
                    )

    def delete_documents_not_in(self, source_id, paths):
        normalized = {str(Path(path).expanduser().resolve()) for path in paths}
        with self.lock, self.connection:
            rows = self.connection.execute("SELECT id, path FROM documents WHERE source_id = ?", (str(source_id),)).fetchall()
            for row in rows:
                if row["path"] not in normalized:
                    self._delete_document_locked(row["id"])

    def delete_document_by_path(self, path, source_id=""):
        resolved = str(Path(path).expanduser().resolve())
        with self.lock, self.connection:
            if source_id:
                row = self.connection.execute(
                    "SELECT id FROM documents WHERE path = ? AND source_id = ?",
                    (resolved, str(source_id)),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT id FROM documents WHERE path = ?",
                    (resolved,),
                ).fetchone()
            if row is not None:
                self._delete_document_locked(row["id"])
                return True
        return False

    def list_documents(self, source_id=None):
        if source_id:
            return self._all("SELECT * FROM documents WHERE source_id = ? ORDER BY path", (str(source_id),))
        return self._all("SELECT * FROM documents ORDER BY path")

    def list_document_summaries(self, source_ids=None):
        source_ids = [str(item) for item in (source_ids or [])]
        sql = """SELECT d.id, d.source_id, d.path, d.display_name, d.mime_type,
        d.size, d.mtime_ns, d.updated_at, COUNT(c.id) AS chunk_count
        FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id"""
        params = []
        if source_ids:
            sql += " WHERE d.source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            params.extend(source_ids)
        sql += " GROUP BY d.id ORDER BY lower(d.display_name), d.path"
        return self._all(sql, tuple(params))

    def get_document(self, document_id):
        return self._one("SELECT * FROM documents WHERE id = ?", (str(document_id),))

    def get_document_by_path(self, path):
        return self._one(
            "SELECT * FROM documents WHERE path = ?",
            (str(Path(path).expanduser().resolve()),),
        )

    def list_document_chunks(self, document_id):
        rows = self._all(
            """SELECT id, document_id, chunk_index, line_start, line_end, location_json, content,
            embedding_language, embedding_base64
            FROM document_chunks WHERE document_id = ? ORDER BY chunk_index""",
            (str(document_id),),
        )
        for row in rows:
            try:
                row["location"] = json.loads(row.pop("location_json") or "{}")
            except (TypeError, ValueError):
                row["location"] = {}
                row.pop("location_json", None)
        return rows

    def document_embeddings_ready(self, document_id):
        row = self._one(
            """SELECT COUNT(*) AS total,
            SUM(CASE WHEN embedding_base64 <> '' THEN 1 ELSE 0 END) AS embedded
            FROM document_chunks WHERE document_id = ?""",
            (str(document_id),),
        )
        return bool(row and int(row.get("total") or 0) > 0 and int(row.get("total") or 0) == int(row.get("embedded") or 0))

    def list_chunk_embeddings(self, source_ids=None, document_ids=None):
        source_ids = [str(item) for item in (source_ids or [])]
        document_ids = [str(item) for item in (document_ids or [])]
        clauses = ["c.embedding_base64 <> ''"]
        params = []
        if document_ids:
            clauses.append("d.id IN (" + ",".join("?" for _ in document_ids) + ")")
            params.extend(document_ids)
        elif source_ids:
            clauses.append("d.source_id IN (" + ",".join("?" for _ in source_ids) + ")")
            params.extend(source_ids)
        else:
            return []
        return self._all(
            """SELECT c.id AS chunk_id, d.id, d.path, d.display_name, c.line_start,
            c.line_end, c.location_json, c.content, c.embedding_language, c.embedding_base64
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE """ + " AND ".join(clauses),
            tuple(params),
        )

    def search_documents(self, query, limit=20, source_ids=None, document_ids=None):
        query = str(query).strip()
        limit = max(1, min(int(limit), 100))
        if not query:
            return []
        source_ids = [str(item) for item in (source_ids or [])]
        document_ids = [str(item) for item in (document_ids or [])]
        if document_ids:
            scope_column = "d.id"
            scope_values = document_ids
        else:
            scope_column = "d.source_id"
            scope_values = source_ids
        if not scope_values:
            return []
        placeholders = ",".join("?" for _ in scope_values)
        terms = _search_terms(query)[:24]
        fetch_limit = min(500, max(limit * 8, limit))
        with self.lock:
            rows = []
            if getattr(self, "fts_available", False):
                try:
                    fts_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
                    rows = self.connection.execute(
                        """SELECT c.id AS chunk_id, d.id, d.path, d.display_name, c.line_start, c.line_end, c.location_json, c.content,
                        bm25(documents_fts) AS rank FROM documents_fts f
                        JOIN document_chunks c ON c.id = f.rowid JOIN documents d ON d.id = c.document_id
                        WHERE documents_fts MATCH ? AND """ + scope_column + " IN (" + placeholders + ") ORDER BY rank LIMIT ?",
                        (fts_query, *scope_values, fetch_limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            match_clauses = []
            match_values = []
            for term in terms:
                needle = f"%{term}%"
                match_clauses.append("(lower(c.content) LIKE ? OR lower(d.display_name) LIKE ? OR lower(d.path) LIKE ?)")
                match_values.extend((needle, needle, needle))
            like_rows = self.connection.execute(
                """SELECT c.id AS chunk_id, d.id, d.path, d.display_name, c.line_start, c.line_end,
                c.location_json, c.content, 0 AS rank
                FROM document_chunks c JOIN documents d ON d.id = c.document_id
                WHERE (""" + " OR ".join(match_clauses) + ") AND " + scope_column + " IN (" + placeholders + ") ORDER BY d.path, c.line_start LIMIT ?",
                (*match_values, *scope_values, fetch_limit),
            ).fetchall()
            known = {row["chunk_id"] for row in rows}
            rows.extend(row for row in like_rows if row["chunk_id"] not in known)
        results = []
        for row in rows:
            item = dict(row)
            try:
                item["location"] = json.loads(item.pop("location_json") or "{}")
            except (TypeError, ValueError):
                item["location"] = {}
                item.pop("location_json", None)
            content_text = str(item.get("content", "")).casefold()
            title_text = f"{item.get('display_name', '')}\n{item.get('path', '')}".casefold()
            item["match_score"] = sum(3 for term in terms if term in content_text) + sum(
                1 for term in terms if term in title_text
            )
            results.append(item)
        results.sort(key=lambda item: (-item["match_score"], float(item.get("rank") or 0), item["path"], item["line_start"]))
        return results[:limit]

    def add_audit_event(self, event_type, tool_name="", session_id="", run_id="", scope="", details=None):
        event_id = "audit_" + uuid4().hex
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO audit_events(id, event_type, tool_name, session_id, run_id, scope, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, str(event_type), str(tool_name), str(session_id), str(run_id), str(scope), json.dumps(details or {}, ensure_ascii=False), utc_now()),
            )
        return self._one("SELECT * FROM audit_events WHERE id = ?", (event_id,))

    def list_audit_events(self, limit=200):
        return self._all("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 1000)),))

    def add_memory(self, category, content, source_session_id=""):
        memory_id = "memory_" + uuid4().hex
        timestamp = utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, category, content, source_session_id, timestamp, timestamp),
            )
        return self.get_memory(memory_id)

    def list_memories(self):
        return self._all("SELECT * FROM memories ORDER BY updated_at DESC")

    def get_memory(self, memory_id):
        return self._one("SELECT * FROM memories WHERE id = ?", (memory_id,))

    def update_memory(self, memory_id, content):
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                (content, utc_now(), memory_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown memory: {memory_id}")
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id):
        with self.lock, self.connection:
            cursor = self.connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if cursor.rowcount == 0:
            raise KeyError(f"unknown memory: {memory_id}")

    def add_approval_rule(self, tool_name, operation, path_scope):
        rule_id = "rule_" + uuid4().hex
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO approval_rules(id, tool_name, operation, path_scope, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tool_name, operation, path_scope) DO NOTHING
                """,
                (rule_id, str(tool_name), str(operation), str(path_scope), utc_now()),
            )
        return self._one(
            "SELECT * FROM approval_rules WHERE tool_name = ? AND operation = ? AND path_scope = ?",
            (str(tool_name), str(operation), str(path_scope)),
        )

    def list_approval_rules(self):
        return self._all("SELECT * FROM approval_rules ORDER BY created_at DESC")

    def has_approval_rule(self, tool_name, operation, path_scope):
        return self._one(
            "SELECT * FROM approval_rules WHERE tool_name = ? AND operation = ? AND path_scope = ?",
            (str(tool_name), str(operation), str(path_scope)),
        ) is not None

    def delete_approval_rule(self, rule_id):
        with self.lock, self.connection:
            cursor = self.connection.execute("DELETE FROM approval_rules WHERE id = ?", (rule_id,))
        if cursor.rowcount == 0:
            raise KeyError(f"unknown approval rule: {rule_id}")

    def _one(self, sql, params=()):
        with self.lock:
            row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _one_locked(self, sql, params=()):
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _delete_chunks_locked(self, document_id):
        if getattr(self, "fts_available", False):
            self.connection.execute("DELETE FROM documents_fts WHERE rowid IN (SELECT id FROM document_chunks WHERE document_id = ?)", (str(document_id),))
        self.connection.execute("DELETE FROM document_chunks WHERE document_id = ?", (str(document_id),))

    def _delete_document_locked(self, document_id):
        self.connection.execute(
            "UPDATE sessions SET locked_document_id = '', updated_at = ? WHERE locked_document_id = ?",
            (utc_now(), str(document_id)),
        )
        self._delete_chunks_locked(document_id)
        self.connection.execute("DELETE FROM documents WHERE id = ?", (str(document_id),))

    def _all(self, sql, params=()):
        with self.lock:
            rows = self.connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_grant(row):
        if row is None:
            return None
        row = dict(row)
        row["can_read"] = bool(row["can_read"])
        row["can_write"] = bool(row["can_write"])
        row["can_shell"] = bool(row["can_shell"])
        return row
