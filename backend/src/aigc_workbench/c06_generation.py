"""One explicit Quick-A image edit using the existing one-shot Runtime and two original images."""

import base64
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
from math import ceil
import os
from pathlib import Path
from uuid import UUID, uuid4

from .c01_context import C01Error, fingerprint, now
from .c01_model import MODEL as UNDERSTANDING_MODEL, OpenAIResponsesTransport, credential_status
from .c01_real import C01RealService
from .c06_candidates import CandidateStore
from .file_storage import inspect_image
from .quick_a_creation_decision import QuickACreationDecisionService

MODEL = "gpt-image-2.6-sunburst"
ENDPOINT = "https://api.openai.com/v1/images/edits"
PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing#image-generation"
CALCULATOR_SOURCE = "https://developers.openai.com/api/docs/guides/image-generation#calculating-costs"
PRICING_CHECKED = "2026-11-05"
PARAMETERS = {"model": MODEL, "size": "1024x1536", "quality": "high", "complexity": "high",
              "n": 1, "stream": False, "output_format": "png"}


def estimate_image_cost(prompt: str) -> dict:
    # Current official calculator: high grid edge=48, other edge=32 at 1024x1536.
    output_tokens = ceil(48 * 32 * (2_000_000 + 1024 * 1536) / 4_000_000)
    # Explicit planning allowances, not an official image-input tokenizer or provider cap.
    text_allowance = len(prompt.encode()) + 1024
    image_allowance = 32768 * 2
    total = (Decimal(text_allowance) * 5 + Decimal(image_allowance) * 8 + output_tokens * 30) / 1_000_000
    return {"label": "LOCAL_PLANNING_ESTIMATE_NOT_PROVIDER_CAP", "currency": "USD",
            "planning_envelope_usd": str(total), "official_output_token_estimate": output_tokens,
            "official_image_output_cost_usd": str(Decimal(output_tokens) * 30 / 1_000_000),
            "text_input_token_allowance": text_allowance, "image_input_token_allowance": image_allowance,
            "input_allowance_note": "本地保守规划余量，不是已验证的2.6输入分词公式；实际用量可能不同。",
            "rates_per_million_usd": {"text_input": "5", "image_input": "8", "image_output": "30"},
            "source": PRICING_SOURCE, "calculator_source": CALCULATOR_SOURCE, "checked_on": PRICING_CHECKED}


def image_usage_cost(raw: dict) -> dict | None:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_tokens_details", {})
    text, image, output = details.get("text_tokens"), details.get("image_tokens"), usage.get("output_tokens")
    if any(type(value) is not int or value < 0 for value in (text, image, output)):
        return None
    return {"label": "USAGE_BASED_ESTIMATE_NOT_PROVIDER_BILL", "currency": "USD",
            "usd": str((Decimal(text) * 5 + Decimal(image) * 8 + Decimal(output) * 30) / 1_000_000),
            "source": PRICING_SOURCE, "checked_on": PRICING_CHECKED, "cache_discount_assumed": False}


