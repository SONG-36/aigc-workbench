"""Fixed S03 experiment preflight and guarded synchronous real execution."""

from copy import deepcopy
from hashlib import sha256
import json
import time
from uuid import UUID

from .c01_context import C01Error, canonical, fingerprint, now, select_source
from .c01_execution import C01Service
from .c01_model import (ENDPOINT, OpenAIResponsesTransport, authorize, build_request,
                        credential_status, estimate_cost, parse_response, usage_cost)
from .c01_model_store import C01ModelStore

SNAPSHOT_ID = "8a72fce2-f08d-4a69-8f94-fb3ccc11a664"
ASSET_ID = "a452aaca-5bfe-4cb9-8bdb-9bd9bd3e8aa3"
IMAGE_SHA256 = "cf304828c8204114c3a71aa69990d22848378b5a50b9330ad66900c2e0d3e554"
SNAPSHOT_SHA256 = "d81408a77016af359ec87a22978cb0bcf4eda3e10240117e5935a0119def80c5"


class C01RealService:
    def __init__(self, settings, service: C01Service):
        self.settings, self.service = settings, service
        self.store = C01ModelStore(service.evidence.database)

    def preflight(self, snapshot_id: UUID, model: str, max_output_tokens: int = 4096) -> dict:
        snapshot = self.service.inspect_snapshot(snapshot_id)
        if (str(snapshot_id) != SNAPSHOT_ID or snapshot["product_asset_id"] != ASSET_ID
                or fingerprint(snapshot) != SNAPSHOT_SHA256
                or snapshot["context"]["operator_confirmed_information"] != []
                or snapshot["effective_state"]["operator_confirmed_information"] != []):
            raise C01Error("EXPERIMENT_SNAPSHOT_MISMATCH", "MODEL_PREFLIGHT", "不是指定的原 S02 实验快照。")
        selection, original = select_source(self.service.tasks, UUID(snapshot["task_id"]), UUID(ASSET_ID))
        frozen = self.service.evidence.read_snapshot_image(snapshot)
        if (original != frozen or sha256(original).hexdigest() != IMAGE_SHA256
                or selection["product_asset"] != snapshot["source_selection"]["product_asset"]):
            raise C01Error("EXPERIMENT_SOURCE_MISMATCH", "MODEL_PREFLIGHT", "真实原图、当前来源和快照不一致，停止使用。")
        request = build_request(snapshot, frozen, model, max_output_tokens)
        return {"label": "PLANNED MODEL INPUT — OFFLINE PREFLIGHT ONLY", "snapshot": snapshot,
                "request": request, "request_sha256": fingerprint(request), "endpoint": ENDPOINT,
                "cost": estimate_cost(snapshot, request), "credentials": credential_status(),
                "checks": {"source_and_frozen_image": "VERIFIED", "four_layer_text": "UNCHANGED",
                           "jpeg_in_multimodal_request": True, "operator_confirmed_information": []},
                "actual_model_input": None, "actual_model_response": None,
                "model_api_call_attempted": False, "network_requests": 0}

    def parse_output(self, raw: dict, model: str) -> dict:
        return parse_response(raw, model)

    def parse_execution_output(self, raw: dict, model: str, snapshot: dict) -> dict:
        return self.parse_output(raw, model)

    def cost_from_usage(self, raw: dict) -> dict | None:
        return usage_cost(raw)

    def run(self, snapshot_id: UUID, model: str, operation_id: UUID, *, confirmed: bool,
            max_cost_usd: str, max_output_tokens: int = 4096, transport=None) -> dict:
        # No DB migration or Run creation before opt-in, quote, source and credentials checks.
        preflight = self.preflight(snapshot_id, model, max_output_tokens)
        authorize(self.settings, confirmed, max_cost_usd, preflight["cost"])
        transport = transport or OpenAIResponsesTransport()
        if transport.label not in {"REAL_PROVIDER_TRANSPORT", "OFFLINE_TRANSPORT_TEST"}:
            raise C01Error("TRANSPORT_INVALID", "ACCESS", "未识别的实验传输方式。")
        self.store.database.initialize()
        record, created = self.store.start(preflight["snapshot"], operation_id, preflight["request"],
                                          preflight["cost"], max_cost_usd, transport.label)
        if not created:
            return record  # Including OUTCOME_UNKNOWN/RUNNING: never replay the paid request.
        # Persist exact request bytes + dispatch intent BEFORE any network operation.
        # A crash here cannot prove whether the provider received it; never claim it did.
        record["dispatch_state"] = "DISPATCH_INTENT_RECORDED"
        try:
            self.store.save(record)
        except Exception as exc:
            raise C01Error("MODEL_DISPATCH_NOT_CONFIRMED", "EVIDENCE_SAVE",
                           f"Run {record['run_id']} 未确认派发记录，未调用；先查询，不自动重试。") from exc
        payload = canonical(preflight["request"]).encode()
        started = time.monotonic()
        response = None
        try:
            record["model_api_call_attempted"] = transport.label == "REAL_PROVIDER_TRANSPORT"
            record["actual_model_input"] = deepcopy(preflight["request"]) if record["model_api_call_attempted"] else None
            if not record['model_api_call_attempted']:
                record['actual_test_adapter_input'] = {'label': 'ACTUAL TEST ADAPTER INPUT / TEST_ONLY',
                                                       'request': deepcopy(preflight['request'])}
            record["dispatch_state"] = "CLIENT_TRANSPORT_INVOKED"
            response = transport.send(payload)  # Exactly one invocation; no loop/retry/fallback.
            record["response_http_status"], record["request_id"] = response.status, response.request_id
            record["provider_receipt"] = ("HTTP_RESPONSE_RECEIVED" if record["model_api_call_attempted"]
                                          else "OFFLINE_TEST_ONLY")
            record["raw_response_text"] = response.body.decode("utf-8", errors="replace")
            try:
                raw = json.loads(record["raw_response_text"])
            except ValueError as exc:
                raise C01Error("MODEL_RESPONSE_INVALID_JSON", "OUTPUT_PARSE", "原始 HTTP 响应无法解析为 JSON。") from exc
            if not isinstance(raw, dict):
                raise C01Error("MODEL_RESPONSE_INVALID_JSON", "OUTPUT_PARSE", "响应不是 JSON 对象。")
            if record["model_api_call_attempted"]:
                record["actual_model_response"] = raw
            else:
                record["offline_test_response"] = raw
            record["provider_usage"] = raw.get("usage") if record["model_api_call_attempted"] else None
            record["usage_based_cost_estimate"] = self.cost_from_usage(raw) if record["model_api_call_attempted"] else None
            if not 200 <= response.status < 300:
                raise C01Error("MODEL_HTTP_ERROR", "MODEL_TRANSPORT", f"供应商 HTTP {response.status}；不重试。")
            result = self.parse_execution_output(raw, model, preflight["snapshot"])
            record["structured_output"] = {"label": ("AI_DERIVED / NOT HUMAN EVALUATED" if record["model_api_call_attempted"]
                                                       else "OFFLINE_TEST_ONLY"), "content": result}
            record["status"] = "SUCCEEDED"
        except Exception as exc:
            # No response: remote execution/billing outcome is unknown, even for a local timeout.
            uncertain = response is None or response.status == 408 or response.status >= 500
            record["status"] = "OUTCOME_UNKNOWN" if uncertain else "FAILED"
            record["error"] = (exc.record() if isinstance(exc, C01Error) else {
                "code": "MODEL_OUTCOME_UNKNOWN" if response is None else "MODEL_RESPONSE_PROCESSING_FAILED",
                "stage": "MODEL_TRANSPORT" if response is None else "OUTPUT_PARSE",
                "message": "未取得可靠响应；不能确认远程执行或费用，不自动重试。" if response is None else "响应处理失败，保留原始证据。",
                "cause_type": type(exc).__name__})
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        record["attempt_status"], record["finished_at"] = record["status"], now()
        try:
            self.store.save(record)
        except Exception as exc:
            raise C01Error("MODEL_EVIDENCE_OUTCOME_UNKNOWN", "EVIDENCE_SAVE",
                           f"Run {record['run_id']} 完成证据未确认；查询保留的派发意图，禁止重新付费。") from exc
        return self.store.inspect(UUID(record["run_id"]))


def preflight_report(preflight: dict) -> dict:
    """Readable inspection omits base64 bulk, keeps exact text and reconstructable byte reference."""
    report = deepcopy(preflight)
    request = report["request"]
    for part in request["input"][0]["content"]:
        if part["type"] == "input_image":
            prefix, image_data = part["image_url"].split(",", 1)
            part["image_url"] = f"{prefix},<omitted {len(image_data)} base64 characters>"
    report["request_display"] = "BASE64_OMITTED_ONLY; request_sha256 covers full exact JSON body"
    report["snapshot"] = {key: preflight["snapshot"][key]
                          for key in ("id", "capability", "task_id", "slot_id", "product_asset_id",
                                      "reference_asset_id", "context_sha256") if key in preflight["snapshot"]}
    report["image_source"] = preflight["snapshot"]["prompt"]["planned_model_input"]["images"][0]
    if len(preflight["snapshot"]["prompt"]["planned_model_input"]["images"]) > 1:
        report["image_sources"] = preflight["snapshot"]["prompt"]["planned_model_input"]["images"]
    return report
