import time
import json
import shlex
import sys

from fastapi.testclient import TestClient

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.api import create_gateway_app
from pico.application.controller import DesktopController
from pico.run_store import RunStore
from pico.storage import AppPaths, DesktopDatabase


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
        }
        if config.session_id:
            return Pico.from_session(session_id=config.session_id, **kwargs)
        return Pico(**kwargs)


def build_gateway(tmp_path, outputs):
    data_root = tmp_path / "app-data"
    paths = AppPaths(data_root).ensure()
    database = DesktopDatabase(paths.database)
    factory = FakeDesktopAgentFactory(paths, outputs)
    controller = DesktopController(paths=paths, database=database, agent_factory=factory)
    app = create_gateway_app(controller=controller, connection_token="desktop-token")
    return controller, TestClient(app), {"X-Pico-Token": "desktop-token"}


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
    assert client.get("/health", headers={"X-Pico-Token": "wrong"}).status_code == 401
    assert client.get("/health", headers=headers).json() == {
        "status": "ok",
        "service": "poppy-desktop-gateway",
    }


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
    assert set(agent.tools) == {"list_files", "read_file", "search", "delegate"}


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
