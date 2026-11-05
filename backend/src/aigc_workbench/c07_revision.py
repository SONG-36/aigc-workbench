"""C07: selected base PNG + effective constraints + this revision; no replay of old revision texts."""

import base64
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from .c01_context import C01Error, canonical, fingerprint, now
from .c01_prompt import LAYER_NAMES, final_text
from .c06_generation import C06Service, MODEL, ENDPOINT, estimate_image_cost, image_parameters
from .c06_candidates import CandidateStore
from .domain import AssetRole
from .source_paths import archived_path
from .image_sizes import OUTPUT_SIZES, output_plan, snapshot_output_plan

INSTRUCTION = "执行C07单图局部修改。Image 1是运营明确选定的基础图片；只执行本轮目标，其余有效设计、布局和内容尽可能保持。"
BOUNDARIES = (
    "Image 2为实际商品原图，控制商品身份、黑色主机、附件与可见连接关系；Image 3为当前营销参考，不可用其主体替代实售商品。"
    "不得因参考重新构图，不将初始制作方案当作重新生成指令。基础图片可能有错误，不独立证明商品事实。"
    "当前有效商品约束、逐图要求及禁止事项继续生效；初始AI营销决定仅有原来源权限，不是运营批准或独立认证。"
    "保持基础版本已实现且本轮未要求改变的文字、背景、布局和视觉设计。不重放历史修改意见，不假定较早基础图拥有后来版本的改动。"
    "不能无依据新增商品参数、附件、线路或认证；若修改目标与重要商品依据明确冲突，不创造事实解决。只影响当前图位。"
    "12V/24V系统兼容与DC3.5V-24V工作范围分开，不为工程车辆追加电压。新候选不覆盖基础图片或已有Pass。"
)
OUTPUT = "输出一张1024x1536 PNG修改候选。不得添加解释、版本标记或审核标识；不声明Pass。仅改指定目标，尽可能保持非目标像素与真实结构。"


def compose(context):
    output = OUTPUT
    if "output_plan" in context:
        plan = context["output_plan"]
        output = OUTPUT.replace("1024x1536", plan["model_generation_size"])
        if plan["postprocess"] != "NONE":
            output += f"原生方图随后以LANCZOS等比例放大交付至{plan['final_output_size']}；不裁切、不新增商品物理尺寸。"
    contents = [INSTRUCTION, BOUNDARIES, canonical(context), output]
    layers = [{"index": i + 1, "name": name, "version": "c07-selected-base-v1", "content": value,
               "content_sha256": sha256(value.encode()).hexdigest()}
              for i, (name, value) in enumerate(zip(LAYER_NAMES, contents))]
    return {"layers": layers, "text": final_text(layers)}


