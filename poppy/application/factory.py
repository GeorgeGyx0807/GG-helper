"""Desktop-specific Poppy assembly that does not depend on CLI arguments."""

import os
from dataclasses import dataclass

from ..providers.clients import AnthropicCompatibleModelClient
from ..run_store import RunStore
from ..runtime import Poppy
from ..session_store import SessionStore
from ..storage.paths import AppPaths
from ..workspace import WorkspaceContext


DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"


def credential_provider_for_url(base_url):
    value = str(base_url or "").lower()
    if "dashscope.aliyuncs.com" in value or ".maas.aliyuncs.com" in value:
        return "dashscope"
    return "deepseek"


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
    allowed_tools: tuple | None = None
    library_searcher: object = None
    personal_memory_sink: object = None


class DesktopAgentFactory:
    def __init__(self, paths=None, api_key_provider=None):
        self.paths = (paths or AppPaths.default()).ensure()
        self.api_key_provider = api_key_provider or self._environment_api_key

    @staticmethod
    def _environment_api_key(base_url=""):
        provider = credential_provider_for_url(base_url)
        name = "POPPY_DASHSCOPE_API_KEY" if provider == "dashscope" else "POPPY_DEEPSEEK_API_KEY"
        return os.environ.get(name, "")

    def api_key_for(self, config):
        if config.api_key:
            return config.api_key
        try:
            return self.api_key_provider(config.base_url)
        except TypeError:
            # Keep compatibility with injected/test providers that predate
            # provider-specific desktop credentials.
            return self.api_key_provider()

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
            "secret_env_names": (
                "POPPY_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY",
                "POPPY_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY",
            ),
            "allowed_tools": config.allowed_tools,
            "library_searcher": config.library_searcher,
            "personal_memory_sink": config.personal_memory_sink,
        }
        if config.session_id:
            return Poppy.from_session(session_id=config.session_id, **kwargs)
        return Poppy(**kwargs)

    def test_connection(self, config):
        if not self.api_key_for(config):
            raise ValueError("模型 API key is not configured")
        client = self._model_client(config)
        client.complete("Reply with OK only.", max_new_tokens=64)
        return {"status": "ok", "model": config.model}

    def _model_client(self, config):
        return AnthropicCompatibleModelClient(
            model=config.model,
            base_url=config.base_url,
            api_key=self.api_key_for(config),
            temperature=config.temperature,
            timeout=config.timeout,
        )
