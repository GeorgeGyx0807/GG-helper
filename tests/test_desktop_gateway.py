import time
import json
import shlex
import sys

from fastapi.testclient import TestClient

from poppy import FakeModelClient, Poppy, SessionStore, WorkspaceContext
from poppy.api import create_gateway_app
from poppy.application.controller import DesktopController
from poppy.run_store import RunStore
from poppy.storage import AppPaths, DesktopDatabase


class FakeDesktopAgentFactory:
    def __init__(self, paths, outputs):
        self.paths = paths
        self.outputs = list(outputs)
        self.api_key_provider = lambda: "sk-configured-for-test"

    def build(self, config):
        workspace = WorkspaceContext.build(config.workspace_root)
        kwargs = {
            "model_client": FakeModelClient(self.outputs),
            "workspace": workspace,
            "session_store": SessionStore(self.paths.sessions),
            "run_store": RunStore(self.paths.runs),
            "approval_policy": config.approval_policy,
            "allowed_tools": config.allowed_tools,
            "library_searcher": config.library_searcher,
            "personal_memory_sink": config.personal_memory_sink,
        }
        if config.session_id:
            return Poppy.from_session(session_id=config.session_id, **kwargs)
        return Poppy(**kwargs)

    def test_connection(self, config):
        return {"status": "ok", "model": config.model}


def build_gateway(tmp_path, outputs):
    data_root = tmp_path / "app-data"
    paths = AppPaths(data_root).ensure()
    database = DesktopDatabase(paths.database)
    factory = FakeDesktopAgentFactory(paths, outputs)
    controller = DesktopController(paths=paths, database=database, agent_factory=factory)
    app = create_gateway_app(controller=controller, connection_token="desktop-token")
    return controller, TestClient(app), {"X-Poppy-Token": "desktop-token"}


def wait_for_terminal(client, headers, run_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        if response.json()["status"] in {"completed", "cancelled", "failed"}:
            return response.json()
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def wait_for_gateway_event(client, headers, run_id, event_type, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}/events", headers=headers)
        assert response.status_code == 200
        for event in response.json():
            if event["event_type"] == event_type:
                return event
        time.sleep(0.01)
    raise AssertionError(f"event did not arrive: {event_type}")


def test_gateway_requires_connection_token_for_every_http_route(tmp_path):
    _controller, client, headers = build_gateway(tmp_path, ["<final>ok</final>"])

    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"X-Poppy-Token": "wrong"}).status_code == 401
    assert client.get("/health", headers=headers).json() == {
        "status": "ok",
        "service": "poppy-desktop-gateway",
    }


def test_filename_mention_and_manual_document_lock_keep_retrieval_scoped(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    first = workspace / "paper-a.md"
    second = workspace / "paper-b.md"
    first.write_text("shared keyword\nA-only evidence", encoding="utf-8")
    second.write_text("shared keyword\nB-only evidence", encoding="utf-8")
    controller, client, headers = build_gateway(tmp_path, ["<final>只引用 A。</final>"])
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Papers"},
    ).json()

    documents = client.get(
        "/library/documents", headers=headers, params={"session_id": session["id"]}
    ).json()
    by_name = {item["display_name"]: item for item in documents}
    started = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "paper-a.md 里的 shared keyword 是什么？"},
    ).json()
    assert wait_for_terminal(client, headers, started["run_id"])["answer"] == "只引用 A。"
    prompt = controller._agents[session["id"]].model_client.prompts[-1]
    assert "A-only evidence" in prompt
    assert "B-only evidence" not in prompt
    detail = client.get(f"/sessions/{session['id']}", headers=headers).json()
    assert detail["locked_document"]["display_name"] == "paper-a.md"

    changed = client.patch(
        f"/sessions/{session['id']}/document-lock",
        headers=headers,
        json={"document_id": by_name["paper-b.md"]["id"]},
    )
    assert changed.status_code == 200
    assert changed.json()["locked_document"]["display_name"] == "paper-b.md"


