"""Desktop use cases shared by HTTP routes and future native commands."""

from pathlib import Path
from threading import Lock

from ..runtime import SECRET_SHAPED_TEXT_PATTERN
from ..session_store import SessionStore
from ..storage import AppPaths, DesktopDatabase
from .factory import DesktopAgentConfig, DesktopAgentFactory
from .service import AssistantService


READ_TOOLS = ("list_files", "read_file", "search", "delegate")
WRITE_TOOLS = ("write_file", "patch_file")
SHELL_TOOLS = ("run_shell",)
ATTACHMENT_MARKER = "\n\n[Poppy attached files]\n"


class DesktopController:
    def __init__(self, paths=None, database=None, agent_factory=None, event_handler=None):
        self.paths = (paths or AppPaths.default()).ensure()
        self.database = database or DesktopDatabase(self.paths.database)
        self.agent_factory = agent_factory or DesktopAgentFactory(self.paths)
        self.service = AssistantService(
            event_handler=event_handler,
            approval_rule_checker=self._approval_rule_allows,
            approval_rule_saver=self._save_approval_rule,
        )
        self._agents = {}
        self._agent_signatures = {}
        self._lock = Lock()

    def list_sessions(self):
        return self.database.list_sessions()

    def create_session(self, workspace_root="", title="新对话", session_type="project"):
        session_type = self._normalize_session_type(session_type)
        settings = self.settings()
        if session_type == "chat":
            workspace_root = ""
            grant = None
            agent = self._build_agent(str(self.paths.root), grant, settings=settings, allowed_tools=())
        else:
            grant = self._require_grant(workspace_root)
            agent = self._build_agent(workspace_root, grant, settings=settings)
        with self._lock:
            self._agents[agent.session["id"]] = agent
            self._agent_signatures[agent.session["id"]] = self._configuration_signature(
                workspace_root or str(self.paths.root), grant, settings, session_type
            )
        return self.database.upsert_session(
            agent.session["id"],
            str(title).strip() or "新对话",
            workspace_root,
            created_at=agent.session.get("created_at"),
            session_type=session_type,
        )

    def get_session(self, session_id):
        item = self.database.get_session(session_id)
        if item is None:
            raise KeyError(f"unknown session: {session_id}")
        with self._lock:
            cached = self._agents.get(session_id)
        session = cached.session if cached is not None else SessionStore(self.paths.sessions).load(session_id)
        return {**item, "history": self._desktop_history(session.get("history", []))}

    def rename_session(self, session_id, title):
        title = str(title).strip()
        if not title:
            raise ValueError("session title must not be empty")
        return self.database.rename_session(session_id, title)

    def delete_session(self, session_id):
        if self.database.get_session(session_id) is None:
            raise KeyError(f"unknown session: {session_id}")
        if self.service.has_active_session(session_id):
            raise RuntimeError("cannot delete a session while it is running")
        with self._lock:
            self._agents.pop(session_id, None)
            self._agent_signatures.pop(session_id, None)
        self.database.delete_session(session_id)
        SessionStore(self.paths.sessions).path(session_id).unlink(missing_ok=True)

    def start_run(self, session_id, message, attachments=None):
        item = self.database.get_session(session_id)
        if item is None:
            raise KeyError(f"unknown session: {session_id}")
        message = str(message).strip()
        if not message:
            raise ValueError("message must not be empty")
        attachment_paths = self._validate_attachments(
            item["workspace_root"], attachments or [], unrestricted=item.get("session_type") == "chat"
        )
        agent_message = message
        if attachment_paths:
            agent_message += ATTACHMENT_MARKER + "\n".join(f"- {path}" for path in attachment_paths)
        return self.service.start_run(self._get_agent(session_id, item), agent_message)

    def get_run(self, run_id):
        return self.service.get_run(run_id)

    def get_events(self, run_id, after_sequence=0):
        return self.service.get_events(run_id, after_sequence=after_sequence)

    def cancel_run(self, run_id):
        return self.service.cancel_run(run_id)

    def resolve_approval(self, run_id, approval_id, decision):
        return self.service.resolve_approval(run_id, approval_id, decision)

    def settings(self):
        settings = {
            "model": "deepseek-v4-pro",
            "base_url": "https://api.deepseek.com/anthropic",
            "timeout": 300,
        }
        settings.update(self.database.get_settings())
        settings["api_key_configured"] = bool(self.agent_factory.api_key_provider())
        return settings

    def update_settings(self, values):
        allowed = {"model", "base_url", "timeout", "max_steps", "max_new_tokens"}
        for key, value in values.items():
            if key in allowed:
                self.database.set_setting(key, value)
        return self.settings()

    def test_model_connection(self):
        settings = self.settings()
        config = DesktopAgentConfig(
            workspace_root=str(self.paths.root),
            model=settings["model"],
            base_url=settings["base_url"],
            timeout=int(settings["timeout"]),
            max_steps=int(settings.get("max_steps", 6)),
            max_new_tokens=int(settings.get("max_new_tokens", 512)),
        )
        return self.agent_factory.test_connection(config)

    def add_grant(self, path, can_read=True, can_write=False, can_shell=False):
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError("grant path must be an existing directory")
        if not can_read:
            raise ValueError("desktop workspace grants must allow reading")
        if can_shell and not can_write:
            raise ValueError("shell access requires write access")
        return self.database.add_grant(resolved, can_read, can_write, can_shell)

    def list_grants(self):
        return self.database.list_grants()

    def delete_grant(self, grant_id):
        self.database.delete_grant(grant_id)

    def list_memories(self):
        return self.database.list_memories()

    def add_memory(self, category, content, source_session_id=""):
        content = str(content).strip()
        if not content:
            raise ValueError("memory content must not be empty")
        if SECRET_SHAPED_TEXT_PATTERN.search(content):
            raise ValueError("memory content looks like a secret and was not saved")
        return self.database.add_memory(str(category).strip() or "preference", content, source_session_id)

    def update_memory(self, memory_id, content):
        content = str(content).strip()
        if not content:
            raise ValueError("memory content must not be empty")
        if SECRET_SHAPED_TEXT_PATTERN.search(content):
            raise ValueError("memory content looks like a secret and was not saved")
        return self.database.update_memory(memory_id, content)

    def delete_memory(self, memory_id):
        self.database.delete_memory(memory_id)

    def list_approval_rules(self):
        return self.database.list_approval_rules()

    def delete_approval_rule(self, rule_id):
        self.database.delete_approval_rule(rule_id)

    def shutdown(self):
        self.service.shutdown()

    def _get_agent(self, session_id, item):
        session_type = self._normalize_session_type(item.get("session_type", "project"))
        if session_type == "chat":
            workspace_root = str(self.paths.root)
            grant = None
            allowed_tools = ()
        else:
            workspace_root = item["workspace_root"]
            grant = self._require_grant(workspace_root)
            allowed_tools = None
        settings = self.settings()
        signature = self._configuration_signature(workspace_root, grant, settings, session_type)
        with self._lock:
            cached = self._agents.get(session_id)
            cached_signature = self._agent_signatures.get(session_id)
        if cached is not None and cached_signature == signature:
            return cached
        agent = self._build_agent(
            workspace_root, grant, session_id=session_id, settings=settings, allowed_tools=allowed_tools
        )
        with self._lock:
            self._agents[session_id] = agent
            self._agent_signatures[session_id] = signature
        return agent

    def _build_agent(self, workspace_root, grant=None, session_id="", settings=None, allowed_tools=None):
        settings = settings or self.settings()
        if allowed_tools is None:
            tools = list(READ_TOOLS)
            if grant and grant["can_write"]:
                tools.extend(WRITE_TOOLS)
            if grant and grant["can_shell"]:
                tools.extend(SHELL_TOOLS)
            allowed_tools = tuple(tools)
        config = DesktopAgentConfig(
            workspace_root=str(Path(workspace_root).resolve()),
            session_id=session_id,
            model=settings["model"],
            base_url=settings["base_url"],
            timeout=int(settings["timeout"]),
            max_steps=int(settings.get("max_steps", 6)),
            max_new_tokens=int(settings.get("max_new_tokens", 512)),
            allowed_tools=tuple(allowed_tools),
        )
        agent = self.agent_factory.build(config)
        agent.personal_memory_provider = self._personal_memory_text
        return agent

    @staticmethod
    def _configuration_signature(workspace_root, grant, settings, session_type="project"):
        return (
            str(Path(workspace_root).expanduser().resolve()),
            str(session_type),
            bool(grant and grant["can_read"]),
            bool(grant and grant["can_write"]),
            bool(grant and grant["can_shell"]),
            str(settings["model"]),
            str(settings["base_url"]),
            int(settings["timeout"]),
            int(settings.get("max_steps", 6)),
            int(settings.get("max_new_tokens", 512)),
        )

    def _personal_memory_text(self):
        rows = self.database.list_memories()
        return "\n".join(f"- [{item['category']}] {item['content']}" for item in rows) or "- none"

    @staticmethod
    def _approval_descriptor(request):
        tool_name = str(request.get("tool_name", ""))
        if tool_name not in WRITE_TOOLS:
            return None
        workspace_root = Path(str(request.get("workspace_root", ""))).expanduser().resolve()
        raw_path = str((request.get("arguments") or {}).get("path", "")).strip()
        if not raw_path:
            return None
        candidate = Path(raw_path).expanduser()
        target = (candidate if candidate.is_absolute() else workspace_root / candidate).resolve()
        if not target.is_relative_to(workspace_root):
            return None
        return tool_name, "write", str(target)

    def _approval_rule_allows(self, request):
        descriptor = self._approval_descriptor(request)
        return bool(descriptor and self.database.has_approval_rule(*descriptor))

    def _save_approval_rule(self, request):
        descriptor = self._approval_descriptor(request)
        if descriptor is None:
            return False
        self.database.add_approval_rule(*descriptor)
        return True

    @staticmethod
    def _normalize_session_type(session_type):
        normalized = str(session_type or "project").strip().lower()
        if normalized not in {"project", "chat"}:
            raise ValueError("session_type must be project or chat")
        return normalized

    def _require_grant(self, workspace_root):
        resolved = Path(workspace_root).expanduser().resolve()
        grant = self.database.get_grant_by_path(resolved)
        if grant is None or not grant["can_read"]:
            raise PermissionError(f"workspace is not authorized: {resolved}")
        return grant

    @staticmethod
    def _validate_attachments(workspace_root, attachments, unrestricted=False):
        root = Path(workspace_root).expanduser().resolve() if workspace_root else None
        validated = []
        for raw_path in attachments:
            path = Path(str(raw_path)).expanduser().resolve()
            if not path.exists() or not (path.is_file() or path.is_dir()):
                raise ValueError(f"attachment is not an existing file or folder: {path}")
            if not unrestricted and (root is None or not path.is_relative_to(root)):
                raise PermissionError(f"attachment is outside the authorized workspace: {path}")
            validated.append(str(path))
        return validated

    @staticmethod
    def _desktop_history(history):
        rendered = []
        for raw_item in history:
            item = dict(raw_item)
            if item.get("role") == "user" and ATTACHMENT_MARKER in str(item.get("content", "")):
                content, attached = str(item["content"]).split(ATTACHMENT_MARKER, 1)
                item["content"] = content
                item["attachments"] = [
                    line[2:].strip()
                    for line in attached.splitlines()
                    if line.startswith("- ") and line[2:].strip()
                ]
            rendered.append(item)
        return rendered
