import json
from threading import Event, Thread
from unittest.mock import patch

import pytest

from pico import CancellationToken, FakeModelClient, Pico, RunCancelled, SessionStore, WorkspaceContext
from pico.providers.clients import AnthropicCompatibleModelClient


def build_agent(tmp_path, model_client, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=model_client,
        workspace=workspace,
        session_store=store,
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


class StreamingModelClient:
    supports_prompt_cache = False
    last_completion_metadata = {}

    def complete_stream(self, prompt, max_new_tokens, on_delta, cancellation_token, **kwargs):
        del prompt, max_new_tokens, kwargs
        cancellation_token.raise_if_cancelled()
        on_delta("<final>")
        on_delta("Hello")
        on_delta("</final>")
        return "<final>Hello</final>"


def test_runtime_emits_ordered_stream_and_completion_events(tmp_path):
    events = []
    agent = build_agent(tmp_path, StreamingModelClient(), event_handler=events.append)

    answer = agent.ask("Say hello", run_id="run_desktop_test")

    assert answer == "Hello"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert all(event["run_id"] == "run_desktop_test" for event in events)
    assert [event["event_type"] for event in events] == [
        "run.started",
        "model.started",
        "message.delta",
        "message.completed",
        "run.completed",
    ]
    assert events[2]["payload"]["delta"] == "Hello"
    assert events[-2]["payload"]["content"] == "Hello"


def test_runtime_uses_injected_approval_handler_and_emits_tool_events(tmp_path):
    events = []
    approvals = []
    agent = build_agent(
        tmp_path,
        FakeModelClient(
            [
                '<tool>{"name":"write_file","args":{"path":"approved.txt","content":"ok"}}</tool>',
                "<final>Done.</final>",
            ]
        ),
        approval_policy="ask",
        event_handler=events.append,
        approval_handler=lambda request: approvals.append(request) or True,
    )

    assert agent.ask("Write it") == "Done."

    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok"
    assert approvals[0]["tool_name"] == "write_file"
    assert approvals[0]["approval_id"].startswith("approval_")
    event_types = [event["event_type"] for event in events]
    assert event_types.index("tool.requested") < event_types.index("tool.approval_required")
    assert event_types.index("tool.approval_required") < event_types.index("tool.started")
    assert event_types.index("tool.started") < event_types.index("tool.completed")


class CancellingStreamingModelClient:
    supports_prompt_cache = False
    last_completion_metadata = {}

    def complete_stream(self, prompt, max_new_tokens, on_delta, cancellation_token, **kwargs):
        del prompt, max_new_tokens, kwargs
        on_delta("partial")
        cancellation_token.cancel()
        cancellation_token.raise_if_cancelled()


def test_runtime_persists_cancelled_run(tmp_path):
    events = []
    token = CancellationToken()
    agent = build_agent(
        tmp_path,
        CancellingStreamingModelClient(),
        event_handler=events.append,
        cancellation_token=token,
    )

    assert agent.ask("Long task", run_id="run_cancel_test") == "Run cancelled."
    assert agent.current_task_state.status == "cancelled"
    assert agent.current_task_state.stop_reason == "cancelled"
    assert events[-1]["event_type"] == "run.cancelled"
    report = json.loads(agent.run_store.report_path("run_cancel_test").read_text(encoding="utf-8"))
    assert report["status"] == "cancelled"


def test_anthropic_compatible_client_streams_text_deltas():
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            lines = [
                'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n',
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"<final>"}}\n',
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"OK"}}\n',
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"</final>"}}\n',
                'data: {"type":"message_delta","usage":{"output_tokens":3}}\n',
                'data: {"type":"message_stop"}\n',
            ]
            return iter(line.encode("utf-8") for line in lines)

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return FakeResponse()

    client = AnthropicCompatibleModelClient(
        model="deepseek-test",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )
    deltas = []

    with patch("urllib.request.urlopen", fake_urlopen):
        text = client.complete_stream("hello", 42, deltas.append)

    assert text == "<final>OK</final>"
    assert deltas == ["<final>", "OK", "</final>"]
    assert captured["body"]["stream"] is True
    assert captured["headers"]["Accept"] == "text/event-stream"
    assert client.last_completion_metadata == {
        "input_tokens": 10,
        "output_tokens": 3,
        "total_tokens": 13,
    }


def test_anthropic_stream_honors_cancellation_between_deltas():
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            return iter(
                [
                    b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"one"}}\n',
                    b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"two"}}\n',
                ]
            )

    client = AnthropicCompatibleModelClient(
        model="deepseek-test",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )
    token = CancellationToken()

    def cancel_after_first(delta):
        assert delta == "one"
        token.cancel()

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        with pytest.raises(RunCancelled):
            client.complete_stream("hello", 42, cancel_after_first, cancellation_token=token)


def test_anthropic_stream_cancel_closes_a_stalled_response():
    entered = Event()
    closed = Event()
    outcome = []

    class StalledResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            entered.set()
            closed.wait(5)
            return iter(())

        def close(self):
            closed.set()

    client = AnthropicCompatibleModelClient(
        model="deepseek-test",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        temperature=0.2,
        timeout=30,
    )
    token = CancellationToken()

    def run_stream():
        try:
            client.complete_stream("hello", 42, lambda _delta: None, cancellation_token=token)
        except Exception as exc:
            outcome.append(exc)

    with patch("urllib.request.urlopen", return_value=StalledResponse()):
        thread = Thread(target=run_stream)
        thread.start()
        assert entered.wait(1)
        token.cancel()
        thread.join(1)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], RunCancelled)


def test_runtime_suppresses_streamed_tool_protocol_from_visible_events(tmp_path):
    class ToolStreamingClient:
        supports_prompt_cache = False
        last_completion_metadata = {}

        def __init__(self):
            self.outputs = [
                '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
                "<final>Done.</final>",
            ]

        def complete_stream(self, prompt, max_new_tokens, on_delta, cancellation_token, **kwargs):
            del prompt, max_new_tokens, cancellation_token, kwargs
            output = self.outputs.pop(0)
            for chunk in (output[:3], output[3:9], output[9:]):
                on_delta(chunk)
            return output

    events = []
    agent = build_agent(tmp_path, ToolStreamingClient(), event_handler=events.append)

    assert agent.ask("Read README") == "Done."

    visible = "".join(
        event["payload"]["delta"]
        for event in events
        if event["event_type"] == "message.delta"
    )
    assert visible == "Done."
    assert "<tool" not in visible
    assert "<final>" not in visible
