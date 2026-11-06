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

from .c01_context import C01Error, canonical, fingerprint, now
from .c01_model import MODEL as UNDERSTANDING_MODEL, OpenAIResponsesTransport, credential_status
from .c01_real import C01RealService
from .c06_candidates import CandidateStore, private_write
from .file_storage import inspect_image
from .source_paths import archived_path
from .quick_a_creation_decision import QuickACreationDecisionService, compose_decision_prompt
from .image_sizes import output_plan, snapshot_output_plan
from .image_output import final_png

MODEL = "gpt-image-2.6-sunburst"
ENDPOINT = "https://api.openai.com/v1/images/edits"
PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing#image-generation"
CALCULATOR_SOURCE = "https://developers.openai.com/api/docs/guides/image-generation#calculating-costs"
PRICING_CHECKED = "2026-11-05"
PARAMETERS = {"model": MODEL, "size": "1024x1536", "quality": "high", "complexity": "high",
              "n": 1, "stream": False, "output_format": "png"}


def image_parameters(plan: dict, model: str = MODEL) -> dict:
    from .bailian_model import IMAGE_MODEL, image_parameters as bailian_parameters
    if model == IMAGE_MODEL:
        return bailian_parameters(plan)
    return {**PARAMETERS, "size": plan["model_generation_size"]}


def generation_prompt(sources: dict, plan: dict) -> str:
    if (plan["requested_output_size"] == plan["model_generation_size"]
            == sources["decision_snapshot"]["context"]["slot_state"]["output_size"]):
        return sources["prompt"]
    return (sources["prompt"] + "\n\nOUTPUT PIXEL SIZE OVERRIDE (not product physical dimensions): "
            + f"requested_output_size={plan['requested_output_size']}; model_generation_size={plan['model_generation_size']}; "
            + f"final_output_size={plan['final_output_size']}; postprocess={plan['postprocess']}. "
            + "Use the actual model canvas for composition, overriding only prior canvas pixel size/aspect instructions. "
            + "Preserve all product, accessory, text and marketing constraints. Do not crop important content.")


