"""SQLite migration and transaction implementation. No file or HTTP work."""

from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import UUID

from .domain import ListingMaterials, OverallRequirement, Asset, AssetRole, ImageMetadata, ImageSlot, SlotKind, Task


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

# Human reviews are append-only. Only an explicit PASS updates a slot's delivery pointer.
V7_TABLES = [
    """CREATE TABLE image_reviews (
        id TEXT PRIMARY KEY NOT NULL, operation_id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL REFERENCES task_drafts(id), slot_id TEXT NOT NULL REFERENCES image_slots(id),
        image_version_id TEXT NOT NULL REFERENCES image_versions(id),
        decision TEXT NOT NULL CHECK(decision IN ('PASS','REJECT')),
        reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL, feedback TEXT NOT NULL,
        image_sha256 TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('EXPLICIT_OPERATOR','TEST_REVIEW')),
        body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL
    )""",
    """CREATE TRIGGER review_scope BEFORE INSERT ON image_reviews
       WHEN NOT EXISTS (SELECT 1 FROM image_versions v WHERE v.id=NEW.image_version_id
         AND v.task_id=NEW.task_id AND v.slot_id=NEW.slot_id)
       BEGIN SELECT RAISE(ABORT, 'review version scope mismatch'); END""",
    """CREATE TABLE slot_deliveries (
        task_id TEXT NOT NULL REFERENCES task_drafts(id), slot_id TEXT PRIMARY KEY NOT NULL REFERENCES image_slots(id),
        image_version_id TEXT NOT NULL REFERENCES image_versions(id),
        review_id TEXT NOT NULL REFERENCES image_reviews(id), updated_at TEXT NOT NULL
    )""",
]
for _action in ("UPDATE", "DELETE"):
    V7_TABLES.append(f"""CREATE TRIGGER image_reviews_immutable_{_action.lower()} BEFORE {_action} ON image_reviews
        BEGIN SELECT RAISE(ABORT, 'immutable human review'); END""")
for _action in ("INSERT", "UPDATE"):
    V7_TABLES.append(f"""CREATE TRIGGER legal_delivery_{_action.lower()} BEFORE {_action} ON slot_deliveries
        WHEN NOT EXISTS (SELECT 1 FROM image_reviews r WHERE r.id=NEW.review_id AND r.decision='PASS'
          AND r.task_id=NEW.task_id AND r.slot_id=NEW.slot_id AND r.image_version_id=NEW.image_version_id)
        BEGIN SELECT RAISE(ABORT, 'delivery requires matching explicit Pass'); END""")

V8_TABLES = ["ALTER TABLE c06_dispatches ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'"]


V9_TABLES = (
    "ALTER TABLE task_creations ADD COLUMN initial_overall_requirement TEXT NOT NULL DEFAULT ''",
    """CREATE TABLE task_requirements (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task_drafts(id),
        version INTEGER NOT NULL CHECK(version > 0), text TEXT NOT NULL CHECK(length(text) <= 2000),
        saved_at TEXT NOT NULL, operation_id TEXT NOT NULL,
        UNIQUE(task_id,version), UNIQUE(task_id,operation_id))""",
    """CREATE TRIGGER task_requirements_immutable_update BEFORE UPDATE ON task_requirements
       BEGIN SELECT RAISE(ABORT, 'retain requirement revisions'); END""",
    """CREATE TRIGGER task_requirements_immutable_delete BEFORE DELETE ON task_requirements
       BEGIN SELECT RAISE(ABORT, 'retain requirement revisions'); END""",
)


def requirement_from_row(row):
    return OverallRequirement(UUID(row["id"]), UUID(row["task_id"]), row["version"], row["text"],
                              datetime.fromisoformat(row["saved_at"]))


def asset_from_row(row: sqlite3.Row) -> Asset:
    return Asset(UUID(row["id"]), UUID(row["task_id"]), AssetRole(row["role"]), row["filename"],
                 ImageMetadata(row["sha256"], row["size_bytes"], row["media_type"],
                               row["width"], row["height"]), datetime.fromisoformat(row["created_at"]))


def slot_from_row(row: sqlite3.Row) -> ImageSlot:
    return ImageSlot(UUID(row["id"]), UUID(row["task_id"]), SlotKind(row["kind"]), row["position"],
                     UUID(row["reference_asset_id"]) if row["reference_asset_id"] else None,
                     row["output_size"], row["requirements"], bool(row["archived"]), row['version'])