def test_locked_document_returns_deterministic_unknown_when_evidence_is_absent(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    path = workspace / "paper.md"
    path.write_text("known evidence only", encoding="utf-8")
    controller, client, headers = build_gateway(tmp_path, ["<final>should not be used</final>"])
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Papers"},
    ).json()
    document = client.get(
        "/library/documents", headers=headers, params={"session_id": session["id"]}
    ).json()[0]
    client.patch(
        f"/sessions/{session['id']}/document-lock",
        headers=headers,
        json={"document_id": document["id"]},
    )

    started = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "火星农业产量是多少？"},
    ).json()
    finished = wait_for_terminal(client, headers, started["run_id"])
    assert "没有找到足够证据" in finished["answer"]
    assert controller._agents[session["id"]].model_client.prompts == []


def test_full_document_mode_runs_map_reduce_and_emits_coverage_progress(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    path = workspace / "whole-paper.md"
    path.write_text(
        "# Definition\nMemory is checkpointed.\n\n# Experiments\n| method | latency |\n| Poppy | 12 ms |\n",
        encoding="utf-8",
    )
    controller, client, headers = build_gateway(
        tmp_path,
        ["<final>证据：定义与实验表格均已读取。</final>", "<final>全文综合答案。</final>"],
    )
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Papers"},
    ).json()

    started = client.post(
        "/runs",
        headers=headers,
        json={
            "session_id": session["id"],
            "message": "请综合全文说明定义和实验结果",
            "document_path": str(path),
            "full_document": True,
        },
    ).json()
    finished = wait_for_terminal(client, headers, started["run_id"])
    assert finished["answer"] == "全文综合答案。"
    prompts = controller._agents[session["id"]].model_client.prompts
    assert "12 ms" in prompts[0]
    assert "证据：定义与实验表格均已读取" in prompts[1]
    progress = client.get(f"/runs/{started['run_id']}/events", headers=headers).json()
    assert any(event["event_type"] == "run.progress" for event in progress)


def test_gateway_allows_only_tauri_origins_for_browser_requests(tmp_path):
    _controller, client, _headers = build_gateway(tmp_path, ["<final>ok</final>"])
    allowed = client.options(
        "/health",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-poppy-token",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "tauri://localhost"
    assert "X-Poppy-Token" in allowed.headers["access-control-allow-headers"]

    denied = client.options(
        "/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_gateway_shutdown_route_is_authenticated_and_optional(tmp_path):
    controller, client, headers = build_gateway(tmp_path, ["<final>ok</final>"])
    assert client.post("/shutdown", headers=headers).status_code == 404

    called = []
    app = create_gateway_app(
        controller=controller,
        connection_token="desktop-token",
        shutdown_handler=lambda: called.append(True),
    )
    with TestClient(app) as shutdown_client:
        assert shutdown_client.post("/shutdown").status_code == 401
        response = shutdown_client.post("/shutdown", headers=headers)
        assert response.status_code == 202
        assert response.json() == {"status": "shutting_down"}
    assert called == [True]


def test_gateway_session_run_event_replay_and_websocket(tmp_path):
    workspace = tmp_path / "notes"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello\n", encoding="utf-8")
    _controller, client, headers = build_gateway(tmp_path, ["<final>Hello desktop.</final>"])

    grant = client.post(
        "/grants",
        headers=headers,
        json={"path": str(workspace), "can_read": True, "can_write": False, "can_shell": False},
    )
    assert grant.status_code == 201
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Notes"},
    )
    assert session.status_code == 201
    session_id = session.json()["id"]

    started = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session_id, "message": "Say hello"},
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert wait_for_terminal(client, headers, run_id)["answer"] == "Hello desktop."

    replay = client.get(f"/runs/{run_id}/events", headers=headers).json()
    assert replay[0]["event_type"] == "run.started"
    assert replay[-1]["event_type"] == "run.completed"
    assert client.get(
        f"/runs/{run_id}/events",
        headers=headers,
        params={"after_sequence": replay[-2]["sequence"]},
    ).json() == [replay[-1]]

    socket_events = []
    with client.websocket_connect(f"/events?token=desktop-token&run_id={run_id}") as socket:
        for _ in replay:
            socket_events.append(socket.receive_json())
    assert socket_events == replay