class C07Service(C06Service):
    def _sources(self, task_id, slot_id, base_id):
        base, image = CandidateStore(self.store.database, self.settings.data_dir).read_for_slot(task_id, slot_id, base_id)
        task = self.service.tasks.get_draft(task_id)
        slot = next((s for s in task.slots if s.id == slot_id), None)
        if task.mode != "quick_a" or slot is None or slot.output_size not in OUTPUT_SIZES or not slot.reference_asset_id:
            raise C01Error("REVISION_SLOT_UNSUPPORTED", "SOURCE_SELECTION", "本轮修改需要当前Quick-A图位、有效参考及受支持的输出像素尺寸。")
        product, product_bytes = self.service.tasks.read_asset(task_id, UUID(base["product_asset_id"]))
        reference, reference_bytes = self.service.tasks.read_asset(task_id, slot.reference_asset_id)
        if product.role != AssetRole.PRODUCT_IMAGE or reference.role != AssetRole.MARKETING_REFERENCE:
            raise C01Error("REVISION_SOURCE_ROLE_INVALID", "SOURCE_SELECTION", "实际商品与营销参考角色不正确。")
        parent = self.service.evidence.load_snapshot(UUID(base["snapshot_id"]))
        root_id = parent.get("root_c06_snapshot_id", parent["id"])
        root = self.service.evidence.load_snapshot(UUID(root_id))
        if root["capability"] != "C06" or root["task_id"] != str(task_id) or root["slot_id"] != str(slot_id):
            raise C01Error("REVISION_ROOT_INVALID", "SOURCE_SELECTION", "基础版本缺少对应的原始制作来源。")
        records = root["source_provenance"]
        if any(sha256(archived_path(self.settings, item["file"]).read_bytes()).hexdigest() != item["sha256"] for item in records):
            raise C01Error("REVISION_PROVENANCE_CHANGED", "SOURCE_SELECTION", "原制作决定或质量修订来源不一致。")
        source = next(item for item in records if Path(item["file"]).name == "05_C06_EXECUTION_HANDOFF.json")
        payload = archived_path(self.settings, source["file"]).read_bytes()
        if sha256(payload).hexdigest() != source["sha256"]:
            raise C01Error("REVISION_CONSTRAINT_SOURCE_CHANGED", "SOURCE_SELECTION", "归档制作约束哈希不一致。")
        original = json.loads(payload)["image_creation_decision"]
        image_inputs = [{"kind": "IMAGE_VERSION", "role": "revision_base", "source_image_version_id": str(base_id), **base["image"]},
                        {"kind": "ASSET", "role": "product_image", "source_asset_id": str(product.id), **asdict(product.image)},
                        {"kind": "ASSET", "role": "marketing_reference", "source_asset_id": str(reference.id), **asdict(reference.image)}]
        effective = {"current_slot_requirements": slot.requirements,
                     "current_slot_size": slot.output_size, "current_reference_asset_id": str(slot.reference_asset_id),
                     "product_protection": original["protected_product_and_accessories"],
                     "prohibited_changes": original["prohibited_changes"],
                     "marketing_relationship_locks": original["C04_MARKETING_CONTENT"]["parameter_and_relationship_locks"],
                     "marketing_source_status": "QUICK_A_REFERENCE_APPLICABLE / NOT_INDEPENDENTLY_CERTIFIED",
                     "initial_AI_content_decisions": original["C04_MARKETING_CONTENT"]["claim_decisions"],
                     "initial_AI_decision_status": "PROPOSED / NOT_OPERATOR_APPROVED; current explicit requirements govern presentation"}
        business_docs = [{"file": name, "sha256": sha256((self.service.source_root / name).read_bytes()).hexdigest()}
                         for name in ("docs/business/02_INTENT_AND_CONTEXT_RULES.md", "docs/business/04_IMAGE_CREATION_WORK_METHOD.md")]
        return {"base": base, "root": root, "root_id": root_id, "image_inputs": image_inputs,
                "bytes": [image, product_bytes, reference_bytes], "effective_constraints": effective,
                "source_provenance": records, "business_documents": business_docs}

    def prepare(self, task_id: UUID, slot_id: UUID, base_id: UUID, revision_text: str):
        revision_text = revision_text.strip()
        if not revision_text or len(revision_text) > 2000:
            raise C01Error("REVISION_TEXT_INVALID", "CONTEXT_ASSEMBLY", "需要1至2000字符的本轮修改意见。")
        sources = self._sources(task_id, slot_id, base_id)
        base, root = sources["base"], sources["root"]
        plan = output_plan(sources["effective_constraints"]["current_slot_size"])
        context = {"version": "c07-selected-base-v1", "business_source": {"task_id": str(task_id), "slot_id": str(slot_id), "mode": "quick_a"},
                   "base_image_version_id": str(base_id), "base_run_id": base["run_id"],
                   "image_inputs": sources["image_inputs"], "capability_specific_inputs": sources["image_inputs"][0],
                   "effective_constraints": sources["effective_constraints"], "revision_text": revision_text,
                   "revision_source": "EXPLICIT_OPERATOR_REQUEST", "historical_revision_instructions": "NOT_REPLAYED",
                   "business_documents": sources["business_documents"], "output_plan": plan}
        composition = compose(context)
        snapshot = {"id": str(uuid4()), "created_at": now(), "capability": "C07", "task_id": str(task_id), "slot_id": str(slot_id),
                    "product_asset_id": base["product_asset_id"], "reference_asset_id": sources["image_inputs"][2]["source_asset_id"],
                    "base_image_version_id": str(base_id), "root_c06_snapshot_id": sources["root_id"],
                    "decision_snapshot_id": root["decision_snapshot_id"], "upstream_run_ids": [base["run_id"]],
                    "source_provenance": sources["source_provenance"], "context": context, "context_sha256": fingerprint(context),
                    "parameters": image_parameters(plan), "output_plan": plan, "prompt": {"layers": composition["layers"], "planned_model_input": {
                        "label": "PLANNED MODEL INPUT", "text": composition["text"], "images": sources["image_inputs"]}},
                    "actual_model_input": None, "model_api_call_attempted": False,
                    "effective_state": {"operation": "REVISION_OF_EXPLICIT_BASE", "base_image_version_id": str(base_id),
                                        "scope": "ONE_SLOT", "operator_approved": False}}
        # The existing asset FK keeps original product lineage; capability_specific_inputs explicitly
        # identifies the frozen IMAGE_VERSION bytes. This copy is not another business Asset.
        return self.service.evidence.save_snapshot(snapshot, sources["bytes"][0])

    def preflight(self, snapshot_id: UUID, model: str, max_output_tokens: int = 4096):
        snapshot = self.service.evidence.load_snapshot(snapshot_id)
        context = snapshot["context"]
        plan = snapshot_output_plan(snapshot)
        if (snapshot.get("capability") != "C07" or model != MODEL or snapshot["parameters"] != image_parameters(plan)
                or context.get("output_plan", plan) != plan
                or context["effective_constraints"]["current_slot_size"] != plan["requested_output_size"]
                or fingerprint(context) != snapshot["context_sha256"]
                or compose(context)["text"] != snapshot["prompt"]["planned_model_input"]["text"]):
            raise C01Error("REVISION_SNAPSHOT_INVALID", "MODEL_PREFLIGHT", "C07快照、指令或模型不一致。")
        sources = self._sources(UUID(snapshot["task_id"]), UUID(snapshot["slot_id"]), UUID(snapshot["base_image_version_id"]))
        if (sources["image_inputs"] != context["image_inputs"]
                or sources["effective_constraints"] != context["effective_constraints"]
                or sources["business_documents"] != context["business_documents"]
                or sources["bytes"][0] != self.service.evidence.read_snapshot_image(snapshot)):
            raise C01Error("REVISION_SOURCE_CHANGED", "MODEL_PREFLIGHT", "基础图片或当前有效约束已变化，请重新核对；未调用。")
        images = [{"image_url": "data:" + item["media_type"] + ";base64," + base64.b64encode(data).decode()}
                  for item, data in zip(sources["image_inputs"], sources["bytes"])]
        request = {**image_parameters(plan), "prompt": snapshot["prompt"]["planned_model_input"]["text"], "images": images}
        return {"snapshot": snapshot, "request": request, "request_sha256": fingerprint(request), "endpoint": ENDPOINT,
                "cost": estimate_image_cost(request["prompt"], image_count=3, model_size=request["size"]), "network_requests": 0}

    def run(self, *args, **kwargs):
        if os.environ.get("AIGC_ENABLE_C07_REAL") != "1":
            raise C01Error("C07_EXECUTION_DISABLED", "ACCESS", "真实修改需要显式启用C07；未调用。")
        return super().run(*args, **kwargs)
