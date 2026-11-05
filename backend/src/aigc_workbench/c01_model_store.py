"""Small durable one-shot model Run/Attempt/Evidence record. No business writes."""

import json
from uuid import UUID, uuid4

from .c01_context import C01Error, canonical, fingerprint, now
from .persistence import SQLiteTaskStore


class C01ModelStore:
    def __init__(self, database: SQLiteTaskStore):
        self.database = database

    @staticmethod
    def _decode(row) -> dict:
        body = json.loads(row["body_json"])
        if (fingerprint(body) != row["body_sha256"] or body["run_id"] != row["id"]
                or body["attempt_id"] != row["attempt_id"] or body["snapshot_id"] != row["snapshot_id"]
                or body["status"] != row["status"] or body["request_sha256"] != row["request_sha256"]
                or body["operation_id"] != row["operation_id"]
                or body["attempt_status"] != body["status"]
                or fingerprint(body["planned_model_input"]) != body["request_sha256"]):
            raise C01Error("MODEL_EVIDENCE_CORRUPT", "EVIDENCE_QUERY", "真实模型证据关联或哈希错误。")
        return body

    def inspect(self, run_id: UUID) -> dict:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM c01_model_runs WHERE id=?", (str(run_id),)).fetchone()
        if row is None:
            raise C01Error("MODEL_RUN_NOT_FOUND", "EVIDENCE_QUERY", "模型 Run 不存在。")
        return self._decode(row)

    def start(self, snapshot: dict, operation_id: UUID, request: dict, quote: dict,
              budget: str, transport_label: str) -> tuple[dict, bool]:
        request_hash = fingerprint(request)
        with self.database.connection(write=True) as db:
            old = db.execute("SELECT * FROM c01_model_runs WHERE operation_id=?", (str(operation_id),)).fetchone()
            if old:
                record = self._decode(old)
                if (record["snapshot_id"] != snapshot["id"] or record["request_sha256"] != request_hash
                        or record["transport_label"] != transport_label):
                    raise C01Error("MODEL_OPERATION_CONFLICT", "RUN_START", "原操作不能更换快照、模型、参数或传输方式。")
                return record, False
            record = {
                "label": snapshot["capability"] + " MODEL EXECUTION", "transport_label": transport_label,
                "run_id": str(uuid4()), "attempt_id": str(uuid4()), "operation_id": str(operation_id),
                "snapshot_id": snapshot["id"], "snapshot_sha256": fingerprint(snapshot),
                "status": "RUNNING", "attempt_status": "RUNNING", "started_at": now(), "finished_at": None,
                "request_sha256": request_hash, "planned_model_input": request,
                "image_source": snapshot["prompt"]["planned_model_input"]["images"][0],
                "provider": "OpenAI", "requested_model": request["model"],
                "endpoint": ("https://api.openai.com/v1/images/edits" if snapshot["capability"] in {"C06", "C07"}
                             else "https://api.openai.com/v1/responses"),
                "cost_preflight": quote, "user_max_cost_usd": budget, "max_calls": 1,
                "automatic_retries": 0, "actual_model_input": None, "actual_model_response": None,
                "model_api_call_attempted": False, "dispatch_state": "NOT_DISPATCHED",
                "provider_receipt": "UNKNOWN", "raw_response_text": None, "response_http_status": None,
                "request_id": None, "structured_output": None, "error": None, "duration_ms": None,
                "provider_usage": None, "provider_cost": None, "usage_based_cost_estimate": None,
                "business_state_changed": False, "human_evaluation": "NOT_PERFORMED",
                "note": "RUNNING 只表示未可靠完成，可能进程已结束；禁止自动或盲目重新付费。",
            }
            if snapshot["capability"] in {"C02", "C03_C04_C05", "C06", "C07"}:
                record.update(capability=snapshot["capability"], task_id=snapshot["task_id"], slot_id=snapshot["slot_id"],
                              reference_asset_id=snapshot["reference_asset_id"])
            if snapshot["capability"] in {"C03_C04_C05", "C06", "C07"}:
                record.update(product_asset_id=snapshot["product_asset_id"],
                              image_sources=snapshot["prompt"]["planned_model_input"]["images"],
                              upstream_run_ids=snapshot["upstream_run_ids"])
            if snapshot["capability"] == "C07":
                record.update(base_image_version_id=snapshot["base_image_version_id"],
                              revision_text=snapshot["context"]["revision_text"])
            if snapshot["capability"] in {"C06", "C07"} and "output_plan" in snapshot:
                # Intent only. Actual native/final image metadata and postprocessing are
                # recorded in structured_output only after decoding/processing succeeds.
                record["output_plan"] = snapshot["output_plan"]
            if budget == "UNRESTRICTED_PILOT":
                record.update(user_max_cost_usd=None, execution_policy="UNRESTRICTED_INTERNAL_PILOT",
                              trigger="EXPLICIT_BUSINESS_COMMAND / UNAUTHENTICATED_OPERATOR")
            db.execute("INSERT INTO c01_model_runs VALUES (?,?,?,?,?,?,?,?)",
                       (record["run_id"], record["attempt_id"], str(operation_id), snapshot["id"],
                        request_hash, "RUNNING", canonical(record), fingerprint(record)))
        return record, True

    def save(self, record: dict) -> None:
        with self.database.connection(write=True) as db:
            row = db.execute("SELECT * FROM c01_model_runs WHERE id=?", (record["run_id"],)).fetchone()
            old = self._decode(row) if row else None
            if old is None or old["status"] != "RUNNING":
                raise C01Error("MODEL_RUN_NOT_RUNNING", "EVIDENCE_SAVE", "执行不能再次推进。")
            for key in ("run_id", "attempt_id", "operation_id", "snapshot_id", "snapshot_sha256",
                        "planned_model_input", "request_sha256", "transport_label", "user_max_cost_usd"):
                if old[key] != record[key]:
                    raise C01Error("MODEL_RUN_INPUT_CHANGED", "EVIDENCE_SAVE", "不能改变固定执行条件。")
            if record["status"] != record["attempt_status"]:
                raise C01Error("MODEL_RUN_STATUS_INVALID", "EVIDENCE_SAVE", "Run/Attempt 状态不同。")
            db.execute("UPDATE c01_model_runs SET status=?,body_json=?,body_sha256=? WHERE id=?",
                       (record["status"], canonical(record), fingerprint(record), record["run_id"]))
