"""SQLite is an infrastructure detail; each operation owns its connection."""

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import UUID

from .domain import TaskDraft


class SQLiteTaskStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                connection.execute("""
                    CREATE TABLE task_drafts (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
                        mode TEXT NOT NULL CHECK(mode = 'quick_a'),
                        status TEXT NOT NULL CHECK(status = 'draft'),
                        created_at TEXT NOT NULL
                    )
                """)
                connection.execute("PRAGMA user_version = 1")
            elif version != 1:
                raise RuntimeError(f"Unsupported database schema version: {version}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, task: TaskDraft) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO task_drafts VALUES (?, ?, ?, ?, ?)",
                (str(task.id), task.title, task.mode, task.status, task.created_at.isoformat()),
            )

    def get(self, task_id: UUID) -> TaskDraft | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM task_drafts WHERE id = ?", (str(task_id),)
            ).fetchone()
        return self._task(row) if row else None

    def list(self) -> list[TaskDraft]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM task_drafts ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [self._task(row) for row in rows]

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskDraft:
        return TaskDraft(UUID(row["id"]), row["title"], datetime.fromisoformat(row["created_at"]))
