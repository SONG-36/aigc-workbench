"""Deterministic PNG output validation and the single square-resolution fallback."""

from io import BytesIO

from PIL import Image

from .c01_context import C01Error
from .file_storage import inspect_image


def final_png(native: bytes, plan: dict) -> bytes:
    metadata = inspect_image(native, "image/png")
    if (metadata.media_type != "image/png"
            or f"{metadata.width}x{metadata.height}" != plan["model_generation_size"]):
        raise C01Error("C06_OUTPUT_FORMAT_INVALID", "OUTPUT_PARSE", "模型PNG尺寸与实际请求不一致。")
    if plan["postprocess"] == "NONE":
        return native
    # Only 1024 square -> 1536 square is supported. No crop, padding or aspect change.
    if (plan["model_generation_size"], plan["final_output_size"], plan["postprocess"]) != (
            "1024x1024", "1536x1536", "LANCZOS_UPSCALE"):
        raise C01Error("IMAGE_OUTPUT_PLAN_INVALID", "OUTPUT_PARSE", "输出处理计划不支持。")
    stream = BytesIO()
    with Image.open(BytesIO(native)) as image:
        # Palette PNGs require RGB/RGBA conversion for real LANCZOS interpolation.
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        image.resize((1536, 1536), Image.Resampling.LANCZOS).save(stream, "PNG")
    result = stream.getvalue()
    inspect_image(result, "image/png")
    return result