def task_has_unresolved_execution(connection: sqlite3.Connection, task_id: UUID) -> bool:
    # 连同旧执行入口检查；结果未知也不能通过删除隐藏后重新付费。
    return connection.execute("""SELECT 1 FROM c06_dispatches
        WHERE task_id=? AND state IN ('QUEUED','RUNNING','OUTCOME_UNKNOWN')
        UNION ALL SELECT 1 FROM c01_model_runs r JOIN c01_snapshots s ON s.id=r.snapshot_id
        WHERE s.task_id=? AND r.status IN ('RUNNING','OUTCOME_UNKNOWN')
        UNION ALL SELECT 1 FROM c01_runs r JOIN c01_snapshots s ON s.id=r.snapshot_id
        WHERE s.task_id=? AND r.status='RUNNING' LIMIT 1""", (str(task_id),)*3).fetchone() is not None


def load_task(connection: sqlite3.Connection, task_id: UUID) -> Task | None:
    row = connection.execute("SELECT * FROM task_drafts WHERE id = ?", (str(task_id),)).fetchone()
    if row is None:
        return None
    assets = connection.execute("SELECT * FROM assets WHERE task_id = ? ORDER BY created_at, id",
                                (str(task_id),)).fetchall()
    slots = connection.execute("SELECT * FROM image_slots WHERE task_id = ? ORDER BY position",
                               (str(task_id),)).fetchall()
    requirement = connection.execute("SELECT * FROM task_requirements WHERE task_id=? ORDER BY version DESC LIMIT 1",
                                     (str(task_id),)).fetchone()
    materials = (connection.execute('SELECT * FROM task_listing_materials WHERE task_id=? ORDER BY version DESC LIMIT 1',
                                    (str(task_id),)).fetchone() if row['mode'] == 'listing' else None)
    return Task(UUID(row["id"]), row["title"], datetime.fromisoformat(row["created_at"]),
                row["mode"], row["status"], tuple(map(asset_from_row, assets)),
                tuple(map(slot_from_row, slots)), requirement_from_row(requirement) if requirement else None,
                datetime.fromisoformat(row['deleted_at']) if row['deleted_at'] else None,
                task_has_unresolved_execution(connection, task_id), listing_materials_from_row(materials) if materials else None)


def listing_materials_from_row(row):
    return ListingMaterials(UUID(row['id']), UUID(row['task_id']), row['version'], row['listing_text'],
                            row['supplemental_text'], datetime.fromisoformat(row['saved_at']))


class SQLiteTaskStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        """Only this explicit startup entry may create a database."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path, timeout=5)) as connection, connection:
            # SQLite requires this before the transaction for a CHECK-preserving table migration.
            # The write transaction and foreign_key_check below protect all existing relations.
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}:
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
                version = 6
            if version == 6:
                for statement in V7_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 7")
                version = 7
            if version == 7:
                # Extend the existing durable dispatch, rather than adding another execution system.
                for statement in V8_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 8")
                version = 8
            if version == 8:
                for statement in V9_TABLES:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 9")
                version = 9
            if version == 9:
                self._upgrade_v10(connection)
                connection.execute("PRAGMA user_version = 10")
                version = 10
            if version == 10:
                if 'version' not in {r[1] for r in connection.execute('PRAGMA table_info(image_slots)')}:
                    connection.execute('ALTER TABLE image_slots ADD COLUMN version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0)')
                connection.execute('PRAGMA user_version = 11')
                version = 11
            if version == 11:
                if 'deleted_at' not in {r[1] for r in connection.execute('PRAGMA table_info(task_drafts)')}:
                    connection.execute('ALTER TABLE task_drafts ADD COLUMN deleted_at TEXT')
                if 'initial_title' not in {r[1] for r in connection.execute('PRAGMA table_info(task_creations)')}:
                    connection.execute('ALTER TABLE task_creations ADD COLUMN initial_title TEXT')
                # 原始创建标题不能随重命名改变，否则原 Operation 的重放会失去幂等性。
                connection.execute('UPDATE task_creations SET initial_title=(SELECT title FROM task_drafts WHERE task_drafts.id=task_creations.task_id) WHERE initial_title IS NULL')
                connection.execute('PRAGMA user_version = 12')
                version = 12
            if version == 12:
                self._upgrade_v13(connection)
                connection.execute('PRAGMA user_version = 13')
                version = 13
            if version == 13:
                self._upgrade_v14(connection)
                connection.execute('PRAGMA user_version = 14')
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Database foreign-key validation failed")

    def _upgrade_v14(self, connection: sqlite3.Connection) -> None:
        # 仅扩展 Task.mode 和版本化商品文字；旧素材、Run、版本与审核表保持原样。
        definition = V1_TASKS.replace('CREATE TABLE task_drafts (', 'CREATE TABLE task_drafts_next (')
        definition = definition.replace("mode = 'quick_a'", "mode IN ('quick_a','quick_b','listing')")
        definition = definition.replace('created_at TEXT NOT NULL', 'created_at TEXT NOT NULL, deleted_at TEXT')
        connection.execute(definition)
        fields = 'id,title,mode,status,created_at,deleted_at'
        connection.execute(f'INSERT INTO task_drafts_next ({fields}) SELECT {fields} FROM task_drafts')
        connection.execute('DROP TABLE task_drafts')
        connection.execute('ALTER TABLE task_drafts_next RENAME TO task_drafts')
        for column in ('initial_listing_text', 'initial_supplemental_text'):
            if column not in {r[1] for r in connection.execute('PRAGMA table_info(task_creations)')}:
                connection.execute(f"ALTER TABLE task_creations ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        connection.execute('''CREATE TABLE IF NOT EXISTS task_listing_materials (
            id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL REFERENCES task_drafts(id),
            version INTEGER NOT NULL CHECK(version > 0), listing_text TEXT NOT NULL CHECK(length(trim(listing_text)) BETWEEN 1 AND 50000),
            supplemental_text TEXT NOT NULL CHECK(length(supplemental_text) <= 20000), saved_at TEXT NOT NULL,
            operation_id TEXT NOT NULL, UNIQUE(task_id,version), UNIQUE(task_id,operation_id))''')
        for action in ('UPDATE','DELETE'):
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS task_listing_materials_immutable_{action.lower()} BEFORE {action} ON task_listing_materials BEGIN SELECT RAISE(ABORT,'immutable Listing materials'); END")

    def _upgrade_v13(self, connection: sqlite3.Connection) -> None:
        # 仅扩展现有 Task.mode 的 CHECK。保留 UUID、软删除及全部子表关系，失败整笔回滚。
        definition = V1_TASKS.replace('CREATE TABLE task_drafts (', 'CREATE TABLE task_drafts_next (')
        definition = definition.replace("mode = 'quick_a'", "mode IN ('quick_a','quick_b')")
        definition = definition.replace('created_at TEXT NOT NULL', 'created_at TEXT NOT NULL, deleted_at TEXT')
        connection.execute(definition)
        fields = 'id,title,mode,status,created_at,deleted_at'
        connection.execute(f'INSERT INTO task_drafts_next ({fields}) SELECT {fields} FROM task_drafts')
        connection.execute('DROP TABLE task_drafts')
        connection.execute('ALTER TABLE task_drafts_next RENAME TO task_drafts')

    def _upgrade_v10(self, connection: sqlite3.Connection) -> None:
        # Rebuild only this table's CHECK. UUIDs, positions, references and child FK names stay unchanged.
        definition = V2_TABLES[2].replace("CREATE TABLE image_slots (", "CREATE TABLE image_slots_next (")
        definition = definition.replace("'main','comparison','selling_point','scene'", "'main','dimensions','comparison','selling_point','scene'")
        definition = definition.replace("requirements TEXT NOT NULL,", "requirements TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),")
        fields = 'id,task_id,operation_id,kind,position,reference_asset_id,output_size,requirements'
        old_columns = {r[1] for r in connection.execute('PRAGMA table_info(image_slots)')}
        if 'version' in old_columns:
            # 保留已有版本字段，兼容带附加字段的旧表恢复/迁移。
            definition = definition.replace('archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),',
                'archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),')
        connection.execute(definition)
        archived = 'archived' if 'archived' in old_columns else '0'
        extra = ',version' if 'version' in old_columns else ''
        connection.execute(f'INSERT INTO image_slots_next ({fields},archived{extra}) SELECT {fields},{archived}{extra} FROM image_slots')
        connection.execute('DROP TABLE image_slots')
        connection.execute('ALTER TABLE image_slots_next RENAME TO image_slots')
        connection.execute(V2_TABLES[3].replace("'main', 'comparison'", "'main', 'dimensions', 'comparison'"))
        connection.execute("""CREATE TABLE IF NOT EXISTS slot_removals (
            task_id TEXT NOT NULL REFERENCES task_drafts(id), slot_id TEXT NOT NULL UNIQUE REFERENCES image_slots(id),
            operation_id TEXT NOT NULL, removed_at TEXT NOT NULL, PRIMARY KEY(task_id,operation_id))""")

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
            rows = connection.execute("SELECT id FROM task_drafts WHERE deleted_at IS NULL ORDER BY created_at DESC, id DESC")
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

    def add_task(self, task: Task, operation_id: UUID, overall_requirement: str = "", listing_text: str = "", supplemental_text: str = "") -> None:
        self.connection.execute(
            "INSERT INTO task_drafts (id,title,mode,status,created_at) VALUES (?,?,?,?,?)",
            (str(task.id), task.title, task.mode, task.status, task.created_at.isoformat()))
        self.connection.execute("INSERT INTO task_creations (operation_id,task_id,initial_overall_requirement,initial_title,initial_listing_text,initial_supplemental_text) VALUES (?,?,?,?,?,?)",
                                (str(operation_id), str(task.id), overall_requirement, task.title, listing_text, supplemental_text))

    def creation_materials(self, operation_id: UUID) -> tuple[str, str]:
        row = self.connection.execute('SELECT initial_listing_text,initial_supplemental_text FROM task_creations WHERE operation_id=?', (str(operation_id),)).fetchone()
        return tuple(row)

    def listing_material_operation(self, task_id: UUID, operation_id: UUID):
        row = self.connection.execute('SELECT * FROM task_listing_materials WHERE task_id=? AND operation_id=?', (str(task_id),str(operation_id))).fetchone()
        return listing_materials_from_row(row) if row else None

    def add_listing_materials(self, materials: ListingMaterials, operation_id: UUID) -> None:
        self.connection.execute('INSERT INTO task_listing_materials VALUES (?,?,?,?,?,?,?)',
            (str(materials.id), str(materials.task_id), materials.version, materials.listing_text, materials.supplemental_text,
             materials.saved_at.isoformat(), str(operation_id)))

    def creation_title(self, operation_id: UUID) -> str:
        return self.connection.execute('SELECT initial_title FROM task_creations WHERE operation_id=?', (str(operation_id),)).fetchone()[0]

    def rename_task(self, task_id: UUID, title: str, expected_title: str) -> None:
        changed = self.connection.execute('UPDATE task_drafts SET title=? WHERE id=? AND title=? AND deleted_at IS NULL',
                                          (title, str(task_id), expected_title)).rowcount
        if changed != 1:
            from .domain import Conflict
            raise Conflict('任务名称已变化或任务已删除，请刷新列表后再编辑。')

    def soft_delete_task(self, task_id: UUID, deleted_at: str) -> None:
        self.connection.execute('UPDATE task_drafts SET deleted_at=? WHERE id=? AND deleted_at IS NULL', (deleted_at, str(task_id)))

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
        changed = self.connection.execute(
            """UPDATE image_slots SET reference_asset_id = ?, output_size = ?, requirements = ?, version = version + 1
               WHERE id = ? AND task_id = ? AND version = ?""",
            (str(slot.reference_asset_id) if slot.reference_asset_id else None,
             slot.output_size, slot.requirements, str(slot.id), str(slot.task_id), slot.version)).rowcount
        if changed != 1:
            from .domain import Conflict
            raise Conflict('图位数据已变化，请重新读取并核对后保存；未覆盖服务器配置。')


    def creation_requirement(self, operation_id: UUID) -> str:
        return self.connection.execute("SELECT initial_overall_requirement FROM task_creations WHERE operation_id=?",
                                       (str(operation_id),)).fetchone()[0]

    def requirement_operation(self, task_id: UUID, operation_id: UUID):
        row = self.connection.execute("SELECT * FROM task_requirements WHERE task_id=? AND operation_id=?",
                                      (str(task_id), str(operation_id))).fetchone()
        return requirement_from_row(row) if row else None

    def add_requirement(self, requirement: OverallRequirement, operation_id: UUID):
        self.connection.execute("INSERT INTO task_requirements VALUES (?,?,?,?,?,?)",
            (str(requirement.id), str(requirement.task_id), requirement.version, requirement.text,
             requirement.saved_at.isoformat(), str(operation_id)))


    def slot_removal(self, task_id: UUID, operation_id: UUID):
        row = self.connection.execute('SELECT slot_id FROM slot_removals WHERE task_id=? AND operation_id=?',
                                      (str(task_id),str(operation_id))).fetchone()
        return UUID(row[0]) if row else None

    def archive_slot(self, task_id: UUID, slot_id: UUID, operation_id: UUID, removed_at: str):
        self.connection.execute('UPDATE image_slots SET archived=1 WHERE id=? AND task_id=?', (str(slot_id),str(task_id)))
        self.connection.execute('INSERT INTO slot_removals VALUES (?,?,?,?)', (str(task_id),str(slot_id),str(operation_id),removed_at))


    def slot_has_active_execution(self, task_id: UUID, slot_id: UUID) -> bool:
        return self.connection.execute("SELECT 1 FROM c06_dispatches WHERE task_id=? AND slot_id=? AND state IN ('QUEUED','RUNNING','OUTCOME_UNKNOWN') LIMIT 1",
                                       (str(task_id),str(slot_id))).fetchone() is not None
