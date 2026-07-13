import json
import shlex
import sys
import time

import pytest

from pico import AssistantService, FakeModelClient, Pico, SessionStore, WorkspaceContext


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


def wait_for_event(service, run_id, event_type, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = service.get_events(run_id)
        for event in events:
            if event["event_type"] == event_type:
                return event
        time.sleep(0.01)
    raise AssertionError(f"event not received: {event_type}")


def test_assistant_service_runs_agent_in_background_and_replays_events(tmp_path):
    agent = build_agent(tmp_path, ["<final>Desktop ready.</final>"])
    forwarded = []
    service = AssistantService(event_handler=forwarded.append)

    started = service.start_run(agent, "Hello")
    finished = service.wait(started["run_id"], timeout=2)

    assert started["status"] in {"starting", "running"}
    assert finished["status"] == "completed"
    assert finished["answer"] == "Desktop ready."
    replayed = service.get_events(started["run_id"])
    assert replayed == forwarded
    assert replayed[0]["event_type"] == "run.started"
    assert replayed[-1]["event_type"] == "run.completed"


def test_assistant_service_resolves_pending_tool_approval(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"write_file","args":{"path":"service.txt","content":"ok"}}</tool>',
            "<final>Done.</final>",
        ],
        approval_policy="ask",
    )
    service = AssistantService()

    started = service.start_run(agent, "Write file")
    approval_event = wait_for_event(service, started["run_id"], "tool.approval_required")
    approval_id = approval_event["payload"]["approval_id"]
    service.resolve_approval(started["run_id"], approval_id, "allow_once")
    finished = service.wait(started["run_id"], timeout=2)

    assert finished["status"] == "completed"
    assert (tmp_path / "service.txt").read_text(encoding="utf-8") == "ok"


def test_assistant_service_rejects_parallel_run_for_same_session(tmp_path):
    agent = build_agent(
        tmp_path,
        ['<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"x"}}</tool>'],
        approval_policy="ask",
    )
    service = AssistantService()

    started = service.start_run(agent, "Wait for approval")
    wait_for_event(service, started["run_id"], "tool.approval_required")
    with pytest.raises(RuntimeError, match="session already has an active run"):
        service.start_run(agent, "Parallel")
    service.cancel_run(started["run_id"])
    assert service.wait(started["run_id"], timeout=2)["status"] == "cancelled"


def test_assistant_service_validates_approval_decision(tmp_path):
    agent = build_agent(tmp_path, ["<final>unused</final>"])
    service = AssistantService()
    started = service.start_run(agent, "Hello")
    service.wait(started["run_id"], timeout=2)

    with pytest.raises(ValueError, match="invalid approval decision"):
        service.resolve_approval(started["run_id"], "approval_x", "yes")


def test_assistant_service_cancels_running_shell_process(tmp_path):
    command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(10)'"
    agent = build_agent(
        tmp_path,
        [
            "<tool>"
            + json.dumps({"name": "run_shell", "args": {"command": command, "timeout": 20}})
            + "</tool>"
        ],
        approval_policy="auto",
    )
    service = AssistantService()

    started = service.start_run(agent, "Run slow command")
    wait_for_event(service, started["run_id"], "tool.started")
    cancelled_at = time.monotonic()
    service.cancel_run(started["run_id"])
    finished = service.wait(started["run_id"], timeout=3)

    assert finished["status"] == "cancelled"
    assert time.monotonic() - cancelled_at < 3


class FailingModelClient:
    supports_prompt_cache = False
    last_completion_metadata = {}

    def complete(self, prompt, max_new_tokens, **kwargs):
        del prompt, max_new_tokens, kwargs
        raise RuntimeError("provider unavailable")


def test_assistant_service_persists_model_failure_once(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.model_client = FailingModelClient()
    service = AssistantService()

    started = service.start_run(agent, "Fail cleanly")
    finished = service.wait(started["run_id"], timeout=2)

    assert finished["status"] == "failed"
    events = service.get_events(started["run_id"])
    assert [event["event_type"] for event in events].count("run.failed") == 1
    assert agent.current_task_state.status == "failed"
    assert agent.current_task_state.stop_reason == "model_error"


class CancelThenFinishClient:
    supports_prompt_cache = False
    last_completion_metadata = {}

    def __init__(self):
        self.calls = 0

    def complete_stream(self, prompt, max_new_tokens, on_delta, cancellation_token, **kwargs):
        del prompt, max_new_tokens, kwargs
        self.calls += 1
        if self.calls == 1:
            on_delta("partial")
            cancellation_token.cancel()
            cancellation_token.raise_if_cancelled()
        on_delta("<final>Recovered</final>")
        return "<final>Recovered</final>"


def test_assistant_service_can_start_new_run_after_cancellation(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.model_client = CancelThenFinishClient()
    service = AssistantService()

    first = service.start_run(agent, "Cancel first")
    assert service.wait(first["run_id"], timeout=2)["status"] == "cancelled"
    second = service.start_run(agent, "Try again")
    recovered = service.wait(second["run_id"], timeout=2)

    assert recovered["status"] == "completed"
    assert recovered["answer"] == "Recovered"
