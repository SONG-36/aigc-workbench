"""Four inspectable logical layers, one planned request, no provider SDK."""

from hashlib import sha256

from .c01_context import C01Error, canonical, fingerprint

TEMPLATE_VERSION = "c01-four-layer-v1"
LAYER_NAMES = ("Capability Instruction", "Rules / Boundaries", "Effective Context", "Output Contract")
INSTRUCTION = (
    "执行 C01 商品视觉理解：只观察所附目标商品图片中的可见结构、组件、文字和空间关系。"
    "不做营销设计、不生成或修改图片，不独立认证商品性能或参数。"
)
OUTPUT_FIELDS = ("visible_structure", "components", "visible_text", "spatial_relations",
                 "inferences", "uncertainties")
OUTPUT_CONTRACT = {
    "type": "object", "additionalProperties": False, "required": list(OUTPUT_FIELDS),
    "properties": {name: {"type": "array", "items": {"type": "string"}} for name in OUTPUT_FIELDS},
}
OUTPUT_INSTRUCTION = (
    "按下列 JSON Schema 输出。前四项仅写直接可见内容；推断只放 inferences；"
    "不清楚、未拍到或无法读清的内容放 uncertainties。不得把推断升级为观察或运营确认。\n"
)


def model_context(context: dict) -> dict:
    return {"operation": context["capability_specific_inputs"]["operation"],
            "operator_confirmed_information": context["operator_confirmed_information"],
            "image_input": context["capability_specific_inputs"]}


def final_text(layers: list[dict]) -> str:
    return "\n\n".join(f"Layer {layer['index']} — {layer['name']}\n{layer['content']}" for layer in layers)


def template_identity(layers: list[dict]) -> str:
    return fingerprint([{"name": l["name"], "content": l["content"], "version": l["version"]}
                        for l in layers if l["index"] != 3])


def compose_prompt(context: dict, snapshot_id: str) -> dict:
    if not context.get("applicable_rules"):
        raise C01Error("RULES_MISSING", "PROMPT_COMPOSITION", "C01 边界规则缺失。")
    contents = [
        INSTRUCTION,
        "\n".join(f"{r['id']}: {r['text']}" for r in context["applicable_rules"]),
        canonical(model_context(context)),
        OUTPUT_INSTRUCTION + canonical(OUTPUT_CONTRACT),
    ]
    sources = [
        {"kind": "FIXED_CAPABILITY_INSTRUCTION", "instruction_sha256": sha256(INSTRUCTION.encode()).hexdigest()},
        {"kind": "APPLICABLE_RULES", "documents": [r["source"] for r in context["applicable_rules"]]},
        {"kind": "DYNAMIC_EFFECTIVE_CONTEXT", "context_sha256": fingerprint(context)},
        {"kind": "FIXED_OUTPUT_CONTRACT", "instruction": OUTPUT_INSTRUCTION, "schema": OUTPUT_CONTRACT},
    ]
    layers = [{"index": i + 1, "name": name, "version": TEMPLATE_VERSION,
               "content": content, "content_sha256": sha256(content.encode()).hexdigest(), "source": source}
              for i, (name, content, source) in enumerate(zip(LAYER_NAMES, contents, sources))]
    image = context["capability_specific_inputs"]
    result = {
        "template_version": TEMPLATE_VERSION, "template_sha256": template_identity(layers),
        "layers": layers,
        "planned_model_input": {
            "label": "PLANNED MODEL INPUT", "text": final_text(layers),
            "images": [{"kind": "FROZEN_IMAGE_BYTES_REFERENCE", "snapshot_id": snapshot_id,
                        "source_asset_id": image["asset_id"], "role": "product_image",
                        "sha256": image["sha256"], "media_type": image["media_type"],
                        "size_bytes": image["size_bytes"]}],
            "provider": None, "model": None, "provider_serialization": "NOT_IMPLEMENTED_S03",
        },
        "actual_model_input": None, "model_api_call_attempted": False,
    }
    validate_composition(result, context, snapshot_id)
    return result


def validate_composition(prompt: dict, context: dict, snapshot_id: str) -> None:
    try:
        layers = prompt["layers"]
        if len(layers) != 4:
            raise ValueError("four layers required")
        image = prompt["planned_model_input"]["images"][0]
        source = context["capability_specific_inputs"]
        checks = [
            tuple(l["name"] for l in layers) == LAYER_NAMES,
            [l["index"] for l in layers] == [1, 2, 3, 4],
            all(l["content"] and l["content_sha256"] == sha256(l["content"].encode()).hexdigest()
                and l["version"] == prompt["template_version"] for l in layers),
            layers[0]["source"]["instruction_sha256"] == layers[0]["content_sha256"],
            layers[1]["content"] == "\n".join(f"{r['id']}: {r['text']}" for r in context["applicable_rules"]),
            layers[2]["content"] == canonical(model_context(context)),
            layers[2]["source"]["context_sha256"] == fingerprint(context),
            layers[3]["content"] == layers[3]["source"]["instruction"] + canonical(layers[3]["source"]["schema"]),
            prompt["template_sha256"] == template_identity(layers),
            prompt["planned_model_input"]["text"] == final_text(layers),
            len(prompt["planned_model_input"]["images"]) == 1,
            image["snapshot_id"] == snapshot_id and image["role"] == "product_image",
            image["source_asset_id"] == source["asset_id"] and image["sha256"] == source["sha256"],
            image["media_type"] == source["media_type"] and image["size_bytes"] == source["size_bytes"],
            prompt["actual_model_input"] is None and prompt["model_api_call_attempted"] is False,
        ]
        if not all(checks):
            raise ValueError("composition differs from its layers or context")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise C01Error("PROMPT_COMPOSITION_INVALID", "PROMPT_COMPOSITION",
                       "四层内容、来源或最终组装输入不一致。") from exc


def readable_request(snapshot: dict) -> str:
    planned = snapshot["prompt"]["planned_model_input"]
    image = planned["images"][0]
    return ("PLANNED MODEL INPUT — NOT SENT TO A MODEL\n"
            f"Snapshot: {snapshot['id']}\n\n" + planned["text"]
            + "\n\nIMAGE INPUT (passed separately as bytes, not as a path string):\n"
            + canonical(image) + "\n")
