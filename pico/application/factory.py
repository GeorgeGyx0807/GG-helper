"""Desktop-specific Pico assembly that does not depend on CLI arguments."""

import os
from dataclasses import dataclass

from ..providers.clients import AnthropicCompatibleModelClient
from ..run_store import RunStore
from ..runtime import Pico
from ..session_store import SessionStore
from ..storage.paths import AppPaths
from ..workspace import WorkspaceContext


DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"


@dataclass(frozen=True)
class DesktopAgentConfig:
    workspace_root: str
    session_id: str = ""
    api_key: str = ""
    model: str = DEFAULT_DEEPSEEK_MODEL
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    temperature: float = 0.2
    timeout: int = 300
    max_steps: int = 6
    max_new_tokens: int = 512
    approval_policy: str = "ask"
    allowed_tools: tuple = ()


class DesktopAgentFactory:
    def __init__(self, paths=None, api_key_provider=None):
        self.paths = (paths or AppPaths.default()).ensure()
        self.api_key_provider = api_key_provider or (lambda: os.environ.get("PICO_DEEPSEEK_API_KEY", ""))

    def build(self, config):
        workspace = WorkspaceContext.build(config.workspace_root)
        model_client = self._model_client(config)
        session_store = SessionStore(self.paths.sessions)
        kwargs = {
            "model_client": model_client,
            "workspace": workspace,
            "session_store": session_store,
            "run_store": RunStore(self.paths.runs),
            "approval_policy": config.approval_policy,
            "max_steps": config.max_steps,
            "max_new_tokens": config.max_new_tokens,
            "secret_env_names": ("PICO_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
            "allowed_tools": config.allowed_tools or None,
        }
        if config.session_id:
            return Pico.from_session(session_id=config.session_id, **kwargs)
        return Pico(**kwargs)

    def test_connection(self, config):
        if not (config.api_key or self.api_key_provider()):
            raise ValueError("DeepSeek API key is not configured")
        client = self._model_client(config)
        client.complete("Reply with OK only.", max_new_tokens=64)
        return {"status": "ok", "model": config.model}

    def _model_client(self, config):
        return AnthropicCompatibleModelClient(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key or self.api_key_provider(),
            temperature=config.temperature,
            timeout=config.timeout,
        )