def png_from_response(raw: dict) -> bytes:
    data = raw.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise C01Error("C06_OUTPUT_COUNT_INVALID", "OUTPUT_PARSE", "未返回恰好一张候选图片；不重试。")
    try:
        return base64.b64decode(data[0]["b64_json"], validate=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise C01Error("C06_OUTPUT_ENCODING_INVALID", "OUTPUT_PARSE", "未返回有效Base64图片，保留原始响应。") from exc


class OpenAIImageEditsTransport(OpenAIResponsesTransport):
    def send(self, payload: bytes):
        result, _ = self._request("POST", "/v1/images/edits", payload,
                                  timeout=300, max_response_bytes=32 * 1024 * 1024)
        return result


class C06Service(C01RealService):
    def _sources(self, decision_id: UUID, handoff_directory: Path) -> dict:
        decision = QuickACreationDecisionService(self.settings, self.service).preflight(
            decision_id, UNDERSTANDING_MODEL)
        snapshot = decision["snapshot"]
        files = ["05_C06_EXECUTION_HANDOFF.md", "05_C06_EXECUTION_HANDOFF.json",
                 "04_QUALITY_AMENDMENTS.md", "04_QUALITY_AMENDMENTS.json", "structured-result.json"]
        manifest = json.loads((handoff_directory / "delivery-manifest-final.json").read_text())
        sources = []
        for name in files:
            path = handoff_directory / name
            digest = sha256(path.read_bytes()).hexdigest()
            if digest != manifest[name]["sha256"]:
                raise C01Error("C06_HANDOFF_CHANGED", "SOURCE_SELECTION", "归档制作交接文件哈希不一致。")
            sources.append({"file": str(path.resolve()), "sha256": digest,
                            "authority": "ORIGINAL_MODEL_OUTPUT" if name == "structured-result.json"
                            else "ASSISTANT_QUALITY_AMENDMENT / USER_CONSTRAINTS / REVIEW_PENDING"})
        handoff = json.loads((handoff_directory / files[1]).read_text())
        prompt = handoff["image_creation_decision"]["C06_creation_input"]
        if (handoff["task_id"] != snapshot["task_id"] or handoff["slot_id"] != snapshot["slot_id"]
                or handoff["output_size"] != "1024x1536" or handoff["C06_executed"] is not False
                or handoff["status"] != "PROPOSED / REVIEW PENDING / NOT OPERATOR APPROVED"
                or not prompt or prompt not in (handoff_directory / files[0]).read_text()
                or [item["sha256"] for item in handoff["images_in_order"]]
                != [item["sha256"] for item in snapshot["context"]["image_inputs"]]):
            raise C01Error("C06_HANDOFF_MISMATCH", "SOURCE_SELECTION", "制作交接身份、尺寸、状态或双图不一致。")
        with self.store.database.connection() as db:
            rows = db.execute("SELECT id FROM c01_model_runs WHERE snapshot_id=?", (str(decision_id),)).fetchall()
        if len(rows) != 1:
            raise C01Error("C06_DECISION_RUN_AMBIGUOUS", "SOURCE_SELECTION", "制作决定Run不唯一。")
        run = self.store.inspect(UUID(rows[0]["id"]))
        if (run["status"] != "SUCCEEDED" or run["transport_label"] != "REAL_PROVIDER_TRANSPORT"
                or run["snapshot_sha256"] != fingerprint(snapshot)
                or run["structured_output"]["content"] != json.loads((handoff_directory / "structured-result.json").read_text())):
            raise C01Error("C06_DECISION_RUN_MISMATCH", "SOURCE_SELECTION", "原始模型制作决定与归档不一致。")
        return {"decision_snapshot": snapshot, "decision_run_id": run["run_id"],
                "sources": sources, "prompt": prompt,
                "handoff_directory": str(handoff_directory.resolve()),
                "images": decision["request"]["input"][0]["content"][1:]}

    def prepare(self, decision_id: UUID, handoff_directory: Path, model: str) -> dict:
        if model != MODEL:
            raise C01Error("C06_MODEL_NOT_SUPPORTED", "MODEL_PREFLIGHT", "仅使用显式指定图像模型，不切换。")
        sources = self._sources(decision_id, handoff_directory)
        old = sources["decision_snapshot"]
        identity = str(uuid4())
        context = {"business_source": old["context"]["business_source"], "slot_state": old["context"]["slot_state"],
                   "image_inputs": old["context"]["image_inputs"],
                   "capability_specific_inputs": old["context"]["capability_specific_inputs"],
                   "operator_confirmed_information": [], "production_instruction": sources["prompt"],
                   "decision_snapshot_id": str(decision_id), "source_provenance": sources["sources"]}
        planned_images = old["prompt"]["planned_model_input"]["images"]
        snapshot = {"id": identity, "created_at": now(), "capability": "C06", "task_id": old["task_id"],
                    "slot_id": old["slot_id"], "product_asset_id": old["product_asset_id"],
                    "reference_asset_id": old["reference_asset_id"], "decision_snapshot_id": str(decision_id),
                    "upstream_run_ids": [sources["decision_run_id"]], "source_provenance": sources["sources"],
                    "handoff_directory": sources["handoff_directory"], "context": context,
                    "context_sha256": fingerprint(context), "effective_state": old["effective_state"],
                    "prompt": {"planned_model_input": {"label": "PLANNED MODEL INPUT", "text": sources["prompt"],
                                                       "images": planned_images}},
                    "actual_model_input": None, "input_classification": "PLANNED MODEL INPUT",
                    "model_api_call_attempted": False, "parameters": PARAMETERS}
        return self.service.evidence.save_snapshot(snapshot, base64.b64decode(sources["images"][0]["image_url"].split(",", 1)[1]))

    def preflight(self, snapshot_id: UUID, model: str, max_output_tokens: int = 4096) -> dict:
        snapshot = self.service.evidence.load_snapshot(snapshot_id)
        if (model != MODEL or snapshot.get("capability") != "C06" or snapshot.get("parameters") != PARAMETERS
                or fingerprint(snapshot["context"]) != snapshot["context_sha256"]):
            raise C01Error("C06_SNAPSHOT_INVALID", "MODEL_PREFLIGHT", "C06模型、参数或快照不一致。")
        sources = self._sources(UUID(snapshot["decision_snapshot_id"]), Path(snapshot["handoff_directory"]))
        if (sources["sources"] != snapshot["source_provenance"]
                or sources["prompt"] != snapshot["prompt"]["planned_model_input"]["text"]
                or sources["decision_snapshot"]["context"]["slot_state"] != snapshot["context"]["slot_state"]
                or self.service.evidence.read_snapshot_image(snapshot)
                != base64.b64decode(sources["images"][0]["image_url"].split(",", 1)[1])):
            raise C01Error("C06_SOURCE_CHANGED", "MODEL_PREFLIGHT", "C06制作依据、要求或原图变化。")
        request = {**PARAMETERS, "prompt": sources["prompt"],
                   "images": [{"image_url": part["image_url"]} for part in sources["images"]]}
        return {"label": "C06 PLANNED MODEL INPUT / NO REQUEST SENT", "snapshot": snapshot,
                "request": request, "request_sha256": fingerprint(request), "endpoint": ENDPOINT,
                "cost": estimate_image_cost(sources["prompt"]), "credentials": credential_status(),
                "model_api_call_attempted": False, "network_requests": 0}

    def parse_output(self, raw: dict, model: str) -> dict:
        if raw.get("model", model) != model:
            raise C01Error("C06_RESPONSE_MODEL_MISMATCH", "OUTPUT_PARSE", "响应模型与指定模型不同。")
        png = png_from_response(raw)
        metadata = inspect_image(png, "image/png")
        if (metadata.media_type, metadata.width, metadata.height) != ("image/png", 1024, 1536):
            raise C01Error("C06_OUTPUT_FORMAT_INVALID", "OUTPUT_PARSE", "实际输出不是1024x1536 PNG。")
        return {"image": asdict(metadata), "status": "CANDIDATE_REVIEW_PENDING", "operator_approved": False}

    def cost_from_usage(self, raw: dict) -> dict | None:
        return image_usage_cost(raw)

    def run(self, snapshot_id: UUID, model: str, operation_id: UUID, *, confirmed: bool,
            max_cost_usd: str, transport=None) -> dict:
        try:
            budget = Decimal(max_cost_usd)
            valid_budget = budget.is_finite() and 0 < budget <= 1
        except (ValueError, ArithmeticError, TypeError):
            valid_budget = False
        if os.environ.get("AIGC_ENABLE_C06_REAL") != "1" or not valid_budget:
            raise C01Error("C06_EXECUTION_DISABLED", "ACCESS", "需显式启用C06，且本次估算门槛不超过USD1。")
        record = super().run(snapshot_id, model, operation_id, confirmed=confirmed,
                             max_cost_usd=max_cost_usd, transport=transport or OpenAIImageEditsTransport())
        if record["status"] == "SUCCEEDED" and record["transport_label"] == "REAL_PROVIDER_TRANSPORT":
            snapshot = self.service.evidence.load_snapshot(snapshot_id)
            candidate = CandidateStore(self.store.database, self.settings.data_dir).save(
                record, snapshot, png_from_response(record["actual_model_response"]))
            return {**record, "candidate": candidate}
        return record
