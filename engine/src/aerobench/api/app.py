"""FastAPI application factory: localhost-only service with a per-launch bearer token."""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
import threading
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..core.errors import AeroBenchError, ErrorCategory
from ..project.store import Project
from ..services.context import AppContext
from ..services.viz import VizService
from ..version import APP_DISPLAY_NAME, APP_VERSION


class ProjectRegistry:
    """Projects opened by this engine instance, keyed by project id."""

    def __init__(self):
        self._items: dict[str, Project] = {}
        self._lock = threading.RLock()
        self.locks: dict[str, threading.RLock] = {}

    def add(self, p: Project) -> str:
        with self._lock:
            pid = p.config.project_id
            old = self._items.get(pid)
            if old is not None and old.root != p.root:
                old.close()
            self._items[pid] = p
            self.locks.setdefault(pid, threading.RLock())
            return pid

    def get(self, pid: str) -> Project:
        with self._lock:
            p = self._items.get(pid)
        if p is None:
            raise AeroBenchError.make("project.not_open", ErrorCategory.PROJECT, "The project is not open.", action="Open the project again.")
        return p

    def lock(self, pid: str) -> threading.RLock:
        return self.locks.setdefault(pid, threading.RLock())

    def remove(self, pid: str) -> None:
        with self._lock:
            p = self._items.pop(pid, None)
        if p:
            p.close()

    def all(self) -> list[Project]:
        with self._lock:
            return list(self._items.values())


class EngineState:
    def __init__(self, ctx: AppContext, token: str):
        self.ctx = ctx
        self.token = token
        self.projects = ProjectRegistry()
        self.viz = VizService(ctx)


def create_app(ctx: Optional[AppContext] = None, token: str = "", dev_cors: bool = False, static_dir: Optional[str] = None) -> FastAPI:
    state = EngineState(ctx or AppContext(), token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        state.viz.close()
        for p in state.projects.all():
            p.close()

    app = FastAPI(title=f"{APP_DISPLAY_NAME} engine", version=APP_VERSION, docs_url="/api/docs" if dev_cors else None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.engine = state
    if dev_cors:
        app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def auth(request: Request, call_next):
        # Path parameters become file-system path components: refuse traversal tricks
        # (decoded backslashes, dot segments, NUL) before any route sees them.
        raw = request.url.path
        if "\\" in raw or "\x00" in raw or any(seg in ("..", ".") for seg in raw.split("/")):
            return JSONResponse({"error": {"code": "request.bad_path", "summary": "Invalid path"}}, status_code=400)
        if state.token and request.url.path.startswith("/api/") and request.url.path not in ("/api/health",):
            if request.method == "OPTIONS":
                return await call_next(request)
            supplied = request.headers.get("authorization", "")
            supplied = supplied[7:] if supplied.lower().startswith("bearer ") else request.query_params.get("token", "")
            if not hmac.compare_digest(supplied, state.token):
                return JSONResponse({"error": {"code": "auth", "summary": "Unauthorised"}}, status_code=401)
        return await call_next(request)

    @app.exception_handler(AeroBenchError)
    async def ab_error(_: Request, exc: AeroBenchError):
        d = exc.diagnostic
        status = 404 if d.code.endswith(("unknown", "not_found", "not_open")) else 409 if d.code in ("reference.locked", "project.exists") else 400
        return JSONResponse({"error": d.model_dump(mode="json")}, status_code=status)

    @app.exception_handler(ValidationError)
    async def val_error(_: Request, exc: ValidationError):
        return JSONResponse({"error": {"code": "config.validation", "category": "configuration", "severity": "error",
                                       "summary": "Invalid input.", "details": str(exc), "errors": exc.errors(include_url=False, include_context=False)}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def internal(_: Request, exc: Exception):
        return JSONResponse({"error": {"code": "internal", "category": "internal", "severity": "error",
                                       "summary": f"Internal error: {exc}", "details": traceback.format_exc()[-6000:]}}, status_code=500)

    from .routes_core import router as core
    from .routes_explore import router as explore
    from .routes_expert import router as expert
    from .routes_geometry import router as geometry
    from .routes_runs import router as runs
    from .routes_viz import router as viz
    from .ws import router as ws

    for r in (core, geometry, runs, viz, expert, explore, ws):
        app.include_router(r)

    if static_dir and Path(static_dir).is_dir():
        from fastapi.staticfiles import StaticFiles

        # The renderer bundle is public (no secrets); every /api route still requires the token.
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


def engine(request: Request) -> EngineState:
    return request.app.state.engine


def engine_ws(ws: WebSocket) -> EngineState:
    return ws.app.state.engine