def test_gateway_blocks_session_outside_grants_and_limits_tools(tmp_path):
    workspace = tmp_path / "private"
    workspace.mkdir()
    controller, client, headers = build_gateway(tmp_path, ["<final>unused</final>"])

    denied = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Denied"},
    )
    assert denied.status_code == 403

    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    created = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Read only"},
    ).json()
    agent = controller._agents[created["id"]]
    assert set(agent.tools) == {"list_files", "read_file", "search", "library_search"}


def test_gateway_creates_unscoped_chat_session_without_project_grant(tmp_path):
    attachment = tmp_path / "reference.md"
    attachment.write_text("reference", encoding="utf-8")
    controller, client, headers = build_gateway(tmp_path, ["<final>好的，我是 Poppy。</final>"])

    response = client.post(
        "/sessions",
        headers=headers,
        json={"title": "日常问题", "session_type": "chat"},
    )
    assert response.status_code == 201
    session = response.json()
    assert session["session_type"] == "chat"
    assert session["workspace_root"] == ""
    assert controller._agents[session["id"]].tools == {}

    run = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "你是谁？", "attachments": [str(attachment)]},
    ).json()
    assert wait_for_terminal(client, headers, run["run_id"])["answer"] == "好的，我是 Poppy。"
    prompt = controller._agents[session["id"]].model_client.prompts[-1]
    assert "[Poppy retrieved document context]" in prompt
    assert "reference" in prompt
    history = client.get(f"/sessions/{session['id']}", headers=headers).json()["history"]
    user_message = next(item for item in history if item["role"] == "user")
    assert user_message["content"] == "你是谁？"
    assert "retrieved document context" not in user_message["content"]


def test_quick_context_resolves_authorized_document_and_keeps_internal_prompt_out_of_history(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    paper = workspace / "2-poppy.md"
    paper.write_text(
        "Poppy uses a layered memory architecture. Session context is restored from bounded checkpoints.\n",
        encoding="utf-8",
    )
    controller, client, headers = build_gateway(tmp_path, ["<final>它使用分层记忆和有界检查点。</final>"])
    assert client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True}).status_code == 201

    resolved = client.post(
        "/quick/context/resolve",
        headers=headers,
        json={
            "text": "layered memory architecture. Session context is restored from bounded checkpoints",
            "source_app": "Preview",
            "window_title": "2-poppy.pdf",
        },
    )
    assert resolved.status_code == 200
    context = resolved.json()
    assert context["mode"] == "document"
    assert context["document"]["display_name"] == "2-poppy.md"

    session = client.post(
        "/sessions",
        headers=headers,
        json={"title": "文献快问", "session_type": "chat"},
    ).json()
    started = client.post(
        "/runs",
        headers=headers,
        json={
            "session_id": session["id"],
            "message": "这里的 memory 是怎么设计的？",
            "quick_context_id": context["context_id"],
            "quick_intent": "explain",
        },
    )
    assert started.status_code == 202
    assert wait_for_terminal(client, headers, started.json()["run_id"])["status"] == "completed"
    prompt = controller._agents[session["id"]].model_client.prompts[-1]
    assert "[Poppy quick selection context]" in prompt
    assert "Poppy uses a layered memory architecture" in prompt
    history = client.get(f"/sessions/{session['id']}", headers=headers).json()["history"]
    user_message = next(item for item in history if item["role"] == "user")
    assert user_message["content"] == "这里的 memory 是怎么设计的？"


