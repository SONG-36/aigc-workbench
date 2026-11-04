"""Composition root. Database initialization happens at application startup."""

from contextlib import asynccontextmanager
import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .api import router
from .application import TaskNotFound, TaskService
from .config import Settings
from .domain import InvalidTask
from .persistence import SQLiteTaskStore

logger = logging.getLogger(__name__)


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, **extra},
    })


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    store = SQLiteTaskStore(settings.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        app.state.task_service = TaskService(store)
        yield

    app = FastAPI(title="AIGC Workbench · F01", lifespan=lifespan)

    @app.middleware("http")
    async def local_development_boundary(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return error(403, "LOCAL_ONLY", "F01 仅支持本机开发访问。")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            same_origin = str(request.base_url).rstrip("/")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and origin not in {*settings.allowed_origins, same_origin}
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

    @app.exception_handler(sqlite3.Error)
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
    return app
