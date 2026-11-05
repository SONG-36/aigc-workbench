"""Allowed output pixels and the fixed image-model compatibility policy."""

from .domain import OUTPUT_SIZES


def output_plan(size: str) -> dict:
    if size not in OUTPUT_SIZES:
        raise ValueError("不支持的图片输出尺寸。")
    upscale = size == "1536x1536"
    return {"requested_output_size": size,
            "model_generation_size": "1024x1024" if upscale else size,
            "final_output_size": size,
            "postprocess": "LANCZOS_UPSCALE" if upscale else "NONE"}


def snapshot_output_plan(snapshot: dict) -> dict:
    # Historical snapshots had no plan and used native portrait output. Never rewrite them.
    plan = snapshot.get("output_plan", output_plan("1024x1536"))
    if plan != output_plan(plan["requested_output_size"]):
        raise ValueError("图片输出计划不一致。")
    return plan
