"""HTTPS/JSON mapping of notifications.v2 and authenticated inbox/subscriptions."""

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from notifyr.config import Settings
from notifyr.contract import (
    Confirmation,
    Error,
    ErrorBody,
    InboxPage,
    Me,
    Notification,
    NotificationState,
    PublishRequest,
    Receipt,
    RespondRequest,
    ResponsePage,
    ServerInfo,
    SubscriptionRequest,
    SubscriptionStatus,
    positive_id,
)
from notifyr.db import Database
from notifyr.push import Policy, Subscriptions, Transport, Worker
from notifyr.service import Service


def safe_sink(message) -> None:
    record = message.record
    allowed = {
        key: value
        for key, value in record["extra"].items()
        if key in {"event", "job_id", "diagnostic"}
    }
    # Deliberately omit free-form message, exception and arbitrary extras entirely.
    sys.stderr.write(json.dumps({"level": record["level"].name, **allowed}) + "\n")


def create_app(settings: Settings | None = None, *, transport=None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database)
    policy = Policy(settings)
    service = Service(db, settings)
    subscriptions = Subscriptions(db, settings, policy)
    worker = Worker(db, settings, transport or Transport(db, settings, policy))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.remove()
        logger.add(safe_sink, diagnose=False, backtrace=False)
        for name in ("urllib3", "requests", "pywebpush", "py_vapid"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        await asyncio.to_thread(db.initialize)
        task = asyncio.create_task(worker.run()) if settings.worker_enabled else None
        app.state.worker_task = task
        logger.bind(event="server_started").info("")
        try:
            yield
        finally:
            worker.stop()
            if task:
                await task
            logger.bind(event="server_stopped").info("")

    errors: dict[int | str, dict[str, Any]] = {
        status: {"model": ErrorBody} for status in (401, 403, 404, 409, 410, 413, 422, 500, 503)
    }
    app = FastAPI(title="Notifyr server", version="0.1.0", lifespan=lifespan, responses=errors)
    app.state.db, app.state.worker, app.state.service = db, worker, service
    app.state.subscriptions = subscriptions
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def bounded_body(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length:
                try:
                    if int(length) < 0 or int(length) > settings.max_request_bytes:
                        return JSONResponse({"error": "request_too_large"}, status_code=413)
                except ValueError:
                    return JSONResponse({"error": "invalid_request"}, status_code=422)
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > settings.max_request_bytes:
                    return JSONResponse({"error": "request_too_large"}, status_code=413)
                body.extend(chunk)
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(Error)
    async def known_error(request: Request, exc: Error):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse({"error": exc.code}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.bind(event="request_failed", diagnostic="internal_error").error("")
        return JSONResponse({"error": "internal_error"}, status_code=500)

    bearer = HTTPBearer(auto_error=False)

    def principal(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or len(credentials.credentials) > 256:
            raise Error(401, "unauthorized")
        return db.authenticate(credentials.credentials)

    def recipient(identity: Annotated[dict, Depends(principal)]) -> str:
        if identity["scope"] != "recipient":
            raise Error(403, "scope_required")
        return identity["user"]

    def publisher(identity: Annotated[dict, Depends(principal)]) -> str:
        if identity["scope"] != "publisher":
            raise Error(403, "scope_required")
        return identity["app"]

    @app.get("/health", tags=["operations"])
    def health():
        return {"status": "ok"}

    @app.get("/ready", tags=["operations"])
    def ready():
        try:
            db.info()
            task = app.state.worker_task
            if settings.worker_enabled and (task is None or task.done()):
                raise Error(503, "not_ready")
        except Exception as exc:
            raise Error(503, "not_ready") from exc
        return {"status": "ready"}

    @app.get("/v1/server-info", response_model=ServerInfo, tags=["discovery"])
    def server_info():
        return db.info()

    @app.get("/v1/me", response_model=Me, response_model_exclude_none=True, tags=["discovery"])
    def me(identity: Annotated[dict, Depends(principal)]):
        return {"scope": identity["scope"], "user_id": identity["user"], "app_id": identity["app"]}

    @app.post(
        "/v1/notifications",
        response_model=Notification,
        response_model_exclude_none=True,
        tags=["notifications"],
        summary="Publish: success means committed to storage",
    )
    def publish(payload: PublishRequest, app_id: Annotated[str, Depends(publisher)]):
        positive_id(payload.notification.id, zero=True)
        return service.publish(app_id, payload.model_dump(exclude_none=True))

    @app.get(
        "/v1/notifications/{id}",
        response_model=NotificationState,
        response_model_exclude_none=True,
        tags=["notifications"],
        summary="Get recipient state",
    )
    def get(id: str, from_server: str, user: Annotated[str, Depends(recipient)]):
        return service.get(user, id, from_server)

    @app.get(
        "/v1/inbox", response_model=InboxPage, response_model_exclude_none=True, tags=["inbox"]
    )
    def inbox(
        user: Annotated[str, Depends(recipient)],
        cursor: str = "0",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        return service.page(user, cursor, limit)

    @app.post(
        "/v1/responses", response_model=Receipt, tags=["responses"], summary="Respond durably"
    )
    def respond(payload: RespondRequest, user: Annotated[str, Depends(recipient)]):
        positive_id(payload.notification_id)
        if "text" in payload.model_fields_set and payload.text is None:
            raise Error(422, "invalid_text")
        return service.respond(user, payload.model_dump(exclude_none=True))

    @app.get(
        "/v1/apps/me/responses",
        response_model=ResponsePage,
        response_model_exclude_none=True,
        tags=["responses"],
    )
    def app_responses(
        app_id: Annotated[str, Depends(publisher)],
        cursor: str = "0",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        return service.page(app_id, cursor, limit, responses=True)

    @app.put(
        "/v1/subscriptions/{installation_id}",
        response_model=SubscriptionStatus,
        tags=["push"],
        summary="Register or rotate; remains pending until challenge confirmation",
    )
    def register(
        installation_id: str, payload: SubscriptionRequest, user: Annotated[str, Depends(recipient)]
    ):
        return subscriptions.register(user, installation_id, payload.model_dump())

    @app.post(
        "/v1/subscriptions/{installation_id}/confirm",
        response_model=SubscriptionStatus,
        tags=["push"],
    )
    def confirm(
        installation_id: str, payload: Confirmation, user: Annotated[str, Depends(recipient)]
    ):
        return subscriptions.confirm(user, installation_id, payload.version, payload.challenge)

    @app.delete("/v1/subscriptions/{installation_id}", status_code=204, tags=["push"])
    def unregister(installation_id: str, user: Annotated[str, Depends(recipient)]):
        subscriptions.delete(user, installation_id)
        return Response(status_code=204)

    return app


app = create_app()