def estimate_image_cost(prompt: str, image_count: int = 2, model_size: str = "1024x1536") -> dict:
    width, height = map(int, model_size.split("x"))
    # High-quality grid long edge=48; preserve the actual request aspect ratio.
    grid_width, grid_height = ceil(48 * width / max(width, height)), ceil(48 * height / max(width, height))
    output_tokens = ceil(grid_width * grid_height * (2_000_000 + width * height) / 4_000_000)
    # Explicit planning allowances, not an official image-input tokenizer or provider cap.
    text_allowance = len(prompt.encode()) + 1024
    image_allowance = 32768 * image_count
    total = (Decimal(text_allowance) * 5 + Decimal(image_allowance) * 8 + output_tokens * 30) / 1_000_000
    return {"label": "LOCAL_PLANNING_ESTIMATE_NOT_PROVIDER_CAP", "currency": "USD",
            "planning_envelope_usd": str(total), "official_output_token_estimate": output_tokens,
            "model_generation_size": model_size,
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
    def __init__(self, settings, service, *, test_mode=False):
        super().__init__(settings, service)
        if test_mode and settings.environment != 'development':
            raise C01Error('TEST_OUTPUT_DISABLED', 'ACCESS', 'TEST_ONLY输出仅允许隔离的开发环境。')
        self.test_mode = test_mode
        from .bailian_model import selected_provider, IMAGE_MODEL
        self.image_model = IMAGE_MODEL if selected_provider() == "bailian" else MODEL
        self._downloaded_png = None

    def prepare_current_decision(self, run):
        """Derive an immutable execution handoff from the saved current-slot result, not a CLI file."""
        snapshot = self.service.evidence.load_snapshot(UUID(run['snapshot_id']))
        from .quick_a_sources import usable_run
        if (run['status'] != 'SUCCEEDED' or not usable_run(run, self.test_mode)
                or snapshot['capability'] != 'C03_C04_C05'
                or snapshot['context']['version'] != 'quick-a-current-slot-decision-v1'
                or run['snapshot_sha256'] != fingerprint(snapshot)):
            raise C01Error('CREATION_RESULT_INVALID', 'SOURCE_SELECTION', '制作决定不是本执行模式下的成功结果。')
        result = run['structured_output']['content']
        decision = result['02_IMAGE_CREATION_DECISION']
        directory = self.settings.data_dir / 'creation_decisions' / UUID(snapshot['id']).hex
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        handoff = {key: snapshot[key] for key in ('task_id', 'slot_id')}
        handoff.update(output_size=snapshot['context']['slot_state']['output_size'], C06_executed=False,
            status='PROPOSED / REVIEW PENDING / NOT OPERATOR APPROVED',
            images_in_order=snapshot['context']['image_inputs'], image_creation_decision=decision,
            decision_run_id=run['run_id'], authority='AI_DERIVED / NO_HUMAN_AMENDMENTS',
            execution_mode='TEST_ONLY' if self.test_mode else 'REAL')
        for name, content in {'structured-result.json': canonical(result),
                              '05_C06_EXECUTION_HANDOFF.json': canonical(handoff),
                              '05_C06_EXECUTION_HANDOFF.md': decision['C06_creation_input']}.items():
            path = directory / name
            data = content.encode()
            if path.exists() or path.is_symlink():
                if path.is_symlink() or path.read_bytes() != data:
                    raise C01Error('C06_HANDOFF_CHANGED', 'SOURCE_SELECTION', '已有制作交接文件不同，未覆盖。')
            else:
                private_write(path, data)
        return self.prepare(UUID(snapshot['id']), directory, self.image_model)

    def _current_decision_sources(self, decision_id, directory):
        from .quick_a_sources import usable_run
        service = QuickACreationDecisionService(self.settings, self.service, workspace=True, test_mode=self.test_mode)
        flight = service.preflight(decision_id, UNDERSTANDING_MODEL)
        snapshot = flight['snapshot']
        expected = self.settings.data_dir / 'creation_decisions' / decision_id.hex
        if archived_path(self.settings, directory).resolve() != expected.resolve():
            raise C01Error('C06_HANDOFF_MISMATCH', 'SOURCE_SELECTION', '当前任务交接位置不一致。')
        handoff = json.loads((expected / '05_C06_EXECUTION_HANDOFF.json').read_text())
        run = self.store.inspect(UUID(handoff['decision_run_id']))
        decision = run['structured_output']['content']['02_IMAGE_CREATION_DECISION']
        prompt = decision['C06_creation_input']
        if (run['snapshot_id'] != str(decision_id) or run['snapshot_sha256'] != fingerprint(snapshot)
                or run['status'] != 'SUCCEEDED' or not usable_run(run, self.test_mode)
                or handoff['task_id'] != snapshot['task_id'] or handoff['slot_id'] != snapshot['slot_id']
                or handoff['image_creation_decision'] != decision
                or handoff['images_in_order'] != snapshot['context']['image_inputs']
                or handoff['output_size'] != snapshot['context']['slot_state']['output_size']
                or (expected / '05_C06_EXECUTION_HANDOFF.md').read_text() != prompt
                or json.loads((expected / 'structured-result.json').read_text()) != run['structured_output']['content']):
            raise C01Error('C06_HANDOFF_MISMATCH', 'SOURCE_SELECTION', '当前图位制作决定及其真实来源不一致。')
        sources = [{'file': str((directory / name).resolve()), 'sha256': sha256((expected / name).read_bytes()).hexdigest(),
                    'authority': 'AI_DERIVED / TEST_ONLY' if self.test_mode else 'ORIGINAL_MODEL_OUTPUT / PROPOSED'}
                   for name in ('structured-result.json', '05_C06_EXECUTION_HANDOFF.json', '05_C06_EXECUTION_HANDOFF.md')]
        return {'decision_snapshot': snapshot, 'decision_run_id': run['run_id'], 'sources': sources, 'prompt': prompt,
                'handoff_directory': str(directory.resolve()), 'slot_state': snapshot['context']['slot_state'],
                'images': flight['request']['input'][0]['content'][1:]}

    def _sources(self, decision_id: UUID, handoff_directory: Path) -> dict:
        # Revalidate the archived C03-C05 decision without rewriting it or its Prompt.
        # Only the current output pixel size may differ; all other source/state checks remain strict.
        decision = QuickACreationDecisionService(self.settings, self.service)
        snapshot = self.service.evidence.load_snapshot(decision_id)
        if snapshot['context']['version'] == 'quick-a-current-slot-decision-v1':
            return self._current_decision_sources(decision_id, handoff_directory)
        context = snapshot["context"]
        if (snapshot.get("capability") != "C03_C04_C05" or fingerprint(context) != snapshot["context_sha256"]
                or snapshot["prompt"] != compose_decision_prompt(context, str(decision_id))):
            raise C01Error("DECISION_SNAPSHOT_INVALID", "MODEL_PREFLIGHT", "原制作决定快照不一致。")
        selection, product, reference, runs = decision.select_sources(
            UUID(snapshot["task_id"]), UUID(snapshot["slot_id"]), UUID(snapshot["product_asset_id"]),
            UUID(snapshot["reference_asset_id"]), *[UUID(value) for value in snapshot["upstream_run_ids"]])
        current_slot = selection["slot_state"]
        comparable = {**selection, "slot_state": {**current_slot,
                      "output_size": snapshot["source_selection"]["slot_state"]["output_size"]}}
        reference_snapshot = self.service.evidence.load_snapshot(UUID(runs[1]["snapshot_id"]))
        if (comparable != snapshot["source_selection"] or runs != context["upstream_results"]
                or product != self.service.evidence.read_snapshot_image(snapshot)
                or reference != self.service.evidence.read_snapshot_image(reference_snapshot)):
            raise C01Error("DECISION_SOURCE_CHANGED", "MODEL_PREFLIGHT", "制作要求、原图或上游依据已变化。")
        output_plan(current_slot["output_size"])
        images = [{"image_url": "data:" + item["media_type"] + ";base64," + base64.b64encode(data).decode()}
                  for item, data in zip(context["image_inputs"], (product, reference))]
        files = ["05_C06_EXECUTION_HANDOFF.md", "05_C06_EXECUTION_HANDOFF.json",
                 "04_QUALITY_AMENDMENTS.md", "04_QUALITY_AMENDMENTS.json", "structured-result.json"]
        local_directory = archived_path(self.settings, handoff_directory)
        manifest = json.loads((local_directory / "delivery-manifest-final.json").read_text())
        sources = []
        for name in files:
            path = handoff_directory / name
            digest = sha256(archived_path(self.settings, path).read_bytes()).hexdigest()
            if digest != manifest[name]["sha256"]:
                raise C01Error("C06_HANDOFF_CHANGED", "SOURCE_SELECTION", "归档制作交接文件哈希不一致。")
            sources.append({"file": str(path.resolve()), "sha256": digest,
                            "authority": "ORIGINAL_MODEL_OUTPUT" if name == "structured-result.json"
                            else "ASSISTANT_QUALITY_AMENDMENT / USER_CONSTRAINTS / REVIEW_PENDING"})
        handoff = json.loads((local_directory / files[1]).read_text())
        prompt = handoff["image_creation_decision"]["C06_creation_input"]
        if (handoff["task_id"] != snapshot["task_id"] or handoff["slot_id"] != snapshot["slot_id"]
                or handoff["output_size"] != context["slot_state"]["output_size"] or handoff["C06_executed"] is not False
                or handoff["status"] != "PROPOSED / REVIEW PENDING / NOT OPERATOR APPROVED"
                or not prompt or prompt not in (local_directory / files[0]).read_text()
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
                or run["structured_output"]["content"] != json.loads((local_directory / "structured-result.json").read_text())):
            raise C01Error("C06_DECISION_RUN_MISMATCH", "SOURCE_SELECTION", "原始模型制作决定与归档不一致。")
        return {"decision_snapshot": snapshot, "decision_run_id": run["run_id"],
                "sources": sources, "prompt": prompt,
                "handoff_directory": str(handoff_directory.resolve()),
                "images": images, "slot_state": current_slot}

    def prepare(self, decision_id: UUID, handoff_directory: Path, model: str) -> dict:
        if model != self.image_model:
            raise C01Error("C06_MODEL_NOT_SUPPORTED", "MODEL_PREFLIGHT", "仅使用显式指定图像模型，不切换。")
        sources = self._sources(decision_id, handoff_directory)
        old = sources["decision_snapshot"]
        identity = str(uuid4())
        plan = output_plan(sources["slot_state"]["output_size"])
        prompt = generation_prompt(sources, plan)
        context = {"business_source": old["context"]["business_source"], "slot_state": sources["slot_state"],
                   "image_inputs": old["context"]["image_inputs"],
                   "capability_specific_inputs": old["context"]["capability_specific_inputs"],
                   "operator_confirmed_information": [], "production_instruction": prompt,
                   "decision_snapshot_id": str(decision_id), "source_provenance": sources["sources"]}
        planned_images = old["prompt"]["planned_model_input"]["images"]
        snapshot = {"id": identity, "created_at": now(), "capability": "C06", "task_id": old["task_id"],
                    "slot_id": old["slot_id"], "product_asset_id": old["product_asset_id"],
                    "reference_asset_id": old["reference_asset_id"], "decision_snapshot_id": str(decision_id),
                    "upstream_run_ids": [sources["decision_run_id"]], "source_provenance": sources["sources"],
                    "decision_template_version": old['context']['version'],
                    "handoff_directory": sources["handoff_directory"], "context": context,
                    "context_sha256": fingerprint(context),
                    "effective_state": {**old["effective_state"], "slot_state": sources["slot_state"], "output_plan": plan},
                    "prompt": {"planned_model_input": {"label": "PLANNED MODEL INPUT", "text": prompt,
                                                       "images": planned_images}},
                    "actual_model_input": None, "input_classification": "PLANNED MODEL INPUT",
                    "model_api_call_attempted": False, "parameters": image_parameters(plan, self.image_model), "output_plan": plan}
        return self.service.evidence.save_snapshot(snapshot, base64.b64decode(sources["images"][0]["image_url"].split(",", 1)[1]))

    def preflight(self, snapshot_id: UUID, model: str, max_output_tokens: int = 4096) -> dict:
        snapshot = self.service.evidence.load_snapshot(snapshot_id)
        plan = snapshot_output_plan(snapshot)
        if (model != self.image_model or snapshot.get("capability") != "C06" or snapshot.get("parameters") != image_parameters(plan, self.image_model)
                or fingerprint(snapshot["context"]) != snapshot["context_sha256"]):
            raise C01Error("C06_SNAPSHOT_INVALID", "MODEL_PREFLIGHT", "C06模型、参数或快照不一致。")
        sources = self._sources(UUID(snapshot["decision_snapshot_id"]), Path(snapshot["handoff_directory"]))
        if (sources["sources"] != snapshot["source_provenance"]
                or generation_prompt(sources, plan) != snapshot["prompt"]["planned_model_input"]["text"]
                or sources["slot_state"] != snapshot["context"]["slot_state"]
                or plan["requested_output_size"] != sources["slot_state"]["output_size"]
                or self.service.evidence.read_snapshot_image(snapshot)
                != base64.b64decode(sources["images"][0]["image_url"].split(",", 1)[1])):
            raise C01Error("C06_SOURCE_CHANGED", "MODEL_PREFLIGHT", "C06制作依据、要求或原图变化。")
        request = {**image_parameters(plan, self.image_model), "prompt": snapshot["prompt"]["planned_model_input"]["text"],
                   "images": [{"image_url": part["image_url"]} for part in sources["images"]]}
        from .bailian_model import IMAGE_MODEL, image_request, quote, details
        endpoint, cost = ENDPOINT, estimate_image_cost(request["prompt"], model_size=request["size"])
        if model == IMAGE_MODEL:
            request = image_request(image_parameters(plan, model), request["prompt"], sources["images"])
            endpoint, cost = details(model)["endpoint"], quote(request)
        return {"label": "C06 PLANNED MODEL INPUT / NO REQUEST SENT", "snapshot": snapshot,
                "request": request, "request_sha256": fingerprint(request), "endpoint": endpoint,
                "cost": cost, "credentials": credential_status(model),
                "model_api_call_attempted": False, "network_requests": 0}

    def parse_output(self, raw: dict, model: str) -> dict:
        if raw.get("model", model) != model:
            raise C01Error("C06_RESPONSE_MODEL_MISMATCH", "OUTPUT_PARSE", "响应模型与指定模型不同。")
        png = png_from_response(raw)
        metadata = inspect_image(png, "image/png")
        if (metadata.media_type, metadata.width, metadata.height) != ("image/png", 1024, 1536):
            raise C01Error("C06_OUTPUT_FORMAT_INVALID", "OUTPUT_PARSE", "实际输出不是1024x1536 PNG。")
        return {"image": asdict(metadata), "status": "CANDIDATE_REVIEW_PENDING", "operator_approved": False}

    def parse_execution_output(self, raw: dict, model: str, snapshot: dict) -> dict:
        if "output_plan" not in snapshot:
            return self.parse_output(raw, model)  # Historical native portrait snapshots.
        if raw.get("model", model) != model:
            raise C01Error("C06_RESPONSE_MODEL_MISMATCH", "OUTPUT_PARSE", "响应模型与指定模型不同。")
        native = self.native_png(raw)
        plan = snapshot_output_plan(snapshot)
        final = final_png(native, plan)
        return {"image": asdict(inspect_image(final, "image/png")),
                "native_image": asdict(inspect_image(native, "image/png")), **plan,
                "status": "CANDIDATE_REVIEW_PENDING", "operator_approved": False}

    def prepare_response(self, raw, record):
        from .bailian_model import IMAGE_MODEL, download_png
        if record["requested_model"] != IMAGE_MODEL:
            return
        self.store.save(record)  # Durably retain the native URL response BEFORE a potentially slow download.
        self._downloaded_png = download_png(raw)
        directory = self.settings.data_dir / "provider_outputs"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (UUID(record["run_id"]).hex + ".png")
        private_write(path, self._downloaded_png)
        record["downloaded_output"] = {"file": str(path.resolve()),
            "sha256": sha256(self._downloaded_png).hexdigest(), "size_bytes": len(self._downloaded_png),
            "source": "ACTUAL_PROVIDER_RESULT_URL" if record["model_api_call_attempted"] else "ACTUAL_TEST_ADAPTER_RESULT_URL / TEST_ONLY", "authorization_header_forwarded": False}

    def native_png(self, raw, record=None):
        from .bailian_model import IMAGE_MODEL
        if self.image_model != IMAGE_MODEL:
            return png_from_response(raw)
        if record is not None:
            path = self.settings.data_dir / "provider_outputs" / (UUID(record["run_id"]).hex + ".png")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read(10 * 1024 * 1024 + 1)
            if sha256(content).hexdigest() != record["downloaded_output"]["sha256"]:
                raise C01Error("PROVIDER_IMAGE_CORRUPT", "CANDIDATE_SAVE", "下载PNG身份不一致。")
            return content
        if self._downloaded_png is None:
            raise C01Error("PROVIDER_IMAGE_MISSING", "OUTPUT_PARSE", "缺少已下载的真实PNG。")
        return self._downloaded_png

    def cost_from_usage(self, raw: dict) -> dict | None:
        from .bailian_model import IMAGE_MODEL, usage_cost
        return usage_cost(raw) if self.image_model == IMAGE_MODEL else image_usage_cost(raw)

    def run(self, snapshot_id: UUID, model: str, operation_id: UUID, *, confirmed: bool,
            max_cost_usd: str, transport=None) -> dict:
        from .bailian_model import IMAGE_MODEL, POLICY, BailianTransport
        bailian = self.image_model == IMAGE_MODEL and max_cost_usd == POLICY
        pilot = self.settings.environment == "pilot" and max_cost_usd == "UNRESTRICTED_PILOT"
        try:
            budget = Decimal(max_cost_usd)
            valid_budget = budget.is_finite() and 0 < budget <= 1
        except (ValueError, ArithmeticError, TypeError):
            valid_budget = False
        if os.environ.get("AIGC_ENABLE_C06_REAL") != "1" or not (bailian or pilot or valid_budget):
            raise C01Error("C06_EXECUTION_DISABLED", "ACCESS",
                           "Pilot真实图像执行未启用或执行策略不一致。" if self.settings.environment == "pilot"
                           else "需显式启用C06，且本次估算门槛不超过USD1。")
        record = super().run(snapshot_id, model, operation_id, confirmed=confirmed,
                             max_cost_usd=max_cost_usd, transport=transport or (BailianTransport(model) if bailian else OpenAIImageEditsTransport()))
        test_output = self.test_mode and record['transport_label'] == 'OFFLINE_TRANSPORT_TEST'
        if record["status"] == "SUCCEEDED" and (record["transport_label"] == "REAL_PROVIDER_TRANSPORT" or test_output):
            snapshot = self.service.evidence.load_snapshot(snapshot_id)
            native = self.native_png(record["offline_test_response"] if test_output else record["actual_model_response"], record)
            final = final_png(native, snapshot_output_plan(snapshot))
            store = CandidateStore(self.store.database, self.settings.data_dir)
            if bailian:
                candidate = store.save(record, snapshot, final, test_only=test_output, native_png=native)
            else:
                candidate = store.save(record, snapshot, final, test_only=True) if test_output else store.save(record, snapshot, final)
            return {**record, "candidate": candidate}
        return record
