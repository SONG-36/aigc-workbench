"""HTTP validation and serialization; use cases do not live in routes."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from .application import TaskService
from .domain import TaskDraft

router = APIRouter(prefix="/api")


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str


class TaskResponse(BaseModel):
    id: UUID
    title: str
    mode: Literal["quick_a"]
    status: Literal["draft"]
    can_generate: Literal[False]
    readiness_reason: Literal["missing_product_and_reference_assets"]
    created_at: datetime

    @classmethod
    def from_task(cls, task: TaskDraft) -> "TaskResponse":
        return cls(
            id=task.id, title=task.title, mode=task.mode, status=task.status,
            can_generate=task.can_generate,
            readiness_reason="missing_product_and_reference_assets",
            created_at=task.created_at,
        )


def task_service(request: Request) -> TaskService:
    return request.app.state.task_service


Service = Annotated[TaskService, Depends(task_service)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "scope": "F01 draft-only; no AI execution"}


@router.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(body: CreateTaskRequest, service: Service) -> TaskResponse:
    return TaskResponse.from_task(service.create_draft(body.title))


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(service: Service) -> list[TaskResponse]:
    return [TaskResponse.from_task(task) for task in service.list_drafts()]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: UUID, service: Service) -> TaskResponse:
    return TaskResponse.from_task(service.get_draft(task_id))
