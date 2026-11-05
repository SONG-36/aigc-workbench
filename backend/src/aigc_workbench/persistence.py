"""SQLite migration and transaction implementation. No file or HTTP work."""

from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import UUID

from .domain import Asset, AssetRole, ImageMetadata, ImageSlot, SlotKind, Task


V1_TASKS = """CREATE TABLE task_drafts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
    mode TEXT NOT NULL CHECK(mode = 'quick_a'),
    status TEXT NOT NULL CHECK(status = 'draft'),
    created_at TEXT NOT NULL
)"""

V2_TABLES = [
    """CREATE TABLE task_creations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL UNIQUE REFERENCES task_drafts(id)
    )""",
    """CREATE TABLE assets (
        id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES task_drafts(id),
        operation_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('product_image', 'marketing_reference')),
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
        media_type TEXT NOT NULL,
        width INTEGER NOT NULL CHECK(width > 0),
        height INTEGER NOT NULL CHECK(height > 0),
        created_at TEXT NOT NULL,
        UNIQUE(task_id, operation_id), UNIQUE(id, task_id)
    )""",
    """CREATE TABLE image_slots (
        id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES task_drafts(id),
        operation_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('main','comparison','selling_point','scene')),
        position INTEGER NOT NULL CHECK(position >= 0),
        reference_asset_id TEXT,
        output_size TEXT NOT NULL,
        requirements TEXT NOT NULL,
        UNIQUE(task_id, operation_id), UNIQUE(task_id, position),
        FOREIGN KEY(reference_asset_id, task_id) REFERENCES assets(id, task_id)
    )""",
    """CREATE UNIQUE INDEX fixed_slot_kind ON image_slots(task_id, kind)
       WHERE kind IN ('main', 'comparison')""",
]

# S02 execution records share the DB but never update Task/Asset/Slot business rows.
V3_TABLES = [
    """CREATE TABLE c01_snapshots (
        id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL REFERENCES task_drafts(id),
        asset_id TEXT NOT NULL, created_at TEXT NOT NULL,
        body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL,
        FOREIGN KEY(asset_id,task_id) REFERENCES assets(id,task_id)
    )""",
    """CREATE TABLE c01_runs (
        id TEXT PRIMARY KEY NOT NULL, snapshot_id TEXT NOT NULL REFERENCES c01_snapshots(id),
        operation_id TEXT NOT NULL UNIQUE, adapter_mode TEXT NOT NULL CHECK(adapter_mode IN ('success','fail')),
        status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED')),
        evidence_status TEXT NOT NULL CHECK(evidence_status IN ('PENDING','AVAILABLE','MISSING')),
        created_at TEXT NOT NULL, finished_at TEXT, error_json TEXT
    )""",
    """CREATE TABLE c01_attempts (
        id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL UNIQUE REFERENCES c01_runs(id),
        status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED')),
        started_at TEXT NOT NULL, finished_at TEXT,
        actual_test_input_json TEXT, raw_result_json TEXT, error_json TEXT
    )""",
    """CREATE TABLE c01_evidence (
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES c01_runs(id),
        body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL
    )""",
]
for _table in ("c01_snapshots", "c01_evidence"):
    for _action in ("UPDATE", "DELETE"):
        V3_TABLES.append(f"""CREATE TRIGGER {_table}_immutable_{_action.lower()}
            BEFORE {_action} ON {_table} BEGIN SELECT RAISE(ABORT, 'immutable S02 evidence'); END""")
for _table in ("c01_runs", "c01_attempts"):
    V3_TABLES.append(f"""CREATE TRIGGER {_table}_terminal
        BEFORE UPDATE ON {_table} WHEN OLD.status != 'RUNNING'
        BEGIN SELECT RAISE(ABORT, 'terminal S02 record'); END""")
    V3_TABLES.append(f"""CREATE TRIGGER {_table}_retain
        BEFORE DELETE ON {_table} BEGIN SELECT RAISE(ABORT, 'retain S02 history'); END""")