def test_quick_full_document_question_searches_only_the_matched_document(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    target = workspace / "target-paper.md"
    distractor = workspace / "distractor.md"
    target.write_text(
        "Anchor: layered memory keeps active session state.\n"
        + "filler\n" * 170
        + "Target evidence: eviction waits for a grace period before permanent removal.\n",
        encoding="utf-8",
    )
    distractor.write_text(
        "Distractor evidence: eviction has a grace period but this document was not selected.\n",
        encoding="utf-8",
    )
    controller, client, headers = build_gateway(tmp_path, ["<final>目标文献使用 grace period。</final>"])
    grant = client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True}).json()
    context = client.post(
        "/quick/context/resolve",
        headers=headers,
        json={
            "text": "Anchor: layered memory keeps active session state.",
            "source_app": "Preview",
            "window_title": "target-paper.pdf",
        },
    ).json()
    assert context["mode"] == "document"
    session = client.post(
        "/sessions", headers=headers, json={"title": "文献快问", "session_type": "chat"}
    ).json()
    started = client.post(
        "/runs",
        headers=headers,
        json={
            "session_id": session["id"],
            "message": "How does the eviction grace period work?",
            "quick_context_id": context["context_id"],
            "quick_intent": "ask",
        },
    )
    assert started.status_code == 202
    assert wait_for_terminal(client, headers, started.json()["run_id"])["status"] == "completed"
    prompt = controller._agents[session["id"]].model_client.prompts[-1]
    assert "Target evidence: eviction waits for a grace period" in prompt
    assert "Distractor evidence" not in prompt

    client.delete(f"/grants/{grant['id']}", headers=headers)


def test_quick_context_forbids_path_forgery_and_revocation_removes_document_context(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    paper = workspace / "private-paper.md"
    paper.write_text(
        "Selected anchor about layered memory.\nSecret neighboring evidence must disappear after revocation.\n",
        encoding="utf-8",
    )
    controller, client, headers = build_gateway(tmp_path, ["<final>仅根据选区回答。</final>"])
    grant = client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True}).json()
    forged = client.post(
        "/quick/context/resolve",
        headers=headers,
        json={"text": "Selected anchor about layered memory.", "path": "/etc/passwd"},
    )
    assert forged.status_code == 422
    context = client.post(
        "/quick/context/resolve",
        headers=headers,
        json={"text": "Selected anchor about layered memory.", "window_title": paper.name},
    ).json()
    assert context["mode"] == "document"
    assert client.delete(f"/grants/{grant['id']}", headers=headers).status_code == 204
    session = client.post(
        "/sessions", headers=headers, json={"title": "文献快问", "session_type": "chat"}
    ).json()
    started = client.post(
        "/runs",
        headers=headers,
        json={
            "session_id": session["id"],
            "message": "解释选区",
            "quick_context_id": context["context_id"],
            "quick_intent": "explain",
        },
    )
    assert started.status_code == 202
    assert wait_for_terminal(client, headers, started.json()["run_id"])["status"] == "completed"
    prompt = controller._agents[session["id"]].model_client.prompts[-1]
    assert "Selected anchor about layered memory" in prompt
    assert "Secret neighboring evidence" not in prompt


def test_all_quick_intents_reuse_run_and_stream_event_contract(tmp_path):
    workspace = tmp_path / "papers"
    workspace.mkdir()
    (workspace / "paper.md").write_text(
        "Layered memory restores session context from bounded checkpoints.\n",
        encoding="utf-8",
    )
    controller, client, headers = build_gateway(
        tmp_path,
        [
            "<final>翻译完成。</final>",
            "<final>解释完成。</final>",
            "<final>总结完成。</final>",
            "<final>问答完成。</final>",
        ],
    )
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    context = client.post(
        "/quick/context/resolve",
        headers=headers,
        json={
            "text": "Layered memory restores session context from bounded checkpoints.",
            "window_title": "paper.pdf",
        },
    ).json()
    session = client.post(
        "/sessions", headers=headers, json={"title": "文献快问", "session_type": "chat"}
    ).json()
    requirements = {
        "translate": "忠实翻译",
        "explain": "先用一句话",
        "summarize": "概括选区",
        "ask": "直接回答用户问题",
    }
    for intent, requirement in requirements.items():
        started = client.post(
            "/runs",
            headers=headers,
            json={
                "session_id": session["id"],
                "message": f"执行 {intent}",
                "quick_context_id": context["context_id"],
                "quick_intent": intent,
            },
        )
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        assert wait_for_terminal(client, headers, run_id)["status"] == "completed"
        events = client.get(f"/runs/{run_id}/events", headers=headers).json()
        assert events[0]["event_type"] == "run.started"
        assert events[-1]["event_type"] == "run.completed"
        assert requirement in controller._agents[session["id"]].model_client.prompts[-1]


