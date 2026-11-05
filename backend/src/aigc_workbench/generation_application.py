"""Workspace use cases: scoped candidate reads, valid C06 sources and explicit paid dispatch."""

import json
import os
from pathlib import Path
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
from .c07_revision import C07Service
from .image_review import ReviewStore


class GenerationApplication:
    def __init__(self, settings, tasks, database, *, transport=None):
        self.settings, self.tasks, self.database, self.transport = settings, tasks, database, transport
        self.candidates = CandidateStore(database, settings.data_dir)
        self.runs = C01ModelStore(database)
        self.reviews = ReviewStore(database, self.candidates, test_mode=transport is not None)
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
        return (s.environment in {"development", "pilot"} and s.enable_workspace_generation
                and (s.environment == "pilot" or s.enable_c01_testing)
                and s.enable_c01_real and os.environ.get("AIGC_ENABLE_C06_REAL") == "1")

    def _service(self):
        return C06Service(self.settings, C01Service(self.settings, self.tasks,
                          C01EvidenceStore(self.database, self.settings.data_dir)))

    def _execute(self, snapshot_id, operation_id, budget):
        snapshot = C01EvidenceStore(self.database, self.settings.data_dir).load_snapshot(snapshot_id)
        service = self._revision_service() if snapshot["capability"] == "C07" else self._service()
        return service.run(snapshot_id, MODEL, operation_id, confirmed=True,
                                   max_cost_usd=budget, transport=self.transport)

    def _revision_service(self):
        return C07Service(self.settings, C01Service(self.settings, self.tasks,
                         C01EvidenceStore(self.database, self.settings.data_dir)))

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
        service = self._service()
        snapshot = service.service.evidence.load_snapshot(UUID(rows[0]["id"]))
        _, slot = self._slot(task_id, slot_id)
        if slot.output_size != snapshot["context"]["slot_state"]["output_size"]:
            # A changed pixel size needs a new execution snapshot, never a rewrite of the
            # decision or previous Run. prepare revalidates every non-size source/requirement.
            # Subsequent reads reuse this snapshot; this is offline preparation, never dispatch.
            snapshot = service.prepare(UUID(snapshot["decision_snapshot_id"]),
                                       Path(snapshot["handoff_directory"]), MODEL)
        return service.preflight(UUID(snapshot["id"]), MODEL)

    @property
    def budget_policy(self):
        return "UNRESTRICTED_PILOT" if self.settings.environment == "pilot" else "1.00"

    def _quote(self, preflight):
        return fingerprint({"snapshot_id": preflight["snapshot"]["id"],
                            "request_sha256": preflight["request_sha256"],
                            "model": MODEL, "execution_policy": self.budget_policy})

    def _candidate(self, task_id, slot_id, version_id):
        body, _ = self.candidates.read_for_slot(task_id, slot_id, version_id)
        keys = ("image_version_id", "candidate_id", "task_id", "slot_id", "run_id", "created_at",
                "product_asset_id", "reference_asset_id", "status", "image", "operator_approved", "pass")
        return {**{key: body[key] for key in keys}, "base_image_version_id": body.get("base_image_version_id"),
                "revision_text": body.get("revision_text"),
                **{key: body[key] for key in ("requested_output_size", "model_generation_size", "final_output_size", "postprocess")
                   if key in body},
                "url": f"/api/tasks/{task_id}/slots/{slot_id}/versions/{version_id}/content"}

    def content(self, task_id, slot_id, version_id):
        self._slot(task_id, slot_id)
        return self.candidates.read_for_slot(task_id, slot_id, version_id)

    def _history(self, task_id, slot_id, candidates):
        with self.database.connection() as db:
            ids = db.execute("SELECT r.id FROM c01_model_runs r JOIN c01_snapshots s ON r.snapshot_id=s.id "
                             "WHERE s.task_id=? AND json_extract(s.body_json,'$.slot_id')=? "
                             "AND json_extract(s.body_json,'$.capability') IN ('C06','C07')", (str(task_id), str(slot_id))).fetchall()
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
                # PNG postprocessing/publication can finish between the candidate query and
                # the Run query. An older projection must not report a false save failure.
                with self.database.connection() as db:
                    published = db.execute("SELECT id FROM image_versions WHERE run_id=?", (run["run_id"],)).fetchone() if run else None
                if published or run is None:
                    state, item_error = "RUNNING", None  # Next poll reads the published candidate.
                else:
                    state, item_error = "FAILED", {"code": "CANDIDATE_NOT_SAVED", "message": "模型已返回，候选未可靠保存；不要重新付费。"}
            error = {key: item_error[key] for key in ("code", "message") if key in item_error} if item_error else None
            items.append({"operation_id": operation, "run_id": run["run_id"] if run else None,
                          "attempt_id": run["attempt_id"] if run else None, "state": state,
                          "created_at": receipt["created_at"] if receipt else run["started_at"],
                          "finished_at": run["finished_at"] if run else None,
                          "model": run["requested_model"] if run else MODEL,
                          "capability": run.get("capability", "C06") if run else "PENDING_DISPATCH",
                          "base_image_version_id": run.get("base_image_version_id") if run else None,
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
        reviews = [{**r, "reviewer_identity": "UNVERIFIED_OPERATOR_LABEL"} for r in self.reviews.list(task_id, slot_id)]
        delivery = self.reviews.current(task_id, slot_id)
        for candidate in candidates:
            related = [r for r in reviews if r["image_version_id"] == candidate["image_version_id"]]
            candidate["review_status"] = related[-1]["decision"] if related else "PENDING"
            candidate["has_historical_pass"] = any(r["decision"] == "PASS" for r in related)
            candidate["pass"] = candidate["review_status"] == "PASS"
            candidate["operator_approved"] = candidate["pass"]
            candidate["is_current_delivery"] = bool(delivery and delivery["image_version_id"] == candidate["image_version_id"])
        executions = self._history(task_id, slot_id, candidates)
        readiness = {"can_generate": False, "reason": "本地服务尚未启用付费生成。", "quote_token": None,
                     "model": MODEL, "parameters": PARAMETERS, "max_cost_usd": None if self.settings.environment == "pilot" else "1.00", "estimate_usd": None,
                     "decision_snapshot_id": None, "product_asset_id": None,
                     "reference_asset_id": str(slot.reference_asset_id) if slot.reference_asset_id else None}
        if self.enabled and self.dispatcher.pool and not self.access_error:
            try:
                preflight = self._select(task_id, slot_id)
                authorize(self.settings, True, self.budget_policy, preflight["cost"])  # Offline guard only; no request.
                snapshot = preflight["snapshot"]
                readiness.update(can_generate=True, reason="制作决定及来源已核验，可生成一张待审候选。",
                    parameters=preflight["snapshot"]["parameters"],
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
                "reviews": reviews, "delivery": ({**delivery,
                    "url": f"/api/tasks/{task_id}/slots/{slot_id}/delivery?review_id={delivery['id']}"} if delivery else None),
                "can_request_revision": bool(self.enabled and self.dispatcher.pool and not self.access_error and candidates
                    and not active and os.environ.get("AIGC_ENABLE_C07_REAL") == "1"
                    and not any(e["error"] and e["error"].get("code") == "CANDIDATE_NOT_SAVED" for e in executions)),
                "candidates": candidates, "executions": executions,
                "state": active["state"] if active else executions[0]["state"] if executions else "NOT_STARTED"}

    def submit(self, task_id, slot_id, operation_id, quote_token, max_cost_usd=None):
        max_cost_usd = self.budget_policy if self.settings.environment == "pilot" or max_cost_usd is None else max_cost_usd
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
        if quote_token != self._quote(preflight) or max_cost_usd != self.budget_policy:
            raise Conflict("制作依据已变化，请刷新并重新核对；未执行。")
        authorize(self.settings, True, max_cost_usd, preflight["cost"])
        receipt = self.dispatcher.submit(operation_id, task_id, slot_id, preflight["snapshot"]["id"],
                                          quote_token, max_cost_usd)
        return {"operation_id": receipt["operation_id"], "state": receipt["state"]}

    def revision_quote(self, task_id, slot_id, base_id, revision_text):
        workspace = self.workspace(task_id, slot_id)
        if not workspace["can_request_revision"]:
            raise Conflict("本图位修改未启用、已有未完成操作或缺少合法基础版本；请核对状态。")
        service = self._revision_service()
        snapshot = service.prepare(task_id, slot_id, base_id, revision_text)
        preflight = service.preflight(UUID(snapshot["id"]), MODEL)
        authorize(self.settings, True, self.budget_policy, preflight["cost"])
        return {"snapshot_id": snapshot["id"], "base_image_version_id": str(base_id),
                "base_sha256": snapshot["context"]["image_inputs"][0]["sha256"],
                "revision_text": snapshot["context"]["revision_text"], "quote_token": self._quote(preflight),
                "model": MODEL, "estimate_usd": preflight["cost"]["planning_envelope_usd"], "max_cost_usd": None if self.settings.environment == "pilot" else "1.00"}

    def submit_revision(self, task_id, slot_id, operation_id, snapshot_id, quote_token, max_cost_usd=None):
        max_cost_usd = self.budget_policy if self.settings.environment == "pilot" or max_cost_usd is None else max_cost_usd
        self._slot(task_id, slot_id)
        old = self.dispatcher.replay(operation_id, task_id, slot_id, quote_token, max_cost_usd)
        if old:
            if old["snapshot_id"] != str(snapshot_id):
                raise Conflict("同一修改操作不能更换基础版本或修改快照。")
            return {"operation_id": old["operation_id"], "state": old["state"]}
        if not self.workspace(task_id, slot_id)["can_request_revision"]:
            raise Conflict("当前修改条件不满足；请查询现有执行，不重复付费。")
        snapshot = self._revision_service().service.evidence.load_snapshot(snapshot_id)
        if snapshot["task_id"] != str(task_id) or snapshot["slot_id"] != str(slot_id) or snapshot["capability"] != "C07":
            raise Conflict("修改快照不属于当前图位或操作。")
        preflight = self._revision_service().preflight(snapshot_id, MODEL)
        if self._quote(preflight) != quote_token or max_cost_usd != self.budget_policy:
            raise Conflict("修改内容或来源已变化；请重新核对。")
        authorize(self.settings, True, max_cost_usd, preflight["cost"])
        receipt = self.dispatcher.submit(operation_id, task_id, slot_id, snapshot_id, quote_token, max_cost_usd)
        return {"operation_id": receipt["operation_id"], "state": receipt["state"]}

    def review(self, task_id, slot_id, version_id, operation_id, decision, reviewer, feedback, expected_current_review_id):
        self._slot(task_id, slot_id)
        return self.reviews.submit(task_id, slot_id, version_id, operation_id, decision, reviewer, feedback, expected_current_review_id)

    def delivery(self, task_id, slot_id, review_id):
        self._slot(task_id, slot_id)
        return self.reviews.delivery(task_id, slot_id, review_id)