# Separate real-model evidence: the S02 tables and their TEST_ONLY semantics stay intact.
V4_TABLES = [
    """CREATE TABLE c01_model_runs (
        id TEXT PRIMARY KEY NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
        operation_id TEXT NOT NULL UNIQUE,
        snapshot_id TEXT NOT NULL REFERENCES c01_snapshots(id), request_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')),
        body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL
    )""",
    """CREATE TRIGGER c01_model_runs_terminal BEFORE UPDATE ON c01_model_runs
       WHEN OLD.status != 'RUNNING' BEGIN SELECT RAISE(ABORT, 'terminal C01 model evidence'); END""",
    """CREATE TRIGGER c01_model_runs_retain BEFORE DELETE ON c01_model_runs
       BEGIN SELECT RAISE(ABORT, 'retain C01 model evidence'); END""",
]

# C06 adds only durable, unapproved image versions, not a Review/Pass system.
V5_TABLES = [
    """CREATE TABLE image_versions (
        id TEXT PRIMARY KEY NOT NULL, candidate_id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL REFERENCES task_drafts(id),
        slot_id TEXT NOT NULL REFERENCES image_slots(id),
        run_id TEXT NOT NULL UNIQUE REFERENCES c01_model_runs(id),
        status TEXT NOT NULL CHECK(status = 'CANDIDATE_REVIEW_PENDING'),
        body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL
    )""",
    """CREATE TRIGGER image_versions_immutable_update BEFORE UPDATE ON image_versions
       BEGIN SELECT RAISE(ABORT, 'immutable candidate image version'); END""",
    """CREATE TRIGGER image_versions_immutable_delete BEFORE DELETE ON image_versions
       BEGIN SELECT RAISE(ABORT, 'retain candidate image version'); END""",
]

# A durable dispatch receipt uses the existing C06 operation/Run; it is not another Runtime.
V6_TABLES = [
    """CREATE TABLE c06_dispatches (
        operation_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES task_drafts(id),
        slot_id TEXT NOT NULL REFERENCES image_slots(id),
        snapshot_id TEXT NOT NULL REFERENCES c01_snapshots(id),
        quote_token TEXT NOT NULL, max_cost_usd TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')),
        execution_mode TEXT NOT NULL CHECK(execution_mode IN ('REAL','TEST_SUBSTITUTE')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error_json TEXT
    )""",
    """CREATE UNIQUE INDEX c06_active_slot ON c06_dispatches(task_id,slot_id)
       WHERE state IN ('QUEUED','RUNNING','OUTCOME_UNKNOWN')""",
]


def asset_from_row(row: sqlite3.Row) -> Asset:
    return Asset(UUID(row["id"]), UUID(row["task_id"]), AssetRole(row["role"]), row["filename"],
                 ImageMetadata(row["sha256"], row["size_bytes"], row["media_type"],
                               row["width"], row["height"]), datetime.fromisoformat(row["created_at"]))


def slot_from_row(row: sqlite3.Row) -> ImageSlot:
    return ImageSlot(UUID(row["id"]), UUID(row["task_id"]), SlotKind(row["kind"]), row["position"],
                     UUID(row["reference_asset_id"]) if row["reference_asset_id"] else None,
                     row["output_size"], row["requirements"])


def load_task(connection: sqlite3.Connection, task_id: UUID) -> Task | None:
    row = connection.execute("SELECT * FROM task_drafts WHERE id = ?", (str(task_id),)).fetchone()
    if row is None:
        return None
    assets = connection.execute("SELECT * FROM assets WHERE task_id = ? ORDER BY created_at, id",
                                (str(task_id),)).fetchall()
    slots = connection.execute("SELECT * FROM image_slots WHERE task_id = ? ORDER BY position",
                               (str(task_id),)).fetchall()
    return Task(UUID(row["id"]), row["title"], datetime.fromisoformat(row["created_at"]),
                row["mode"], row["status"], tuple(map(asset_from_row, assets)),
                tuple(map(slot_from_row, slots)))


class SQLiteTaskStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        """Only this explicit startup entry may create a database."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path, timeout=5)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2, 3, 4, 5, 6}:
                raise RuntimeError(f"Unsupported database schema version: {version}")
            if version == 0:
                connection.execute(V1_TASKS)
                version = 1
            if version == 1:
                self._upgrade_v1(connection)
                version = 2
            if version == 2:
                for statement in V3_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 3")
                version = 3
            if version == 3:
                for statement in V4_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 4")
                version = 4
            if version == 4:
                for statement in V5_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 5")
                version = 5
            if version == 5:
                for statement in V6_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 6")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Database foreign-key validation failed")

    def _upgrade_v1(self, connection: sqlite3.Connection) -> None:
        # Additive migration: existing task rows are never recreated or assigned new IDs.
        connection.execute("SELECT id, title, mode, status, created_at FROM task_drafts LIMIT 1")
        for statement in V2_TABLES:
            connection.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        # mode=rw fails if missing: a normal GET must never silently create a DB.
        connection = sqlite3.connect(self.database_path.resolve().as_uri() + "?mode=rw",
                                     uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self, *, write: bool = False):
        """Infrastructure-only connection boundary shared by business and S02 stores."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection

    @contextmanager
    def write(self):
        with self.connection(write=True) as connection:
            yield SQLiteChanges(connection)

    def get(self, task_id: UUID) -> Task | None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            return load_task(connection, task_id)

    def list(self) -> list[Task]:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT id FROM task_drafts ORDER BY created_at DESC, id DESC")
            ids = [UUID(row["id"]) for row in rows]
            return [load_task(connection, task_id) for task_id in ids]

    def asset_ids(self) -> set[UUID]:
        with closing(self._connect()) as connection:
            return {UUID(row[0]) for row in connection.execute("SELECT id FROM assets")}


class SQLiteChanges:
    """One actual write transaction; never shared across requests."""
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_task(self, task_id: UUID) -> Task | None:
        return load_task(self.connection, task_id)

    def task_creation(self, operation_id: UUID) -> Task | None:
        row = self.connection.execute("SELECT task_id FROM task_creations WHERE operation_id = ?",
                                      (str(operation_id),)).fetchone()
        return self.get_task(UUID(row[0])) if row else None

    def add_task(self, task: Task, operation_id: UUID) -> None:
        self.connection.execute(
            "INSERT INTO task_drafts (id,title,mode,status,created_at) VALUES (?,?,?,?,?)",
            (str(task.id), task.title, task.mode, task.status, task.created_at.isoformat()))
        self.connection.execute("INSERT INTO task_creations (operation_id,task_id) VALUES (?,?)",
                                (str(operation_id), str(task.id)))

    def asset_upload(self, task_id: UUID, operation_id: UUID) -> Asset | None:
        row = self.connection.execute("SELECT * FROM assets WHERE task_id = ? AND operation_id = ?",
                                      (str(task_id), str(operation_id))).fetchone()
        return asset_from_row(row) if row else None

    def add_asset(self, asset: Asset, operation_id: UUID) -> None:
        m = asset.image
        self.connection.execute(
            """INSERT INTO assets
               (id,task_id,operation_id,role,filename,sha256,size_bytes,media_type,width,height,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (str(asset.id), str(asset.task_id), str(operation_id), asset.role, asset.filename,
             m.sha256, m.size_bytes, m.media_type, m.width, m.height, asset.created_at.isoformat()))

    def slot_creation(self, task_id: UUID, operation_id: UUID) -> ImageSlot | None:
        row = self.connection.execute(
            "SELECT * FROM image_slots WHERE task_id = ? AND operation_id = ?",
            (str(task_id), str(operation_id))).fetchone()
        return slot_from_row(row) if row else None

    def add_slot(self, slot: ImageSlot, operation_id: UUID) -> None:
        self.connection.execute(
            """INSERT INTO image_slots
               (id,task_id,operation_id,kind,position,reference_asset_id,output_size,requirements)
               VALUES (?,?,?,?,?,?,?,?)""",
            (str(slot.id), str(slot.task_id), str(operation_id), slot.kind, slot.position,
             str(slot.reference_asset_id) if slot.reference_asset_id else None,
             slot.output_size, slot.requirements))

    def update_slot(self, slot: ImageSlot) -> None:
        self.connection.execute(
            """UPDATE image_slots SET reference_asset_id = ?, output_size = ?, requirements = ?
               WHERE id = ? AND task_id = ?""",
            (str(slot.reference_asset_id) if slot.reference_asset_id else None,
             slot.output_size, slot.requirements, str(slot.id), str(slot.task_id)))
