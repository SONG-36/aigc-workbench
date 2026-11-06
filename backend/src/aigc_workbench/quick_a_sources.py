"""Current Quick-A sources and generic C01 execution; the fixed S03 CLI stays fixed."""

from dataclasses import asdict
from hashlib import sha256
from uuid import UUID, uuid4

from .c01_context import C01Error, applicable_rules, fingerprint, now
from .c01_model import vision_endpoint, DEFAULT_UNDERSTANDING_MODEL, VISION_OUTPUT_TOKENS, estimate_cost, multimodal_request
from .c01_prompt import OUTPUT_CONTRACT, compose_prompt
from .c01_real import C01RealService
from .domain import AssetRole
from .image_sizes import output_plan

BUSINESS_FILES = ("docs/business/01_BUSINESS_MODES.md", "docs/business/02_INTENT_AND_CONTEXT_RULES.md",
                  "docs/business/04_IMAGE_CREATION_WORK_METHOD.md")


def documents(root, files=BUSINESS_FILES):
    return [{"file": name, "sha256": sha256((root / name).read_bytes()).hexdigest()} for name in files]


def current_sources(service, task_id, slot_id):
    task = service.tasks.get_draft(task_id)
    slot = next((s for s in task.slots if s.id == slot_id), None)
    if task.mode != 'quick_a' or slot is None:
        raise C01Error('QUICK_A_SLOT_INVALID', 'BUSINESS_ROUTING', '需要当前Quick-A任务的合法图位。')
    products = [a for a in task.assets if a.role == AssetRole.PRODUCT_IMAGE]
    if not products or not slot.reference_asset_id:
        raise C01Error('QUICK_A_INPUT_MISSING', 'EFFECTIVE_STATE', '请保存实际商品图片并为本图位绑定营销参考。')
    # Existing uploads are append-only. The visible current source is the latest upload;
    # earlier originals remain available, and every execution freezes the chosen identity.
    product, product_bytes = service.tasks.read_asset(task_id, products[-1].id)
    reference, reference_bytes = service.tasks.read_asset(task_id, slot.reference_asset_id)
    if reference.role != AssetRole.MARKETING_REFERENCE:
        raise C01Error('QUICK_A_REFERENCE_ROLE_INVALID', 'EFFECTIVE_STATE', '图位必须绑定本任务的营销参考。')
    state = {"task_id": str(task.id), "mode": task.mode, "slot_id": str(slot.id),
             "slot_state": {"id": str(slot.id), "kind": slot.kind.value, "reference_asset_id": str(reference.id),
                            "output_size": slot.output_size, "requirements": slot.requirements},
             "product_asset_id": str(product.id), "reference_asset_id": str(reference.id),
             "image_inputs": [{"asset_id": str(a.id), "role": a.role.value, **asdict(a.image)} for a in (product, reference)],
             "product_selection": "LATEST_UPLOADED_PRODUCT_IMAGE / SHOWN_IN_WORKSPACE",
             "business_documents": documents(service.source_root), "output_plan": output_plan(slot.output_size),
             "operator_confirmed_information": []}
    return state, (product_bytes, reference_bytes)


def prepare_creation(service, state, images, models):
    identity = str(uuid4())
    context = {**state, "capability_specific_inputs": state['image_inputs'][0]}
    snapshot = {"id": identity, "created_at": now(), "capability": "QUICK_A_CREATION",
                "task_id": state['task_id'], "slot_id": state['slot_id'],
                "product_asset_id": state['product_asset_id'], "reference_asset_id": state['reference_asset_id'],
                "models": models,
                "context": context, "context_sha256": fingerprint(context),
                "effective_state": {"operation": "CREATE_ONE_QUICK_A_CANDIDATE", "scope": "ONE_SLOT",
                    "sources": state, "pass_protection": "UNCHANGED / NO_AUTOMATIC_PASS"},
                "prompt": {"planned_model_input": {"label": "BUSINESS COMMAND / NOT A MODEL REQUEST",
                    "images": state['image_inputs']}}, "model_api_call_attempted": False}
    return service.evidence.save_snapshot(snapshot, images[0])


class ProductUnderstandingService(C01RealService):
    def preflight(self, snapshot_id, model=DEFAULT_UNDERSTANDING_MODEL, max_output_tokens=VISION_OUTPUT_TOKENS):
        snapshot = self.service.inspect_snapshot(snapshot_id)
        if (snapshot['capability'] != 'C01' or snapshot['context']['operator_confirmed_information'] != []
                or snapshot['prompt'] != compose_prompt(snapshot['context'], snapshot['id'])
                or snapshot['context']['applicable_rules'] != applicable_rules(self.service.source_root)):
            raise C01Error('C01_CURRENT_INPUT_INVALID', 'MODEL_PREFLIGHT', 'C01来源、规则或四层指令已变化。')
        asset, original = self.service.tasks.read_asset(UUID(snapshot['task_id']), UUID(snapshot['product_asset_id']))
        frozen = self.service.evidence.read_snapshot_image(snapshot)
        if asset.role != AssetRole.PRODUCT_IMAGE or original != frozen:
            raise C01Error('C01_CURRENT_SOURCE_CHANGED', 'MODEL_PREFLIGHT', '商品身份或真实原图已变化。')
        request = multimodal_request(snapshot['prompt']['planned_model_input']['text'], frozen,
                                     asset.image.media_type, OUTPUT_CONTRACT, 'c01_visual_observation', model, max_output_tokens)
        return {"snapshot": snapshot, "request": request, "request_sha256": fingerprint(request),
                "endpoint": vision_endpoint(model), "cost": estimate_cost(snapshot, request), "network_requests": 0}


def usable_run(run, test_mode=False):
    return ((run['transport_label'] == 'REAL_PROVIDER_TRANSPORT' and run['model_api_call_attempted'] is True)
            or (test_mode and run['transport_label'] == 'OFFLINE_TRANSPORT_TEST'
                and run['model_api_call_attempted'] is False and run.get('offline_test_response') is not None))
