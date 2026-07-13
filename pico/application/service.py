"""Threaded application service used by the local desktop gateway."""

from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Callable, Dict, List, Optional

from .cancellation import CancellationToken, RunCancelled


TERMINAL_RUN_STATUSES = {"completed", "cancelled", "failed"}
APPROVAL_ALLOW_ONCE = "allow_once"
APPROVAL_ALLOW_ALWAYS = "allow_always"
APPROVAL_DENY = "deny"
LEGAL_APPROVAL_DECISIONS = {APPROVAL_ALLOW_ONCE, APPROVAL_ALLOW_ALWAYS, APPROVAL_DENY}


@dataclass
class ApprovalWaiter:
    ready: Event = field(default_factory=Event)
    decision: str = ""
    request: dict = field(default_factory=dict)


@dataclass
class ActiveRun:
    run_id: str
    session_id: str
    cancellation_token: CancellationToken
    thread: Optional[Thread] = None
    status: str = "starting"
    answer: str = ""
    error: str = ""
    events: List[dict] = field(default_factory=list)
    approvals: Dict[str, ApprovalWaiter] = field(default_factory=dict)
    early_approval_decisions: Dict[str, str] = field(default_factory=dict)

    def snapshot(self):
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "answer": self.answer,
            "error": self.error,
        }


class AssistantService:
    """Own active runs without exposing CLI or runtime internals to clients."""

    def __init__(
        self,
        event_handler: Optional[Callable[[dict], None]] = None,
        event_limit=2000,
        approval_rule_checker: Optional[Callable[[dict], bool]] = None,
        approval_rule_saver: Optional[Callable[[dict], bool]] = None,
    ):
        self.event_handler = event_handler
        self.event_limit = max(100, int(event_limit))
        self.approval_rule_checker = approval_rule_checker
        self.approval_rule_saver = approval_rule_saver
        self._runs: Dict[str, ActiveRun] = {}
        self._active_sessions: Dict[str, str] = {}
        self._lock = Lock()

    def start_run(self, agent, user_message):
        session_id = agent.session["id"]
        run_id = agent.new_run_id()
        token = CancellationToken()
        run = ActiveRun(run_id=run_id, session_id=session_id, cancellation_token=token)
        with self._lock:
            existing_run_id = self._active_sessions.get(session_id)
            if existing_run_id:
                existing = self._runs.get(existing_run_id)
                if existing and existing.status not in TERMINAL_RUN_STATUSES:
                    raise RuntimeError(f"session already has an active run: {existing_run_id}")
            self._runs[run_id] = run
            self._active_sessions[session_id] = run_id

        agent.configure_run_controls(
            cancellation_token=token,
            event_handler=lambda event: self._record_event(run_id, event),
            approval_handler=lambda request: self._wait_for_approval(run_id, request),
            approval_precheck_handler=self._is_preapproved,
        )
        thread = Thread(
            target=self._run_agent,
            args=(run, agent, str(user_message)),
            name=f"pico-{run_id}",
            daemon=True,
        )
        run.thread = thread
        thread.start()
        return run.snapshot()

    def _run_agent(self, run, agent, user_message):
        run.status = "running"
        try:
            run.answer = agent.ask(user_message, run_id=run.run_id)
            if agent.current_task_state is not None:
                run.status = agent.current_task_state.status
            elif run.cancellation_token.cancelled:
                run.status = "cancelled"
            else:
                run.status = "completed"
        except RunCancelled:
            run.status = "cancelled"
            run.answer = "Run cancelled."
            agent.emit_event("run.cancelled", {"status": "cancelled", "stop_reason": "cancelled"}, run_id=run.run_id)
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            if not run.events or run.events[-1].get("event_type") != "run.failed":
                agent.emit_event("run.failed", {"status": "failed", "error": str(exc)}, run_id=run.run_id)
        finally:
            with self._lock:
                if self._active_sessions.get(run.session_id) == run.run_id:
                    self._active_sessions.pop(run.session_id, None)
                for waiter in run.approvals.values():
                    waiter.ready.set()

    def _record_event(self, run_id, event):
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.events.append(dict(event))
            if len(run.events) > self.event_limit:
                del run.events[:-self.event_limit]
        if self.event_handler is not None:
            self.event_handler(dict(event))

    def _wait_for_approval(self, run_id, request):
        approval_id = str(request.get("approval_id", ""))
        if not approval_id:
            return False
        if self.approval_rule_checker is not None and self.approval_rule_checker(dict(request)):
            return True
        with self._lock:
            run = self._require_run(run_id)
            early = run.early_approval_decisions.pop(approval_id, "")
            if early:
                if early == APPROVAL_ALLOW_ALWAYS and self.approval_rule_saver is not None:
                    self.approval_rule_saver(dict(request))
                return early in {APPROVAL_ALLOW_ONCE, APPROVAL_ALLOW_ALWAYS}
            waiter = ApprovalWaiter(request=dict(request))
            run.approvals[approval_id] = waiter
        while not waiter.ready.wait(0.1):
            run.cancellation_token.raise_if_cancelled()
        run.cancellation_token.raise_if_cancelled()
        with self._lock:
            run.approvals.pop(approval_id, None)
        if waiter.decision == APPROVAL_ALLOW_ALWAYS and self.approval_rule_saver is not None:
            self.approval_rule_saver(dict(waiter.request))
        return waiter.decision in {APPROVAL_ALLOW_ONCE, APPROVAL_ALLOW_ALWAYS}

    def _is_preapproved(self, request):
        return bool(self.approval_rule_checker and self.approval_rule_checker(dict(request)))

    def resolve_approval(self, run_id, approval_id, decision):
        if decision not in LEGAL_APPROVAL_DECISIONS:
            raise ValueError(f"invalid approval decision: {decision}")
        with self._lock:
            run = self._require_run(run_id)
            waiter = run.approvals.get(approval_id)
            if waiter is None:
                run.early_approval_decisions[approval_id] = decision
            else:
                waiter.decision = decision
                waiter.ready.set()
        return {"run_id": run_id, "approval_id": approval_id, "decision": decision}

    def cancel_run(self, run_id):
        with self._lock:
            run = self._require_run(run_id)
            run.cancellation_token.cancel()
            for waiter in run.approvals.values():
                waiter.ready.set()
        return run.snapshot()

    def get_run(self, run_id):
        with self._lock:
            return self._require_run(run_id).snapshot()

    def has_active_session(self, session_id):
        with self._lock:
            run_id = self._active_sessions.get(session_id)
            if not run_id:
                return False
            run = self._runs.get(run_id)
            return bool(run and run.status not in TERMINAL_RUN_STATUSES)

    def get_events(self, run_id, after_sequence=0):
        with self._lock:
            run = self._require_run(run_id)
            return [
                dict(event)
                for event in run.events
                if int(event.get("sequence", 0)) > int(after_sequence)
            ]

    def wait(self, run_id, timeout=None):
        with self._lock:
            run = self._require_run(run_id)
            thread = run.thread
        if thread is not None:
            thread.join(timeout)
        return self.get_run(run_id)

    def shutdown(self, timeout=2.0):
        with self._lock:
            runs = list(self._runs.values())
            for run in runs:
                if run.status not in TERMINAL_RUN_STATUSES:
                    run.cancellation_token.cancel()
                    for waiter in run.approvals.values():
                        waiter.ready.set()
        for run in runs:
            if run.thread is not None and run.thread.is_alive():
                run.thread.join(timeout)

    def _require_run(self, run_id):
        run = self._runs.get(str(run_id))
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        return run