def test_quick_context_rejects_unknown_or_expired_identifier(tmp_path):
    _controller, client, headers = build_gateway(tmp_path, ["<final>unused</final>"])
    session = client.post(
        "/sessions",
        headers=headers,
        json={"title": "文献快问", "session_type": "chat"},
    ).json()
    response = client.post(
        "/runs",
        headers=headers,
        json={
            "session_id": session["id"],
            "message": "解释",
            "quick_context_id": "quick_missing",
            "quick_intent": "explain",
        },
    )
    assert response.status_code == 422
    assert "expired" in response.json()["detail"]


def test_revoking_a_grant_blocks_new_runs_but_preserves_saved_history(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _controller, client, headers = build_gateway(tmp_path, ["<final>Saved.</final>"])
    grant = client.post(
        "/grants", headers=headers, json={"path": str(workspace), "can_read": True}
    ).json()
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Revoked"},
    ).json()
    run = client.post(
        "/runs", headers=headers, json={"session_id": session["id"], "message": "Save this"}
    ).json()
    wait_for_terminal(client, headers, run["run_id"])

    assert client.delete(f"/grants/{grant['id']}", headers=headers).status_code == 204
    restored = client.get(f"/sessions/{session['id']}", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["history"][-1]["content"] == "Saved."
    denied = client.post(
        "/runs", headers=headers, json={"session_id": session["id"], "message": "Try again"}
    )
    assert denied.status_code == 403


def test_downgrading_a_grant_rebuilds_agent_without_write_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outputs = [
        '<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"no"}}</tool>',
        "<final>Write was unavailable.</final>",
    ]
    controller, client, headers = build_gateway(tmp_path, outputs)
    client.post(
        "/grants",
        headers=headers,
        json={"path": str(workspace), "can_read": True, "can_write": True},
    )
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Downgrade"},
    ).json()
    client.post(
        "/grants",
        headers=headers,
        json={"path": str(workspace), "can_read": True, "can_write": False},
    )

    run = client.post(
        "/runs", headers=headers, json={"session_id": session["id"], "message": "Write"}
    ).json()
    assert wait_for_terminal(client, headers, run["run_id"])["status"] == "completed"
    assert not (workspace / "blocked.txt").exists()
    assert set(controller._agents[session["id"]].tools) == {
        "list_files", "read_file", "search", "library_search"
    }


def test_gateway_renames_and_restores_session_history(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller, client, headers = build_gateway(tmp_path, ["<final>Remember me.</final>"])
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Original"},
    ).json()
    renamed = client.patch(
        f"/sessions/{session['id']}",
        headers=headers,
        json={"title": "Renamed"},
    )
    assert renamed.json()["title"] == "Renamed"
    run = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "Remember"},
    ).json()
    wait_for_terminal(client, headers, run["run_id"])

    controller._agents.clear()
    restored = client.get(f"/sessions/{session['id']}", headers=headers).json()
    assert restored["title"] == "Renamed"
    assert [item["role"] for item in restored["history"]][-2:] == ["user", "assistant"]


def test_gateway_deletes_session_and_saved_history(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _controller, client, headers = build_gateway(tmp_path, ["<final>unused</final>"])
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "待删除"},
    ).json()

    deleted = client.delete(f"/sessions/{session['id']}", headers=headers)

    assert deleted.status_code == 204
    assert client.get(f"/sessions/{session['id']}", headers=headers).status_code == 404
    assert all(item["id"] != session["id"] for item in client.get("/sessions", headers=headers).json())


