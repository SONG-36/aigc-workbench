"""HTTP validation and serialization; business coordination stays in Application."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .application import TaskService
from .domain import Asset, AssetRole, SlotKind, Task, OutputSize
from .file_storage import MAX_UPLOAD_BYTES
from .generation_application import GenerationApplication

router = APIRouter(prefix="/api")


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(strict=True)
    operation_id: UUID
    overall_requirements: str = Field(default="", strict=True, max_length=2000)


class SaveOverallRequirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(strict=True, max_length=2000)
    expected_version: int = Field(strict=True, ge=0)
    operation_id: UUID


class CreateSlotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: SlotKind
    operation_id: UUID


class ConfigureSlotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_asset_id: UUID | None
    output_size: OutputSize
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
    overall_requirement: dict
    assets: list[AssetResponse]
    slots: list[SlotResponse]

    @classmethod
    def from_task(cls, task: Task) -> "TaskResponse":
        return cls(
            id=task.id, title=task.title, mode=task.mode, status=task.status,
            can_generate=task.can_generate, materials_ready=task.materials_ready,
            readiness_reason=task.readiness_reason, created_at=task.created_at,
            overall_requirement=(task.overall_requirement.projection() if task.overall_requirement else {
                "id": None, "task_id": str(task.id), "version": 0, "text": "", "scope": "TASK_ALL_SLOTS", "saved_at": None,
                "source": "NOT_PROVIDED"}),
            assets=[AssetResponse.from_asset(a) for a in task.assets],
            slots=[SlotResponse(id=s.id, task_id=s.task_id, kind=s.kind, position=s.position,
                                reference_asset_id=s.reference_asset_id, output_size=s.output_size,
                                requirements=s.requirements, inputs_ready=task.slot_inputs_ready(s))
                   for s in task.slots],
        )


def task_service(request: Request) -> TaskService:
    return request.app.state.task_service


Service = Annotated[TaskService, Depends(task_service)]


def generation_service(request: Request) -> GenerationApplication:
    return request.app.state.generation_service


Generation = Annotated[GenerationApplication, Depends(generation_service)]


class GenerateImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    quote_token: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")
    confirm_paid: Literal[True] | None = None  # Legacy development clients; Pilot needs no fee approval.
    max_cost_usd: Literal["1.00"] | None = None


class RevisionQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_text: str = Field(strict=True, min_length=1, max_length=2000)


class ReviseImageRequest(GenerateImageRequest):
    snapshot_id: UUID


class ReviewImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    decision: Literal["PASS", "REJECT"]
    reviewer: str = Field(strict=True, min_length=1, max_length=80)
    feedback: str = Field(default="", strict=True, max_length=2000)
    expected_current_review_id: UUID | None


@router.get("/tasks/{task_id}/slots/{slot_id}/workspace")
def slot_workspace(task_id: UUID, slot_id: UUID, service: Generation):
    return service.workspace(task_id, slot_id)


@router.post("/tasks/{task_id}/slots/{slot_id}/generations", status_code=202)
def generate_image(task_id: UUID, slot_id: UUID, body: GenerateImageRequest, service: Generation):
    return service.submit(task_id, slot_id, body.operation_id, body.quote_token, body.max_cost_usd)


@router.post("/tasks/{task_id}/slots/{slot_id}/versions/{version_id}/revision-quote")
def revision_quote(task_id: UUID, slot_id: UUID, version_id: UUID, body: RevisionQuoteRequest, service: Generation):
    return service.revision_quote(task_id, slot_id, version_id, body.revision_text)


@router.post("/tasks/{task_id}/slots/{slot_id}/revisions", status_code=202)
def revise_image(task_id: UUID, slot_id: UUID, body: ReviseImageRequest, service: Generation):
    return service.submit_revision(task_id, slot_id, body.operation_id, body.snapshot_id, body.quote_token, body.max_cost_usd)


@router.post("/tasks/{task_id}/slots/{slot_id}/versions/{version_id}/reviews", status_code=201)
def review_image(task_id: UUID, slot_id: UUID, version_id: UUID, body: ReviewImageRequest, service: Generation):
    return service.review(task_id, slot_id, version_id, body.operation_id, body.decision, body.reviewer,
                          body.feedback, body.expected_current_review_id)


@router.get("/tasks/{task_id}/slots/{slot_id}/delivery")
def download_delivery(task_id: UUID, slot_id: UUID, review_id: UUID, service: Generation):
    candidate, content = service.delivery(task_id, slot_id, review_id)
    prefix = 'TEST_ONLY' if candidate.get('execution_mode') == 'TEST_ONLY' else 'quick-a'
    return Response(content, media_type="image/png", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "ETag": '"' + candidate["image"]["sha256"] + '"',
        "Content-Disposition": f'attachment; filename="{prefix}-{slot_id}-{candidate["image_version_id"]}.png"',
    })


@router.get("/tasks/{task_id}/slots/{slot_id}/versions/{version_id}/content")
def candidate_content(task_id: UUID, slot_id: UUID, version_id: UUID, service: Generation):
    body, content = service.content(task_id, slot_id, version_id)
    return Response(content, media_type="image/png", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "ETag": '"' + body["image"]["sha256"] + '"',
        "Content-Disposition": f'inline; filename="{version_id}.png"',
    })


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "scope": "Quick-A workspace; controlled C06 generation"}


@router.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(body: CreateTaskRequest, service: Service) -> TaskResponse:
    return TaskResponse.from_task(service.create_draft(body.title, body.operation_id, body.overall_requirements))


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(service: Service) -> list[TaskResponse]:
    return [TaskResponse.from_task(task) for task in service.list_drafts()]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: UUID, service: Service) -> TaskResponse:
    return TaskResponse.from_task(service.get_draft(task_id))


@router.put("/tasks/{task_id}/overall-requirement", response_model=TaskResponse)
def save_overall_requirement(task_id: UUID, body: SaveOverallRequirementRequest, service: Service):
    return TaskResponse.from_task(service.save_overall_requirement(task_id, body.text, body.expected_version, body.operation_id))


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
