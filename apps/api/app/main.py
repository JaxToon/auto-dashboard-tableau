"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .audit import request_id_var
from .config import Settings, get_settings
from .db import init_engine, session_factory
from .errors import AppError
from .identity import seed_mock_users
from .recommendations import seed_simulated_events
from .routes import router
from .runtime import Container

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("pbac")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings.database_url)
        db = session_factory()()
        try:
            if settings.auth_mode == "mock":
                seed_mock_users(db)
            if settings.seed_simulated_events:
                seed_simulated_events(db)
            app.state.container.runtime.load(db)
        finally:
            db.close()
        log.info("started env=%s mode=%s tableau=%s llm=%s", settings.app_env, settings.app_mode, app.state.container.publisher.mode, app.state.container.planner.name)
        yield

    app = FastAPI(title="Proactive Business Analytics Copilot API (PoC)", version="0.1.0", lifespan=lifespan,
                  description="PoC API. All data is SYNTHETIC. See docs/api.md.")
    app.state.container = Container(settings)

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-Request-ID"])

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        rid = request.headers.get("X-Request-ID", "")[:64] or uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def error_body(code: str, message: str, details: dict | None = None) -> dict:
        return {"error": {"code": code, "message": message, "request_id": request_id_var.get(), **({"details": details} if details else {})}}

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def req_validation(request: Request, exc: RequestValidationError):
        details = {"fields": [{"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()[:20]]}
        return JSONResponse(error_body("REQUEST_INVALID", "The request was not valid.", details), status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", request_id_var.get())
        return JSONResponse(error_body("INTERNAL_ERROR", "Something went wrong. Please try again; your draft was preserved."), status_code=500)

    app.include_router(router)
    return app


app = create_app()