def test_gateway_settings_grants_and_memory_crud(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _controller, client, headers = build_gateway(tmp_path, ["<final>unused</final>"])

    settings = client.patch(
        "/settings",
        headers=headers,
        json={"model": "deepseek-custom", "timeout": 120},
    ).json()
    assert settings["model"] == "deepseek-custom"
    assert settings["timeout"] == 120
    assert settings["api_key_configured"] is True
    assert client.post("/settings/test-connection", headers=headers).json() == {
        "status": "ok",
        "model": "deepseek-custom",
    }

    grant = client.post(
        "/grants",
        headers=headers,
        json={"path": str(workspace), "can_read": True, "can_write": True},
    ).json()
    assert client.get("/grants", headers=headers).json()[0]["path"] == str(workspace.resolve())

    memory = client.post(
        "/memories",
        headers=headers,
        json={"category": "preference", "content": "Use Chinese"},
    ).json()
    updated = client.patch(
        f"/memories/{memory['id']}",
        headers=headers,
        json={"content": "Use concise Chinese"},
    ).json()
    assert updated["content"] == "Use concise Chinese"
    assert len(client.get("/memories", headers=headers).json()) == 1
    assert client.delete(f"/memories/{memory['id']}", headers=headers).status_code == 204
    rejected_secret = client.post(
        "/memories",
        headers=headers,
        json={"category": "preference", "content": "api key sk-do-not-store"},
    )
    assert rejected_secret.status_code == 422
    assert client.delete(f"/grants/{grant['id']}", headers=headers).status_code == 204


def test_gateway_personal_memory_is_injected_into_model_context(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller, client, headers = build_gateway(tmp_path, ["<final>好的。</final>"])
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    client.post(
        "/memories",
        headers=headers,
        json={"category": "preference", "content": "回答尽量简洁"},
    )
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Memory"},
    ).json()
    run = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "你好"},
    ).json()
    wait_for_terminal(client, headers, run["run_id"])

    prompt = controller._agents[session["id"]].model_client.prompts[-1]
    assert "Personal memory" in prompt
    assert "回答尽量简洁" in prompt


def test_gateway_attachment_must_stay_in_authorized_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attachment = workspace / "notes.md"
    attachment.write_text("hello", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    controller, client, headers = build_gateway(tmp_path, ["<final>Read it.</final>"])
    client.post("/grants", headers=headers, json={"path": str(workspace), "can_read": True})
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Attachments"},
    ).json()

    denied = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "Read", "attachments": [str(outside)]},
    )
    assert denied.status_code == 403

    run = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "Read", "attachments": [str(attachment)]},
    ).json()
    wait_for_terminal(client, headers, run["run_id"])
    assert str(attachment.resolve()) in controller._agents[session["id"]].model_client.prompts[-1]
    restored = client.get(f"/sessions/{session['id']}", headers=headers).json()
    user_message = next(item for item in restored["history"] if item["role"] == "user")
    assert user_message["content"] == "Read"
    assert user_message["attachments"] == [str(attachment.resolve())]


def test_always_allow_rule_is_exact_path_scoped_and_revocable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outputs = [
        '<tool>{"name":"write_file","args":{"path":"allowed.txt","content":"one"}}</tool>',
        "<final>First.</final>",
        '<tool>{"name":"write_file","args":{"path":"allowed.txt","content":"two"}}</tool>',
        "<final>Second.</final>",
    ]
    _controller, client, headers = build_gateway(tmp_path, outputs)
    client.post(
        "/grants",
        headers=headers,
        json={"path": str(workspace), "can_read": True, "can_write": True},
    )
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Rules"},
    ).json()

    first = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "First write"},
    ).json()
    approval = wait_for_gateway_event(client, headers, first["run_id"], "tool.approval_required")
    client.post(
        f"/runs/{first['run_id']}/approvals/{approval['payload']['approval_id']}",
        headers=headers,
        json={"decision": "allow_always"},
    )
    assert wait_for_terminal(client, headers, first["run_id"])["status"] == "completed"
    rules = client.get("/approval-rules", headers=headers).json()
    assert len(rules) == 1
    assert rules[0]["tool_name"] == "write_file"
    assert rules[0]["path_scope"] == str((workspace / "allowed.txt").resolve())

    second = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "Second write"},
    ).json()
    assert wait_for_terminal(client, headers, second["run_id"])["status"] == "completed"
    events = client.get(f"/runs/{second['run_id']}/events", headers=headers).json()
    assert "tool.approval_required" not in [event["event_type"] for event in events]
    assert (workspace / "allowed.txt").read_text(encoding="utf-8") == "two"

    assert client.delete(f"/approval-rules/{rules[0]['id']}", headers=headers).status_code == 204
    assert client.get("/approval-rules", headers=headers).json() == []


