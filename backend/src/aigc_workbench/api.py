"""HTTP validation and serialization; business coordination stays in Application."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .application import TaskService
from .domain import Asset, AssetRole, SlotKind, Task
from .file_storage import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api")


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(strict=True)
    operation_id: UUID


class CreateSlotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: SlotKind
    operation_id: UUID


class ConfigureSlotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_asset_id: UUID | None
    output_size: Literal["1024x1536", "1024x1024", "1536x1024"]
    requirements: str = Field(strict=True, max_length=2000)


class AssetResponse(BaseModel):
    id: UUID
    task_id: UUID
    role: AssetRole
    filename: str
    sha256: str
    size_bytes: int
    media_type: str
    width: int
    height: int
    created_at: datetime
    url: str

    @classmethod
    def from_asset(cls, asset: Asset) -> "AssetResponse":
        return cls(id=asset.id, task_id=asset.task_id, role=asset.role, filename=asset.filename,
                   sha256=asset.image.sha256, size_bytes=asset.image.size_bytes,
                   media_type=asset.image.media_type, width=asset.image.width, height=asset.image.height,
                   created_at=asset.created_at,
                   url=f"/api/tasks/{asset.task_id}/assets/{asset.id}/content")


class SlotResponse(BaseModel):
    id: UUID
    task_id: UUID
    kind: SlotKind
    position: int
    reference_asset_id: UUID | None
    output_size: str
    requirements: str
    inputs_ready: bool


class TaskResponse(BaseModel):
    id: UUID
    title: str
    mode: Literal["quick_a"]
    status: Literal["draft"]
    can_generate: Literal[False]
    materials_ready: bool
    readiness_reason: str
    created_at: datetime
    assets: list[AssetResponse]
    slots: list[SlotResponse]

    @classmethod
    def from_task(cls, task: Task) -> "TaskResponse":
        return cls(
            id=task.id, title=task.title, mode=task.mode, status=task.status,
            can_generate=task.can_generate, materials_ready=task.materials_ready,
            readiness_reason=task.readiness_reason, created_at=task.created_at,
            assets=[AssetResponse.from_asset(a) for a in task.assets],
            slots=[SlotResponse(id=s.id, task_id=s.task_id, kind=s.kind, position=s.position,
                                reference_asset_id=s.reference_asset_id, output_size=s.output_size,
                                requirements=s.requirements, inputs_ready=task.slot_inputs_ready(s))
                   for s in task.slots],
        )


def task_service(request: Request) -> TaskService:
    return request.app.state.task_service


Service = Annotated[TaskService, Depends(task_service)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "scope": "S01 assets and slots; no AI execution"}


@router.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(body: CreateTaskRequest, service: Service) -> TaskResponse:
    return TaskResponse.from_task(service.create_draft(body.title, body.operation_id))


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(service: Service) -> list[TaskResponse]:
    return [TaskResponse.from_task(task) for task in service.list_drafts()]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: UUID, service: Service) -> TaskResponse:
    return TaskResponse.from_task(service.get_draft(task_id))


@router.post("/tasks/{task_id}/assets", response_model=TaskResponse, status_code=201)
async def upload_asset(task_id: UUID, request: Request, service: Service, role: AssetRole,
                       operation_id: UUID, filename: Annotated[str, Query(min_length=1, max_length=180)]):
    # Raw file body avoids multipart spooling before our explicit byte limit.
    content = bytearray()
    try:
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "单张图片超过 10 MiB，未保存。")
            content.extend(chunk)
    except ClientDisconnect as exc:
        raise HTTPException(400, "上传中断，未保存素材。") from exc
    await run_in_threadpool(service.upload_asset, task_id, operation_id, role, filename,
                            bytes(content), request.headers.get("content-type", "application/octet-stream"))
    return TaskResponse.from_task(await run_in_threadpool(service.get_draft, task_id))


@router.get("/tasks/{task_id}/assets/{asset_id}/content")
def asset_content(task_id: UUID, asset_id: UUID, service: Service) -> Response:
    asset, content = service.read_asset(task_id, asset_id)
    return Response(content, media_type=asset.image.media_type, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "ETag": '"' + asset.image.sha256 + '"',
        "Content-Disposition": f'inline; filename="{asset.id}.image"',
    })


@router.post("/tasks/{task_id}/slots", response_model=TaskResponse, status_code=201)
def create_slot(task_id: UUID, body: CreateSlotRequest, service: Service) -> TaskResponse:
    service.create_slot(task_id, body.operation_id, body.kind)
    return TaskResponse.from_task(service.get_draft(task_id))


@router.put("/tasks/{task_id}/slots/{slot_id}", response_model=TaskResponse)
def configure_slot(task_id: UUID, slot_id: UUID, body: ConfigureSlotRequest, service: Service) -> TaskResponse:
    service.configure_slot(task_id, slot_id, body.reference_asset_id, body.output_size, body.requirements)
    return TaskResponse.from_task(service.get_draft(task_id))
