"""Business invariants without HTTP, database or model dependencies."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4


class InvalidTask(ValueError):
    pass


@dataclass(frozen=True)
class TaskDraft:
    id: UUID
    title: str
    created_at: datetime

    @classmethod
    def create(cls, title: str) -> "TaskDraft":
        title = title.strip()
        if not 1 <= len(title) <= 120:
            raise InvalidTask("任务名称须为 1～120 个字符，不能只有空白。")
        return cls(uuid4(), title, datetime.now(timezone.utc))

    @property
    def mode(self) -> str:
        return "quick_a"

    @property
    def status(self) -> str:
        return "draft"

    @property
    def can_generate(self) -> bool:
        # This entity is only an unprepared draft, never a production-ready task.
        return False
