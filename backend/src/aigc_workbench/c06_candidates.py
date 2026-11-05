"""Durable, immutable candidate PNGs linked to the existing Task, Slot and model Run."""

import base64
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID, uuid5

from .c01_context import C01Error, canonical, fingerprint, now
from .file_storage import MAX_UPLOAD_BYTES, inspect_image
from .application import TaskNotFound
from .c01_model_store import C01ModelStore
from .image_sizes import snapshot_output_plan


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

    def _read_png(self, version_id: UUID, *, native: bool = False) -> bytes:
        try:
            suffix = ".native.png" if native else ".png"
            descriptor = os.open(self.directory / (version_id.hex + suffix), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                return stream.read(MAX_UPLOAD_BYTES + 1)
        except OSError as exc:
            raise C01Error("CANDIDATE_IMAGE_UNAVAILABLE", "CANDIDATE_QUERY", "候选PNG无法安全读取。") from exc

    def read_for_slot(self, task_id: UUID, slot_id: UUID, version_id: UUID) -> tuple[dict, bytes]:
        # Scope before opening any file. Never use the persisted absolute 'file' as an input path.
        with self.database.connection() as db:
            row = db.execute("SELECT id FROM image_versions WHERE id=? AND task_id=? AND slot_id=?",
                             (str(version_id), str(task_id), str(slot_id))).fetchone()
        if row is None:
            raise TaskNotFound("当前图位没有该候选版本。")
        body = self.inspect(version_id)
        with self.database.connection() as db:
            slot = db.execute("SELECT task_id FROM image_slots WHERE id=?", (str(slot_id),)).fetchone()
            assets = db.execute("SELECT id,role FROM assets WHERE task_id=? AND id IN (?,?)",
                                (str(task_id), body["product_asset_id"], body["reference_asset_id"])).fetchall()
        run = C01ModelStore(self.database).inspect(UUID(body["run_id"]))
        if (slot is None or slot["task_id"] != str(task_id)
                or {r["id"]: r["role"] for r in assets} != {
                    body["product_asset_id"]: "product_image", body["reference_asset_id"]: "marketing_reference"}
                or run.get("capability") not in {"C06", "C07"} or run["status"] != "SUCCEEDED"
                or run.get("task_id") != str(task_id) or run.get("slot_id") != str(slot_id)
                or run.get("product_asset_id") != body["product_asset_id"]
                or run.get("reference_asset_id") != body["reference_asset_id"]
                or run["snapshot_id"] != body["snapshot_id"]
                or run["structured_output"]["content"]["image"] != body["image"]):
            raise C01Error("CANDIDATE_ASSOCIATION_INVALID", "CANDIDATE_QUERY", "候选来源或执行关联不一致。")
        content = self._read_png(version_id)
        if asdict(inspect_image(content, "image/png")) != body["image"]:
            raise C01Error("CANDIDATE_IMAGE_CORRUPT", "CANDIDATE_QUERY", "候选文件身份或图片格式不一致。")
        if "requested_output_size" in body:
            for key in ("requested_output_size", "model_generation_size", "final_output_size", "postprocess", "native_image"):
                if body[key] != run["structured_output"]["content"][key]:
                    raise C01Error("CANDIDATE_ASSOCIATION_INVALID", "CANDIDATE_QUERY", "候选尺寸来源与执行证据不一致。")
            if body["postprocess"] != "NONE" and asdict(inspect_image(
                    self._read_png(version_id, native=True), "image/png")) != body["native_image"]:
                raise C01Error("CANDIDATE_IMAGE_CORRUPT", "CANDIDATE_QUERY", "原生模型PNG完整性失败。")
        return body, content

    def inspect(self, version_id: UUID) -> dict:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM image_versions WHERE id=?", (str(version_id),)).fetchone()
        if row is None:
            raise C01Error("CANDIDATE_NOT_FOUND", "CANDIDATE_QUERY", "候选版本不存在。")
        body = json.loads(row["body_json"])
        if (fingerprint(body) != row["body_sha256"] or body["image_version_id"] != row["id"]
                or any(body[key] != row[key] for key in ("candidate_id", "task_id", "slot_id", "run_id", "status"))):
            raise C01Error("CANDIDATE_CORRUPT", "CANDIDATE_QUERY", "候选来源记录不一致。")
        content = self._read_png(UUID(body["image_version_id"]))
        if len(content) != body["image"]["size_bytes"] or sha256(content).hexdigest() != body["image"]["sha256"]:
            raise C01Error("CANDIDATE_IMAGE_CORRUPT", "CANDIDATE_QUERY", "候选PNG完整性失败。")
        return body

    def save(self, run: dict, snapshot: dict, png: bytes) -> dict:
        if run["status"] != "SUCCEEDED" or run["transport_label"] != "REAL_PROVIDER_TRANSPORT":
            raise C01Error("CANDIDATE_REAL_RUN_REQUIRED", "CANDIDATE_SAVE", "仅真实成功结果可保存业务候选。")
        metadata = inspect_image(png, "image/png")
        plan = snapshot_output_plan(snapshot)
        if (metadata.media_type != "image/png" or f"{metadata.width}x{metadata.height}" != plan["final_output_size"]
                or asdict(metadata) != run["structured_output"]["content"]["image"]):
            raise C01Error("CANDIDATE_DIMENSIONS_INVALID", "CANDIDATE_SAVE", "候选PNG与本次最终输出计划不一致。")
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
        if snapshot["capability"] == "C07":
            body.update(base_image_version_id=snapshot["base_image_version_id"],
                        revision_text=snapshot["context"]["revision_text"])
        native = None
        if "output_plan" in snapshot:
            body.update(plan, native_image=run["structured_output"]["content"]["native_image"])
            if plan["postprocess"] != "NONE":
                native = base64.b64decode(run["actual_model_response"]["data"][0]["b64_json"], validate=True)
                if asdict(inspect_image(native, "image/png")) != body["native_image"]:
                    raise C01Error("CANDIDATE_IMAGE_CORRUPT", "CANDIDATE_SAVE", "模型原生PNG与执行证据不一致。")
                body["native_file"] = str((self.directory / (version_id.hex + ".native.png")).resolve())
        with self.database.connection(write=True) as db:
            existing = db.execute("SELECT body_json FROM image_versions WHERE run_id=?", (run["run_id"],)).fetchone()
            if existing:
                if {k: v for k, v in json.loads(existing[0]).items() if k not in {"file", "native_file"}} != {k: v for k, v in body.items() if k not in {"file", "native_file"}}:
                    raise C01Error("CANDIDATE_CONFLICT", "CANDIDATE_SAVE", "同一Run不能创建不同候选。")
            else:
                if native is not None:
                    self._publish(self.directory / (version_id.hex + ".native.png"), native)
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

    def _publish(self, target: Path, content: bytes) -> None:
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.read_bytes() != content:
                raise C01Error("CANDIDATE_FILE_CONFLICT", "CANDIDATE_SAVE", "已有不同PNG，未覆盖。")
            return
        temporary = target.with_suffix(target.suffix + ".part")
        private_write(temporary, content)
        os.link(temporary, target)
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary.unlink()
