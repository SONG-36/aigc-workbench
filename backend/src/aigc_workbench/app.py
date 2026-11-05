"""Composition root. Database initialization happens at application startup."""

from contextlib import asynccontextmanager
import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .api import router
from .application import StorageUnavailable, TaskNotFound, TaskService
from .config import Settings
from .domain import Conflict, InvalidTask
from .file_storage import LocalImageFiles
from .persistence import SQLiteTaskStore
from .generation_application import GenerationApplication
from .c01_context import C01Error
from .pilot_http import pilot_boundary, peer_allowed, attach_frontend

logger = logging.getLogger(__name__)


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, **extra},
    })


def create_app(settings: Settings | None = None, *, generation_transport=None, understanding_transport=None) -> FastAPI:
    settings = settings or Settings.from_environment()
    pilot = settings.environment == "pilot"
    networks = pilot_boundary(settings) if pilot else ()
    store = SQLiteTaskStore(settings.database_path)
    files = LocalImageFiles(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        files.initialize()
        app.state.task_service = TaskService(store, files)
        app.state.generation_service = GenerationApplication(settings, app.state.task_service, store,
                                                              transport=generation_transport, responses_transport=understanding_transport)
        app.state.generation_service.start()
        orphans = app.state.task_service.orphan_files()
        if orphans:
            logger.warning("Unreferenced immutable asset files retained for inspection: %s", orphans)
        try:
            yield
        finally:
            app.state.generation_service.dispatcher.close()

    app = FastAPI(title="AIGC Workbench · Quick-A", lifespan=lifespan,
                  docs_url=None if pilot else "/docs", redoc_url=None if pilot else "/redoc",
                  openapi_url=None if pilot else "/openapi.json")

    @app.middleware("http")
    async def local_development_boundary(request: Request, call_next):
        if pilot and (request.url.hostname not in settings.pilot_hosts or not request.client
                      or not peer_allowed(request.client.host, networks)):
            return error(403, "PILOT_NETWORK_DENIED", "该地址不在已确认的内部Pilot访问范围。")
        if pilot and any(name == "forwarded" or name.startswith("x-forwarded-") for name in request.headers):
            return error(403, "PILOT_PROXY_DENIED", "Pilot仅允许已确认局域网内直接访问。")
        if not pilot and request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return error(403, "LOCAL_ONLY", "当前仅支持本机开发访问。")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            same_origin = str(request.base_url).rstrip("/")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and origin not in ({same_origin} if pilot else {*settings.allowed_origins, same_origin})
            ):
                return error(403, "ORIGIN_DENIED", "该来源不能写入本地任务。")
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error(422, "INVALID_REQUEST", "请求不符合接口要求。", details=[
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in exc.errors()
        ])

    @app.exception_handler(InvalidTask)
    async def invalid_task(request: Request, exc: InvalidTask):
        return error(422, "INVALID_TASK", str(exc))

    @app.exception_handler(TaskNotFound)
    async def missing_task(request: Request, exc: TaskNotFound):
        return error(404, "TASK_NOT_FOUND", str(exc))

    @app.exception_handler(Conflict)
    async def conflict(request: Request, exc: Conflict):
        return error(409, "OPERATION_CONFLICT", str(exc))

    @app.exception_handler(C01Error)
    async def generation_error(request: Request, exc: C01Error):
        status = 404 if exc.code.endswith("NOT_FOUND") else 503
        return error(status, exc.code, str(exc))

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(StorageUnavailable)
    async def storage_error(request: Request, exc: sqlite3.Error):
        logger.error("Task storage operation failed", exc_info=exc)
        return error(503, "STORAGE_UNAVAILABLE", "任务存储暂不可用，请查询状态后再操作。")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("Unexpected application error", exc_info=exc)
        return error(500, "INTERNAL_ERROR", "服务发生错误，未确认保存成功，请查询任务后再操作。")

    app.include_router(router)
    if pilot:
        attach_frontend(app, settings.frontend_dist)
    return app
