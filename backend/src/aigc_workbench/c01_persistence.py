"""Immutable S02 snapshots/evidence and small synchronous Run/Attempt persistence."""

from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from .c01_context import C01Error, canonical, fingerprint, now
from .file_storage import MAX_UPLOAD_BYTES
from .persistence import SQLiteTaskStore


class C01EvidenceStore:
    def __init__(self, database: SQLiteTaskStore, data_dir: Path):
        self.database = database
        self.images = data_dir / "c01" / "snapshot_images"

    def save_snapshot(self, snapshot: dict, image: bytes) -> dict:
        snapshot_id = UUID(snapshot["id"])
        if sha256(image).hexdigest() != snapshot["context"]["capability_specific_inputs"]["sha256"]:
            raise C01Error("SNAPSHOT_IMAGE_MISMATCH", "SNAPSHOT_SAVE", "图片字节与来源哈希不一致。")
        self.images.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Copy has execution identity, not a second business Asset identity.
        target = self.images / snapshot_id.hex
        with self.database.connection(write=True) as db:
            db.execute(
                """INSERT INTO c01_snapshots
                   (id,task_id,asset_id,created_at,body_json,body_sha256) VALUES (?,?,?,?,?,?)""",
                (str(snapshot_id), snapshot["task_id"], snapshot["product_asset_id"],
                 snapshot["created_at"], canonical(snapshot), fingerprint(snapshot)))
            with target.open("xb") as stream:
                stream.write(image); stream.flush(); os.fsync(stream.fileno())
            target.chmod(0o400)
            descriptor = os.open(self.images, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        # If DB commit fails after copying, retain identifiable UUID orphan; never overwrite.
        return self.load_snapshot(snapshot_id)

    def load_snapshot(self, snapshot_id: UUID) -> dict:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM c01_snapshots WHERE id=?", (str(snapshot_id),)).fetchone()
        if row is None:
            raise C01Error("SNAPSHOT_NOT_FOUND", "EVIDENCE_QUERY", "Execution Snapshot 不存在。")
        body = json.loads(row["body_json"])
        if (fingerprint(body) != row["body_sha256"] or body["id"] != row["id"]
                or body["task_id"] != row["task_id"] or body["product_asset_id"] != row["asset_id"]):
            raise C01Error("SNAPSHOT_INTEGRITY_FAILED", "EVIDENCE_QUERY", "快照记录不一致。")
        return body

    def read_snapshot_image(self, snapshot: dict) -> bytes:
        try:
            descriptor = os.open(self.images / UUID(snapshot["id"]).hex, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read(MAX_UPLOAD_BYTES + 1)
        except OSError as exc:
            raise C01Error("SNAPSHOT_IMAGE_UNAVAILABLE", "EXECUTION_INPUT", "固定图片副本不可读。") from exc
        source = snapshot["context"]["capability_specific_inputs"]
        if len(content) != source["size_bytes"] or sha256(content).hexdigest() != source["sha256"]:
            raise C01Error("SNAPSHOT_IMAGE_CORRUPT", "EXECUTION_INPUT", "固定图片副本完整性失败。")
        return content

    def start_run(self, snapshot_id: UUID, operation_id: UUID, adapter_mode: str) -> tuple[UUID, bool]:
        run_id, attempt_id, started = uuid4(), uuid4(), now()
        with self.database.connection(write=True) as db:
            previous = db.execute("SELECT * FROM c01_runs WHERE operation_id=?", (str(operation_id),)).fetchone()
            if previous:
                if previous["snapshot_id"] != str(snapshot_id) or previous["adapter_mode"] != adapter_mode:
                    raise C01Error("OPERATION_CONFLICT", "RUN_START", "同一执行操作不能改换快照或测试模式。")
                return UUID(previous["id"]), False
            db.execute(
                """INSERT INTO c01_runs
                   (id,snapshot_id,operation_id,adapter_mode,status,evidence_status,created_at)
                   VALUES (?,?,?,?,'RUNNING','PENDING',?)""",
                (str(run_id), str(snapshot_id), str(operation_id), adapter_mode, started))
            db.execute(
                "INSERT INTO c01_attempts (id,run_id,status,started_at) VALUES (?,?,'RUNNING',?)",
                (str(attempt_id), str(run_id), started))
        return run_id, True

    def finish_run(self, run_id: UUID, evidence: dict) -> None:
        status, finished = evidence["status"], evidence["finished_at"]
        with self.database.connection(write=True) as db:
            running = self._require_running(db, run_id)
            if (evidence["run_id"] != str(run_id) or evidence["attempt_id"] != running["attempt_id"]
                    or evidence["snapshot_id"] != running["snapshot_id"]
                    or status not in {"SUCCEEDED", "FAILED"}
                    or (status == "FAILED") != (evidence["error"] is not None)
                    or evidence["actual_model_input"] is not None
                    or evidence["model_api_call_attempted"] is not False):
                raise C01Error("EVIDENCE_STATE_INCONSISTENT", "EVIDENCE_SAVE", "不能保存不一致执行证据。")
            db.execute("INSERT INTO c01_evidence (run_id,body_json,body_sha256) VALUES (?,?,?)",
                       (str(run_id), canonical(evidence), fingerprint(evidence)))
            db.execute(
                """UPDATE c01_attempts SET status=?,finished_at=?,actual_test_input_json=?,
                   raw_result_json=?,error_json=? WHERE run_id=?""",
                (status, finished, canonical(evidence["actual_test_adapter_input"]),
                 canonical(evidence["raw_test_result"]), canonical(evidence["error"]), str(run_id)))
            db.execute(
                """UPDATE c01_runs SET status=?,evidence_status='AVAILABLE',finished_at=?,error_json=?
                   WHERE id=?""", (status, finished, canonical(evidence["error"]), str(run_id)))

    def mark_evidence_failure(self, run_id: UUID, evidence: dict, cause_type: str) -> None:
        error = {"code": "EVIDENCE_SAVE_FAILED", "stage": "EVIDENCE_SAVE",
                 "message": "执行证据未可靠保存；不能宣称执行成功。",
                 "adapter_outcome_before_save": evidence["status"], "cause_type": cause_type}
        with self.database.connection(write=True) as db:
            self._require_running(db, run_id)
            db.execute(
                """UPDATE c01_attempts SET status='FAILED',finished_at=?,actual_test_input_json=?,
                   raw_result_json=?,error_json=? WHERE run_id=?""",
                (now(), canonical(evidence["actual_test_adapter_input"]),
                 canonical(evidence["raw_test_result"]), canonical(error), str(run_id)))
            db.execute(
                """UPDATE c01_runs SET status='FAILED',evidence_status='MISSING',
                   finished_at=?,error_json=? WHERE id=?""",
                (now(), canonical(error), str(run_id)))

    @staticmethod
    def _require_running(db, run_id: UUID):
        row = db.execute(
            """SELECT r.status AS run_status,a.status AS attempt_status,r.snapshot_id,a.id AS attempt_id FROM c01_runs r
               JOIN c01_attempts a ON a.run_id=r.id WHERE r.id=?""", (str(run_id),)).fetchone()
        if not row or row["run_status"] != "RUNNING" or row["attempt_status"] != "RUNNING":
            raise C01Error("EXECUTION_STATE_INCONSISTENT", "EVIDENCE_SAVE", "执行状态不能安全完成。")
        return row

    def inspect_run(self, run_id: UUID) -> dict:
        with self.database.connection() as db:
            run = db.execute("SELECT * FROM c01_runs WHERE id=?", (str(run_id),)).fetchone()
            if run is None:
                raise C01Error("RUN_NOT_FOUND", "EVIDENCE_QUERY", "Run 不存在。")
            attempt = db.execute("SELECT * FROM c01_attempts WHERE run_id=?", (str(run_id),)).fetchone()
            row = db.execute("SELECT * FROM c01_evidence WHERE run_id=?", (str(run_id),)).fetchone()
        if not attempt or run["status"] != attempt["status"]:
            raise C01Error("EXECUTION_STATE_INCONSISTENT", "EVIDENCE_QUERY", "Run 与 Attempt 状态不一致。")
        evidence = json.loads(row["body_json"]) if row else None
        if evidence is not None and (
            fingerprint(evidence) != row["body_sha256"] or evidence["run_id"] != run["id"]
            or evidence["attempt_id"] != attempt["id"] or evidence["snapshot_id"] != run["snapshot_id"]
            or evidence["status"] != run["status"]
        ):
            raise C01Error("EVIDENCE_INTEGRITY_FAILED", "EVIDENCE_QUERY", "执行证据关联或状态不一致。")
        if evidence is not None and (
            json.loads(attempt["actual_test_input_json"]) != evidence["actual_test_adapter_input"]
            or json.loads(attempt["raw_result_json"]) != evidence["raw_test_result"]
            or json.loads(attempt["error_json"]) != evidence["error"]
            or json.loads(run["error_json"]) != evidence["error"]
        ):
            raise C01Error("EVIDENCE_INTEGRITY_FAILED", "EVIDENCE_QUERY", "Attempt 原始结果与 Evidence 不一致。")
        if run["evidence_status"] == "AVAILABLE" and evidence is None:
            raise C01Error("EVIDENCE_MISSING", "EVIDENCE_QUERY", "已标记保存的执行证据缺失。")
        if run["status"] == "SUCCEEDED" and (not evidence or run["evidence_status"] != "AVAILABLE"):
            raise C01Error("EVIDENCE_MISSING", "EVIDENCE_QUERY", "没有证据不能读取为成功。")
        if ((run["evidence_status"] == "MISSING" and evidence is not None)
                or (run["status"] == "RUNNING" and run["evidence_status"] != "PENDING")
                or (run["status"] != "RUNNING" and run["evidence_status"] == "PENDING")):
            raise C01Error("EXECUTION_STATE_INCONSISTENT", "EVIDENCE_QUERY", "执行与证据状态矛盾。")
        run_record, attempt_record = dict(run), dict(attempt)
        for record in [run_record, attempt_record]:
            for key in list(record):
                if key.endswith("_json"):
                    value = record.pop(key)
                    record[key[:-5]] = json.loads(value) if value else None
        return {"label": "TEST_ONLY EXECUTION", "run": run_record, "attempt": attempt_record,
                "evidence": evidence, "actual_model_input": None, "model_api_call_attempted": False}

    def history(self, task_id: UUID) -> dict:
        with self.database.connection() as db:
            snapshots = [dict(r) for r in db.execute(
                "SELECT id,asset_id,created_at,body_sha256 FROM c01_snapshots WHERE task_id=? ORDER BY created_at",
                (str(task_id),))]
            run_ids = [UUID(r[0]) for r in db.execute(
                """SELECT r.id FROM c01_runs r JOIN c01_snapshots s ON s.id=r.snapshot_id
                   WHERE s.task_id=? ORDER BY r.created_at""", (str(task_id),))]
        records = [self.inspect_run(identity) for identity in run_ids]
        return {"task_id": str(task_id), "snapshots": snapshots,
                "runs": [r["run"] for r in records], "label": "TEST_ONLY EXECUTION"}

    def orphan_snapshot_images(self) -> list[str]:
        with self.database.connection() as db:
            known = {UUID(r[0]).hex for r in db.execute("SELECT id FROM c01_snapshots")}
        return sorted(p.name for p in self.images.iterdir() if p.name not in known) if self.images.exists() else []
