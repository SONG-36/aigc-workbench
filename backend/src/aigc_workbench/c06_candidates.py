"""Durable, immutable candidate PNGs linked to the existing Task, Slot and model Run."""

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID, uuid5

from .c01_context import C01Error, canonical, fingerprint, now
from .file_storage import inspect_image


def private_write(path: Path, content: bytes) -> None:
    """Exclusive durable write; existing files are never replaced."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


class CandidateStore:
    def __init__(self, database, data_dir: Path):
        self.database = database
        self.directory = data_dir / "image_versions"

    def inspect(self, version_id: UUID) -> dict:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM image_versions WHERE id=?", (str(version_id),)).fetchone()
        if row is None:
            raise C01Error("CANDIDATE_NOT_FOUND", "CANDIDATE_QUERY", "候选版本不存在。")
        body = json.loads(row["body_json"])
        if (fingerprint(body) != row["body_sha256"] or body["image_version_id"] != row["id"]
                or any(body[key] != row[key] for key in ("candidate_id", "task_id", "slot_id", "run_id", "status"))):
            raise C01Error("CANDIDATE_CORRUPT", "CANDIDATE_QUERY", "候选来源记录不一致。")
        path = self.directory / (UUID(body["image_version_id"]).hex + ".png")
        if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != body["image"]["sha256"]:
            raise C01Error("CANDIDATE_IMAGE_CORRUPT", "CANDIDATE_QUERY", "候选PNG完整性失败。")
        return body

    def save(self, run: dict, snapshot: dict, png: bytes) -> dict:
        if run["status"] != "SUCCEEDED" or run["transport_label"] != "REAL_PROVIDER_TRANSPORT":
            raise C01Error("CANDIDATE_REAL_RUN_REQUIRED", "CANDIDATE_SAVE", "仅真实成功结果可保存业务候选。")
        metadata = inspect_image(png, "image/png")
        if (metadata.media_type, metadata.width, metadata.height) != ("image/png", 1024, 1536):
            raise C01Error("CANDIDATE_DIMENSIONS_INVALID", "CANDIDATE_SAVE", "候选必须是1024x1536 PNG。")
        version_id = uuid5(UUID(run["run_id"]), "image-version-1")
        candidate_id = uuid5(UUID(run["run_id"]), "candidate-1")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self.directory / (version_id.hex + ".png")
        body = {"image_version_id": str(version_id), "candidate_id": str(candidate_id),
                "task_id": snapshot["task_id"], "slot_id": snapshot["slot_id"], "run_id": run["run_id"],
                "attempt_id": run["attempt_id"], "operation_id": run["operation_id"],
                "snapshot_id": snapshot["id"], "decision_snapshot_id": snapshot["decision_snapshot_id"],
                "product_asset_id": snapshot["product_asset_id"], "reference_asset_id": snapshot["reference_asset_id"],
                "source_provenance": snapshot["source_provenance"], "image": asdict(metadata),
                "file": str(target.resolve()), "status": "CANDIDATE_REVIEW_PENDING",
                "operator_approved": False, "pass": False, "created_at": run["finished_at"] or now()}
        with self.database.connection(write=True) as db:
            existing = db.execute("SELECT body_json FROM image_versions WHERE run_id=?", (run["run_id"],)).fetchone()
            if existing:
                if json.loads(existing[0]) != body:
                    raise C01Error("CANDIDATE_CONFLICT", "CANDIDATE_SAVE", "同一Run不能创建不同候选。")
            else:
                if (target.exists() or target.is_symlink()):
                    # An interrupted local save may leave this exact UUID orphan. Never overwrite it.
                    if target.is_symlink() or target.read_bytes() != png:
                        raise C01Error("CANDIDATE_FILE_CONFLICT", "CANDIDATE_SAVE", "已有不同候选文件，未覆盖。")
                else:
                    temporary = self.directory / (version_id.hex + ".part")
                    private_write(temporary, png)
                    os.link(temporary, target)  # Atomic no-overwrite publication.
                    descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    temporary.unlink()
                db.execute("INSERT INTO image_versions VALUES (?,?,?,?,?,?,?,?)", (
                    str(version_id), str(candidate_id), body["task_id"], body["slot_id"], run["run_id"],
                    body["status"], canonical(body), fingerprint(body)))
        return self.inspect(version_id)
