"""Workspace use cases: scoped candidate reads, valid C06 sources and explicit paid dispatch."""

import json
import os
from uuid import UUID

from .application import TaskNotFound
from .c01_context import C01Error, fingerprint
from .c01_execution import C01Service
from .c01_model import authorize
from .c01_model_store import C01ModelStore
from .c01_persistence import C01EvidenceStore
from .c06_candidates import CandidateStore
from .c06_dispatch import C06Dispatcher, ACTIVE
from .c06_generation import C06Service, MODEL, PARAMETERS
from .config import CredentialConfigError, load_openai_credentials
from .domain import Conflict


class GenerationApplication:
    def __init__(self, settings, tasks, database, *, transport=None):
        self.settings, self.tasks, self.database, self.transport = settings, tasks, database, transport
        self.candidates = CandidateStore(database, settings.data_dir)
        self.runs = C01ModelStore(database)
        self.dispatcher = C06Dispatcher(database, settings.data_dir, self._execute,
                                         "TEST_SUBSTITUTE" if transport is not None else "REAL")
        self.access_error = None

    def start(self):
        self.dispatcher.start()
        if self.enabled:
            try:
                if load_openai_credentials() != "KEY_CONFIGURED":
                    self.access_error = "后端未配置模型凭证。"
            except CredentialConfigError:
                self.access_error = "后端私有凭证配置不可用。"

    @property
    def enabled(self):
        s = self.settings
        return (s.environment == "development" and s.enable_workspace_generation and s.enable_c01_testing
                and s.enable_c01_real and os.environ.get("AIGC_ENABLE_C06_REAL") == "1")

    def _service(self):
        return C06Service(self.settings, C01Service(self.settings, self.tasks,
                          C01EvidenceStore(self.database, self.settings.data_dir)))

    def _execute(self, snapshot_id, operation_id, budget):
        return self._service().run(snapshot_id, MODEL, operation_id, confirmed=True,
                                   max_cost_usd=budget, transport=self.transport)

    def _slot(self, task_id, slot_id):
        task = self.tasks.get_draft(task_id)
        slot = next((slot for slot in task.slots if slot.id == slot_id), None)
        if slot is None:
            raise TaskNotFound("当前任务没有该图位。")
        return task, slot

    def _select(self, task_id, slot_id):
        # Discover by persisted business ownership, never by sample UUID or a filesystem glob.
        with self.database.connection() as db:
            rows = db.execute("SELECT id FROM c01_snapshots WHERE task_id=? "
                              "AND json_extract(body_json,'$.slot_id')=? "
                              "AND json_extract(body_json,'$.capability')='C06' ORDER BY created_at DESC,id DESC",
                              (str(task_id), str(slot_id))).fetchall()
        if not rows:
            raise C01Error("CREATION_DECISION_MISSING", "SOURCE_SELECTION", "本图位尚无已归档且可执行的制作决定。")
        # A newer invalid decision must not silently fall back to an older one.
        return self._service().preflight(UUID(rows[0]["id"]), MODEL)

    @staticmethod
    def _quote(preflight):
        return fingerprint({"snapshot_id": preflight["snapshot"]["id"],
                            "request_sha256": preflight["request_sha256"],
                            "model": MODEL, "max_cost_usd": "1.00", "cost": preflight["cost"]})

    def _candidate(self, task_id, slot_id, version_id):
        body, _ = self.candidates.read_for_slot(task_id, slot_id, version_id)
        keys = ("image_version_id", "candidate_id", "task_id", "slot_id", "run_id", "created_at",
                "product_asset_id", "reference_asset_id", "status", "image", "operator_approved", "pass")
        return {**{key: body[key] for key in keys},
                "url": f"/api/tasks/{task_id}/slots/{slot_id}/versions/{version_id}/content"}

    def content(self, task_id, slot_id, version_id):
        self._slot(task_id, slot_id)
        return self.candidates.read_for_slot(task_id, slot_id, version_id)

    def _history(self, task_id, slot_id, candidates):
        with self.database.connection() as db:
            ids = db.execute("SELECT r.id FROM c01_model_runs r JOIN c01_snapshots s ON r.snapshot_id=s.id "
                             "WHERE s.task_id=? AND json_extract(s.body_json,'$.slot_id')=? "
                             "AND json_extract(s.body_json,'$.capability')='C06'", (str(task_id), str(slot_id))).fetchall()
            receipts = [dict(row) for row in db.execute("SELECT * FROM c06_dispatches WHERE task_id=? AND slot_id=?",
                                                       (str(task_id), str(slot_id)))]
        runs = {run["operation_id"]: run for run in (self.runs.inspect(UUID(row["id"])) for row in ids)}
        receipts_by_op = {row["operation_id"]: row for row in receipts}
        items = []
        for operation in runs.keys() | receipts_by_op.keys():
            run, receipt = runs.get(operation), receipts_by_op.get(operation)
            state = receipt["state"] if receipt else run["status"]
            if state == "QUEUED": state = "RUNNING"
            if not receipt and state == "RUNNING": state = "OUTCOME_UNKNOWN"
            item_error = json.loads(receipt["error_json"]) if receipt and receipt["error_json"] else None
            item_error = item_error or (run.get("error") if run else None)
            candidate_ids = [c["image_version_id"] for c in candidates if run and c["run_id"] == run["run_id"]]
            if receipt and receipt["state"] in {"QUEUED", "RUNNING"} and run:
                if run["status"] in {"FAILED", "OUTCOME_UNKNOWN"}:
                    state = run["status"]
                elif run["status"] == "SUCCEEDED" and candidate_ids:
                    state = "SUCCEEDED"
            if item_error and item_error.get("code") == "CANDIDATE_NOT_SAVED" and candidate_ids:
                state, item_error = "SUCCEEDED", None  # A separately completed local save recovered the result.
            if state == "SUCCEEDED" and not candidate_ids:
                state, item_error = "FAILED", {"code": "CANDIDATE_NOT_SAVED", "message": "模型已返回，候选未可靠保存；不要重新付费。"}
            error = {key: item_error[key] for key in ("code", "message") if key in item_error} if item_error else None
            items.append({"operation_id": operation, "run_id": run["run_id"] if run else None,
                          "attempt_id": run["attempt_id"] if run else None, "state": state,
                          "created_at": receipt["created_at"] if receipt else run["started_at"],
                          "finished_at": run["finished_at"] if run else None,
                          "model": run["requested_model"] if run else MODEL,
                          "execution_mode": receipt["execution_mode"] if receipt else
                              ("REAL" if run["transport_label"] == "REAL_PROVIDER_TRANSPORT" else "TEST_SUBSTITUTE"),
                          "candidate_ids": candidate_ids,
                          "http_status": run.get("response_http_status") if run else None,
                          "duration_ms": run["duration_ms"] if run else None,
                          "usage": run["provider_usage"] if run else None,
                          "usage_cost_estimate": run["usage_based_cost_estimate"] if run else None,
                          "error": error})
        return sorted(items, key=lambda row: row["created_at"], reverse=True)

    def workspace(self, task_id, slot_id):
        task, slot = self._slot(task_id, slot_id)
        with self.database.connection() as db:
            versions = db.execute("SELECT id FROM image_versions WHERE task_id=? AND slot_id=? ORDER BY rowid DESC",
                                  (str(task_id), str(slot_id))).fetchall()
        candidates = [self._candidate(task_id, slot_id, UUID(row["id"])) for row in versions]
        executions = self._history(task_id, slot_id, candidates)
        readiness = {"can_generate": False, "reason": "本地服务尚未启用付费生成。", "quote_token": None,
                     "model": MODEL, "parameters": PARAMETERS, "max_cost_usd": "1.00", "estimate_usd": None,
                     "decision_snapshot_id": None, "product_asset_id": None,
                     "reference_asset_id": str(slot.reference_asset_id) if slot.reference_asset_id else None}
        if self.enabled and self.dispatcher.pool and not self.access_error:
            try:
                preflight = self._select(task_id, slot_id)
                authorize(self.settings, True, "1.00", preflight["cost"])  # Offline guard only; no request.
                snapshot = preflight["snapshot"]
                readiness.update(can_generate=True, reason="制作决定及来源已核验，可生成一张待审候选。",
                    quote_token=self._quote(preflight), estimate_usd=preflight["cost"]["planning_envelope_usd"],
                    decision_snapshot_id=snapshot["decision_snapshot_id"], product_asset_id=snapshot["product_asset_id"],
                    reference_asset_id=snapshot["reference_asset_id"])
            except C01Error as exc:
                readiness["reason"] = str(exc)
            except (OSError, ValueError, KeyError, TypeError):
                readiness["reason"] = "制作决定或来源记录不可读取，未开放生成。"
        elif self.access_error:
            readiness["reason"] = self.access_error
        active = next((item for item in executions if item["state"] in ACTIVE), None)
        if active:
            readiness.update(can_generate=False, reason="本图位有执行中或结果未知的操作；请查询，不要重复付费。")
        if any(item["error"] and item["error"].get("code") == "CANDIDATE_NOT_SAVED" for item in executions):
            readiness.update(can_generate=False, reason="已有模型结果待恢复本地候选保存；不要再次付费。")
        return {"task_id": str(task_id), "slot_id": str(slot_id), "readiness": readiness,
                "execution_mode": "TEST_SUBSTITUTE" if self.transport is not None else "REAL",
                "candidates": candidates, "executions": executions,
                "state": active["state"] if active else executions[0]["state"] if executions else "NOT_STARTED"}

    def submit(self, task_id, slot_id, operation_id, quote_token, max_cost_usd):
        self._slot(task_id, slot_id)
        old = self.dispatcher.replay(operation_id, task_id, slot_id, quote_token, max_cost_usd)
        if old:
            return {"operation_id": old["operation_id"], "state": old["state"]}
        with self.database.connection() as db:
            if db.execute("SELECT id FROM c01_model_runs WHERE operation_id=?", (str(operation_id),)).fetchone():
                raise Conflict("操作身份已用于历史模型执行，不能重新提交为新生图。")
        if not self.enabled or self.access_error:
            raise C01Error("WORKSPACE_GENERATION_DISABLED", "ACCESS", self.access_error or "本地服务未启用付费生成。")
        workspace = self.workspace(task_id, slot_id)
        ready = workspace["readiness"]
        if not ready["can_generate"]:
            raise Conflict(ready["reason"])
        preflight = self._select(task_id, slot_id)
        if quote_token != self._quote(preflight) or max_cost_usd != "1.00":
            raise Conflict("制作依据或费用估算已变化，请重新查看；未执行。")
        authorize(self.settings, True, max_cost_usd, preflight["cost"])
        receipt = self.dispatcher.submit(operation_id, task_id, slot_id, preflight["snapshot"]["id"],
                                          quote_token, max_cost_usd)
        return {"operation_id": receipt["operation_id"], "state": receipt["state"]}
