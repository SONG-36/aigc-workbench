"""Explicit task use cases; the store is the only external dependency."""

from typing import Protocol
from uuid import UUID

from .domain import TaskDraft


class TaskStore(Protocol):
    def save(self, task: TaskDraft) -> None: ...
    def get(self, task_id: UUID) -> TaskDraft | None: ...
    def list(self) -> list[TaskDraft]: ...


class TaskNotFound(LookupError):
    pass


class TaskService:
    def __init__(self, store: TaskStore):
        self.store = store

    def create_draft(self, title: str) -> TaskDraft:
        task = TaskDraft.create(title)
        self.store.save(task)
        return task

    def get_draft(self, task_id: UUID) -> TaskDraft:
        task = self.store.get(task_id)
        if task is None:
            raise TaskNotFound("任务草稿不存在。")
        return task

    def list_drafts(self) -> list[TaskDraft]:
        return self.store.list()
