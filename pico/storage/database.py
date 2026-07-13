"""Small SQLite index for desktop sessions, settings, grants, and memories."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DesktopDatabase:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self._migrate()

    def _migrate(self):
        with self.lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
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
                """
            )

    def upsert_session(self, session_id, title, workspace_root, created_at=None):
        timestamp = utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO sessions(id, title, workspace_root, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    workspace_root=excluded.workspace_root,
                    updated_at=excluded.updated_at
                """,
                (session_id, title, str(Path(workspace_root).resolve()), created_at or timestamp, timestamp),
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
