"""Desktop use cases shared by HTTP routes and future native commands."""

from pathlib import Path
from threading import Lock

from ..runtime import SECRET_SHAPED_TEXT_PATTERN
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
        self._lock = Lock()

    def list_sessions(self):
        return self.database.list_sessions()

    def create_session(self, workspace_root, title="New conversation"):
        grant = self._require_grant(workspace_root)
        agent = self._build_agent(workspace_root, grant)
        with self._lock:
            self._agents[agent.session["id"]] = agent
        return self.database.upsert_session(
            agent.session["id"],
            str(title).strip() or "New conversation",
            workspace_root,
            created_at=agent.session.get("created_at"),
        )

    def get_session(self, session_id):
        item = self.database.get_session(session_id)
        if item is None:
            raise KeyError(f"unknown session: {session_id}")
        agent = self._get_agent(session_id, item)
        return {**item, "history": self._desktop_history(agent.session.get("history", []))}

    def rename_session(self, session_id, title):
        title = str(title).strip()
        if not title:
            raise ValueError("session title must not be empty")
        return self.database.rename_session(session_id, title)

    def start_run(self, session_id, message, attachments=None):
        item = self.database.get_session(session_id)
        if item is None:
            raise KeyError(f"unknown session: {session_id}")
        message = str(message).strip()
        if not message:
            raise ValueError("message must not be empty")
        attachment_paths = self._validate_attachments(item["workspace_root"], attachments or [])
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
        with self._lock:
            cached = self._agents.get(session_id)
        if cached is not None:
            return cached
        grant = self._require_grant(item["workspace_root"])
        agent = self._build_agent(item["workspace_root"], grant, session_id=session_id)
        with self._lock:
            self._agents[session_id] = agent
        return agent

    def _build_agent(self, workspace_root, grant, session_id=""):
        settings = self.settings()
        tools = list(READ_TOOLS)
        if grant["can_write"]:
            tools.extend(WRITE_TOOLS)
        if grant["can_shell"]:
            tools.extend(SHELL_TOOLS)
        config = DesktopAgentConfig(
            workspace_root=str(Path(workspace_root).resolve()),
            session_id=session_id,
            model=settings["model"],
            base_url=settings["base_url"],
            timeout=int(settings["timeout"]),
            max_steps=int(settings.get("max_steps", 6)),
            max_new_tokens=int(settings.get("max_new_tokens", 512)),
            allowed_tools=tuple(tools),
        )
        agent = self.agent_factory.build(config)
        agent.personal_memory_provider = self._personal_memory_text
        return agent

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

    def _require_grant(self, workspace_root):
        resolved = Path(workspace_root).expanduser().resolve()
        grant = self.database.get_grant_by_path(resolved)
        if grant is None or not grant["can_read"]:
            raise PermissionError(f"workspace is not authorized: {resolved}")
        return grant

    @staticmethod
    def _validate_attachments(workspace_root, attachments):
        root = Path(workspace_root).expanduser().resolve()
        validated = []
        for raw_path in attachments:
            path = Path(str(raw_path)).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"attachment is not a file: {path}")
            if not path.is_relative_to(root):
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