def test_gateway_approval_and_cancel_endpoints_control_active_runs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    slow_command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(10)'"
    outputs = [
        '<tool>{"name":"write_file","args":{"path":"approved.txt","content":"yes"}}</tool>',
        "<final>Written.</final>",
        "<tool>" + json.dumps({"name": "run_shell", "args": {"command": slow_command, "timeout": 20}}) + "</tool>",
    ]
    _controller, client, headers = build_gateway(tmp_path, outputs)
    client.post(
        "/grants",
        headers=headers,
        json={"path": str(workspace), "can_read": True, "can_write": True, "can_shell": True},
    )
    session = client.post(
        "/sessions",
        headers=headers,
        json={"workspace_root": str(workspace), "title": "Actions"},
    ).json()

    write_run = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "Write"},
    ).json()
    approval = wait_for_gateway_event(client, headers, write_run["run_id"], "tool.approval_required")
    approval_id = approval["payload"]["approval_id"]
    allowed = client.post(
        f"/runs/{write_run['run_id']}/approvals/{approval_id}",
        headers=headers,
        json={"decision": "allow_once"},
    )
    assert allowed.status_code == 200
    assert wait_for_terminal(client, headers, write_run["run_id"])["status"] == "completed"
    assert (workspace / "approved.txt").read_text(encoding="utf-8") == "yes"

    shell_run = client.post(
        "/runs",
        headers=headers,
        json={"session_id": session["id"], "message": "Run slowly"},
    ).json()
    shell_approval = wait_for_gateway_event(client, headers, shell_run["run_id"], "tool.approval_required")
    client.post(
        f"/runs/{shell_run['run_id']}/approvals/{shell_approval['payload']['approval_id']}",
        headers=headers,
        json={"decision": "allow_once"},
    )
    wait_for_gateway_event(client, headers, shell_run["run_id"], "tool.started")
    cancelled = client.post(f"/runs/{shell_run['run_id']}/cancel", headers=headers)
    assert cancelled.status_code == 202
    assert wait_for_terminal(client, headers, shell_run["run_id"], timeout=3)["status"] == "cancelled"


def test_reader_run_locks_retrieval_to_requested_document(tmp_path):
    library = tmp_path / "papers"
    library.mkdir()
    target = library / "target.txt"
    distractor = library / "distractor.txt"
    target.write_text("Shared topic. TARGET_ONLY evidence about bounded memory.\n", encoding="utf-8")
    distractor.write_text("Shared topic. DISTRACTOR_ONLY unrelated evidence.\n", encoding="utf-8")
    controller, client, headers = build_gateway(tmp_path, ["<final>reader answer</final>"])

    assert client.post(
        "/grants",
        headers=headers,
        json={"path": str(library), "can_read": True, "can_write": False, "can_shell": False},
    ).status_code == 201
    assert client.post(
        "/library/sources",
        headers=headers,
        json={"path": str(library)},
    ).status_code == 201
    session = client.post(
        "/sessions",
        headers=headers,
        json={"title": "阅读 target.txt", "session_type": "chat"},
    ).json()

    started = client.post(
        "/runs",
        headers=headers,
        json={
            "session_id": session["id"],
            "message": "What does the shared topic say?",
            "attachments": [str(target)],
            "document_path": str(target),
        },
    )
    assert started.status_code == 202
    assert wait_for_terminal(client, headers, started.json()["run_id"])["status"] == "completed"

    prompt = controller._agents[session["id"]].model_client.prompts[0]
    assert "TARGET_ONLY" in prompt
    assert "DISTRACTOR_ONLY" not in prompt
