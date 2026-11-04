"""Use cases coordinate domain rules, DB transactions and real files."""

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from .domain import Asset, AssetRole, Conflict, ImageMetadata, ImageSlot, InvalidTask, SlotKind, Task


class TaskNotFound(LookupError):
    pass


class StorageUnavailable(OSError):
    pass


class TaskChanges(Protocol):
    def get_task(self, task_id: UUID) -> Task | None: ...
    def task_creation(self, operation_id: UUID) -> Task | None: ...
    def add_task(self, task: Task, operation_id: UUID) -> None: ...
    def asset_upload(self, task_id: UUID, operation_id: UUID) -> Asset | None: ...
    def add_asset(self, asset: Asset, operation_id: UUID) -> None: ...
    def slot_creation(self, task_id: UUID, operation_id: UUID) -> ImageSlot | None: ...
    def add_slot(self, slot: ImageSlot, operation_id: UUID) -> None: ...
    def update_slot(self, slot: ImageSlot) -> None: ...


class TaskStore(Protocol):
    def write(self) -> AbstractContextManager[TaskChanges]: ...
    def get(self, task_id: UUID) -> Task | None: ...
    def list(self) -> list[Task]: ...
    def asset_ids(self) -> set[UUID]: ...


class ImageFiles(Protocol):
    def stage(self, asset_id: UUID, content: bytes, media_type: str) -> ImageMetadata: ...
    def publish(self, asset_id: UUID) -> None: ...
    def discard_staging(self, asset_id: UUID) -> None: ...
    def read(self, asset: Asset) -> bytes: ...
    def orphan_ids(self, known_ids: set[UUID]) -> list[str]: ...


def require_task(task: Task | None) -> Task:
    if task is None:
        raise TaskNotFound("任务不存在。")
    return task


class TaskService:
    def __init__(self, store: TaskStore, files: ImageFiles):
        self.store, self.files = store, files

    def create_draft(self, title: str, operation_id: UUID) -> Task:
        title = Task.normalize_title(title)
        with self.store.write() as changes:
            previous = changes.task_creation(operation_id)
            if previous:
                if previous.title != title:
                    raise Conflict("同一创建操作不能用于不同任务内容。")
                return previous
            task = Task.create(title)
            changes.add_task(task, operation_id)
        return task

    def get_draft(self, task_id: UUID) -> Task:
        return require_task(self.store.get(task_id))

    def list_drafts(self) -> list[Task]:
        return self.store.list()

    def upload_asset(self, task_id: UUID, operation_id: UUID, role: AssetRole,
                     filename: str, content: bytes, media_type: str) -> Asset:
        self.get_draft(task_id)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not filename or len(filename) > 180:
            raise InvalidTask("文件名须为 1～180 字符。")
        asset_id = uuid4()
        try:
            image = self.files.stage(asset_id, content, media_type)
            with self.store.write() as changes:
                require_task(changes.get_task(task_id))
                previous = changes.asset_upload(task_id, operation_id)
                if previous:
                    if not previous.same_upload(role, filename, image):
                        raise Conflict("同一上传操作不能用于不同文件或素材角色。")
                    self.files.read(previous)
                    return previous
                asset = Asset(asset_id, task_id, role, filename, image, datetime.now(timezone.utc))
                changes.add_asset(asset, operation_id)
                self.files.publish(asset_id)
            return asset
        finally:
            self.files.discard_staging(asset_id)
            # Never remove a published file after an ambiguous commit error.
            # Unreferenced UUID files remain identifiable by orphan_files().

    def read_asset(self, task_id: UUID, asset_id: UUID) -> tuple[Asset, bytes]:
        task = self.get_draft(task_id)
        asset = next((a for a in task.assets if a.id == asset_id), None)
        if asset is None:
            raise TaskNotFound("当前任务没有该素材。")
        return asset, self.files.read(asset)

    def create_slot(self, task_id: UUID, operation_id: UUID, kind: SlotKind) -> ImageSlot:
        with self.store.write() as changes:
            task = require_task(changes.get_task(task_id))
            previous = changes.slot_creation(task_id, operation_id)
            if previous:
                if previous.kind != kind:
                    raise Conflict("同一图位创建操作不能用于不同图位类型。")
                return previous
            slot = task.new_slot(kind)
            changes.add_slot(slot, operation_id)
        return slot

    def configure_slot(self, task_id: UUID, slot_id: UUID, reference_id: UUID | None,
                       output_size: str, requirements: str) -> ImageSlot:
        with self.store.write() as changes:
            task = require_task(changes.get_task(task_id))
            slot = next((s for s in task.slots if s.id == slot_id), None)
            if slot is None:
                raise TaskNotFound("当前任务没有该图位。")
            slot = slot.configure(task, reference_id, output_size, requirements)
            changes.update_slot(slot)
        return slot

    def orphan_files(self) -> list[str]:
        return self.files.orphan_ids(self.store.asset_ids())
