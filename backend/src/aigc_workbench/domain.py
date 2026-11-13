"""Quick-A identities and rules; standard-library dependencies only."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class InvalidTask(ValueError):
    pass


class Conflict(ValueError):
    pass


class AssetRole(StrEnum):
    PRODUCT_IMAGE = "product_image"
    MARKETING_REFERENCE = "marketing_reference"


class SlotKind(StrEnum):
    MAIN = "main"
    DIMENSIONS = "dimensions"
    COMPARISON = "comparison"
    SELLING_POINT = "selling_point"
    SCENE = "scene"


class OutputSize(StrEnum):
    PORTRAIT = "1024x1536"
    SQUARE = "1024x1024"
    LANDSCAPE = "1536x1024"
    LARGE_SQUARE = "1536x1536"


OUTPUT_SIZES = frozenset(OutputSize)


@dataclass(frozen=True)
class ImageMetadata:
    sha256: str
    size_bytes: int
    media_type: str
    width: int
    height: int


@dataclass(frozen=True)
class Asset:
    id: UUID
    task_id: UUID
    role: AssetRole
    filename: str
    image: ImageMetadata
    created_at: datetime

    def same_upload(self, role: AssetRole, filename: str, image: ImageMetadata) -> bool:
        return (self.role, self.filename, self.image) == (role, filename, image)


@dataclass(frozen=True)
class ImageSlot:
    id: UUID
    task_id: UUID
    kind: SlotKind
    position: int
    reference_asset_id: UUID | None = None
    output_size: str = "1024x1536"
    requirements: str = ""
    archived: bool = False
    version: int = 0

    def configure(self, task: "Task", reference_id: UUID | None,
                  output_size: str, requirements: str) -> "ImageSlot":
        if self.archived:
            raise Conflict("已移除图位只保留历史，不能修改配置。")
        if self.task_id != task.id:
            raise InvalidTask("图位不属于当前任务。")
        if output_size not in OUTPUT_SIZES or len(requirements) > 2000:
            raise InvalidTask("输出尺寸不支持，或逐图要求超过 2000 字符。")
        if reference_id is not None and not any(
            a.id == reference_id and a.task_id == task.id
            and a.role == AssetRole.MARKETING_REFERENCE for a in task.assets
        ):
            raise InvalidTask("参考必须是当前任务已保存的营销参考素材。")
        return replace(self, reference_asset_id=reference_id,
                       output_size=output_size, requirements=requirements)


@dataclass(frozen=True)
class OverallRequirement:
    id: UUID
    task_id: UUID
    version: int
    text: str
    saved_at: datetime
    scope: str = "TASK_ALL_SLOTS"

    @staticmethod
    def validate(text: str) -> str:
        if not isinstance(text, str) or len(text) > 2000:
            raise InvalidTask("整体运营要求须为文本，最多 2000 字符。")
        return text

    def projection(self) -> dict:
        return {"id": str(self.id), "task_id": str(self.task_id), "version": self.version,
                "text": self.text, "scope": self.scope, "saved_at": self.saved_at.isoformat(),
                "source": "EXPLICIT_OPERATOR_REQUIREMENT / UNAUTHENTICATED_OPERATOR"}


@dataclass(frozen=True)
class Task:
    id: UUID
    title: str
    created_at: datetime
    mode: str = "quick_a"
    status: str = "draft"
    assets: tuple[Asset, ...] = ()
    slots: tuple[ImageSlot, ...] = ()
    overall_requirement: OverallRequirement | None = None
    deleted_at: datetime | None = None
    has_unresolved_execution: bool = False

    @classmethod
    def create(cls, title: str, mode: str = "quick_a") -> "Task":
        if mode not in {"quick_a", "quick_b"}:
            raise InvalidTask("任务模式须为 Quick-A 或 Quick-B。")
        return cls(uuid4(), cls.normalize_title(title), datetime.now(timezone.utc), mode=mode)

    @staticmethod
    def normalize_title(title: str) -> str:
        title = title.strip()
        if not 1 <= len(title) <= 120:
            raise InvalidTask("任务名称须为 1～120 个字符，不能只有空白。")
        return title

    def new_slot(self, kind: SlotKind) -> ImageSlot:
        if kind in {SlotKind.MAIN, SlotKind.DIMENSIONS, SlotKind.COMPARISON} and any(s.kind == kind for s in self.slots):
            raise Conflict("主图、尺寸图和对比图各最多一个。")
        return ImageSlot(uuid4(), self.id, kind,
                         max((s.position for s in self.slots), default=-1) + 1)

    def slot_inputs_ready(self, slot: ImageSlot) -> bool:
        return self.slot_generation_blocked_reason(slot) is None

    def slot_generation_blocked_reason(self, slot: ImageSlot) -> str | None:
        if slot.archived:
            return '该图位已移除，仅保留历史。'
        if not any(a.role == AssetRole.PRODUCT_IMAGE for a in self.assets):
            return '请先上传并保存商品图。'
        if slot.reference_asset_id is None:
            return '请为本图位上传并绑定营销参考图。'
        if not any(a.id == slot.reference_asset_id and a.role == AssetRole.MARKETING_REFERENCE for a in self.assets):
            return '本图位绑定的参考素材不可用，请重新核对。'
        return None

    @property
    def delete_blocked_reason(self) -> str | None:
        if self.has_unresolved_execution:
            return '任务有排队、执行中或结果未知的操作，请先核对执行状态，暂不能删除。'
        return None

    @property
    def materials_ready(self) -> bool:
        """At least one independent slot has basic Quick-A image inputs."""
        return any(self.slot_inputs_ready(s) for s in self.slots)

    @property
    def readiness_reason(self) -> str:
        product = any(a.role == AssetRole.PRODUCT_IMAGE for a in self.assets)
        reference = any(a.role == AssetRole.MARKETING_REFERENCE for a in self.assets)
        if not product and not reference:
            return "missing_product_and_reference_assets"
        if not product:
            return "missing_product_assets"
        if not self.slots:
            return "missing_slots"
        return "assets_prepared_generation_disabled" if self.materials_ready else "missing_slot_references"

    @property
    def can_generate(self) -> bool:
        return False  # S01 never enables execution, even when files are present.


# F01 source compatibility: same class, same Task UUID, no parallel entity.
TaskDraft = Task
