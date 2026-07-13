"""Authenticated loopback FastAPI gateway for Poppy desktop."""

import asyncio
import secrets
from typing import Dict, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..application.controller import DesktopController
from ..application.service import TERMINAL_RUN_STATUSES


def generate_connection_token():
    return secrets.token_urlsafe(32)


class SessionCreate(BaseModel):
    workspace_root: str
    title: str = "New conversation"


class SessionUpdate(BaseModel):
    title: str


class RunCreate(BaseModel):
    session_id: str
    message: str
    attachments: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    decision: str


class GrantCreate(BaseModel):
    path: str
    can_read: bool = True
    can_write: bool = False
    can_shell: bool = False


class SettingsUpdate(BaseModel):
    model: Optional[str] = None
    base_url: Optional[str] = None
    timeout: Optional[int] = Field(default=None, ge=1, le=900)
    max_steps: Optional[int] = Field(default=None, ge=1, le=100)
    max_new_tokens: Optional[int] = Field(default=None, ge=64, le=32768)


class MemoryCreate(BaseModel):
    category: str = "preference"
    content: str
    source_session_id: str = ""


class MemoryUpdate(BaseModel):
    content: str


def create_gateway_app(controller=None, connection_token=None, shutdown_handler=None):
    controller = controller or DesktopController()
    connection_token = connection_token or generate_connection_token()
    app = FastAPI(title="Poppy Desktop Gateway", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Pico-Token"],
    )
    app.state.controller = controller
    app.state.connection_token = connection_token

    def require_token(x_pico_token: str = Header(default="")):
        if not secrets.compare_digest(str(x_pico_token), connection_token):
            raise HTTPException(status_code=401, detail="invalid connection token")

    auth = [Depends(require_token)]

    @app.exception_handler(KeyError)
    async def handle_key_error(_request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.exception_handler(PermissionError)
    async def handle_permission_error(_request, exc):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def handle_value_error(_request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health", dependencies=auth)
    def health():
        return {"status": "ok", "service": "poppy-desktop-gateway"}

    @app.get("/sessions", dependencies=auth)
    def list_sessions():
        return controller.list_sessions()

    @app.post("/sessions", dependencies=auth, status_code=201)
    def create_session(body: SessionCreate):
        return controller.create_session(body.workspace_root, body.title)

    @app.get("/sessions/{session_id}", dependencies=auth)
    def get_session(session_id: str):
        return controller.get_session(session_id)

    @app.patch("/sessions/{session_id}", dependencies=auth)
    def update_session(session_id: str, body: SessionUpdate):
        return controller.rename_session(session_id, body.title)

    @app.post("/runs", dependencies=auth, status_code=202)
    def create_run(body: RunCreate):
        return controller.start_run(body.session_id, body.message, body.attachments)

    @app.get("/runs/{run_id}", dependencies=auth)
    def get_run(run_id: str):
        return controller.get_run(run_id)

    @app.get("/runs/{run_id}/events", dependencies=auth)
    def get_run_events(run_id: str, after_sequence: int = Query(default=0, ge=0)):
        return controller.get_events(run_id, after_sequence=after_sequence)

    @app.post("/runs/{run_id}/cancel", dependencies=auth, status_code=202)
    def cancel_run(run_id: str):
        return controller.cancel_run(run_id)

    @app.post("/runs/{run_id}/approvals/{approval_id}", dependencies=auth)
    def resolve_approval(run_id: str, approval_id: str, body: ApprovalDecision):
        return controller.resolve_approval(run_id, approval_id, body.decision)

    @app.get("/settings", dependencies=auth)
    def settings():
        return controller.settings()

    @app.patch("/settings", dependencies=auth)
    def update_settings(body: SettingsUpdate):
        values: Dict[str, object] = {
            key: value
            for key, value in body.model_dump().items()
            if value is not None
        }
        return controller.update_settings(values)

    @app.post("/settings/test-connection", dependencies=auth)
    def test_model_connection():
        try:
            return controller.test_model_connection()
        except ValueError:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/grants", dependencies=auth)
    def list_grants():
        return controller.list_grants()

    @app.post("/grants", dependencies=auth, status_code=201)
    def add_grant(body: GrantCreate):
        return controller.add_grant(body.path, body.can_read, body.can_write, body.can_shell)

    @app.delete("/grants/{grant_id}", dependencies=auth, status_code=204)
    def delete_grant(grant_id: str):
        controller.delete_grant(grant_id)

    @app.get("/memories", dependencies=auth)
    def list_memories():
        return controller.list_memories()

    @app.post("/memories", dependencies=auth, status_code=201)
    def add_memory(body: MemoryCreate):
        return controller.add_memory(body.category, body.content, body.source_session_id)

    @app.patch("/memories/{memory_id}", dependencies=auth)
    def update_memory(memory_id: str, body: MemoryUpdate):
        return controller.update_memory(memory_id, body.content)

    @app.delete("/memories/{memory_id}", dependencies=auth, status_code=204)
    def delete_memory(memory_id: str):
        controller.delete_memory(memory_id)

    @app.get("/approval-rules", dependencies=auth)
    def list_approval_rules():
        return controller.list_approval_rules()

    @app.delete("/approval-rules/{rule_id}", dependencies=auth, status_code=204)
    def delete_approval_rule(rule_id: str):
        controller.delete_approval_rule(rule_id)

    if shutdown_handler is not None:
        @app.post("/shutdown", dependencies=auth, status_code=202)
        def shutdown(background_tasks: BackgroundTasks):
            controller.shutdown()
            background_tasks.add_task(shutdown_handler)
            return {"status": "shutting_down"}

    @app.websocket("/events")
    async def events_socket(
        websocket: WebSocket,
        token: str = Query(default=""),
        run_id: str = Query(default=""),
        after_sequence: int = Query(default=0, ge=0),
    ):
        if not secrets.compare_digest(str(token), connection_token) or not run_id:
            await websocket.close(code=1008, reason="invalid connection token or run id")
            return
        try:
            controller.get_run(run_id)
        except KeyError:
            await websocket.close(code=1008, reason="unknown run")
            return
        await websocket.accept()
        last_sequence = int(after_sequence)
        while True:
            for event in controller.get_events(run_id, after_sequence=last_sequence):
                await websocket.send_json(event)
                last_sequence = max(last_sequence, int(event.get("sequence", 0)))
            state = controller.get_run(run_id)
            if state["status"] in TERMINAL_RUN_STATUSES:
                await websocket.close(code=1000)
                return
            await asyncio.sleep(0.02)

    return app
