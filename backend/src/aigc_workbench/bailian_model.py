"""Two explicit Beijing Bailian models; native wire formats, no SDK retries or routing platform."""

import base64
from decimal import Decimal
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .c01_context import C01Error
from .c01_model import OpenAIResponsesTransport, _NoRedirect, _proxy_configuration
from .file_storage import inspect_image, MAX_UPLOAD_BYTES

VISION_MODEL = "qwen3.7-plus"
IMAGE_MODEL = "qwen-image-3.0-pro"
ORIGIN = "https://dashscope.aliyuncs.com"  # Official Beijing legacy domain remains supported.
VISION_PATH = "/compatible-mode/v1/chat/completions"
IMAGE_PATH = "/compatible-mode/v1/images/generations"
API_REFERENCE = "https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference"
VISION_REFERENCE = "https://help.aliyun.com/zh/model-studio/qwen3-7-plus"
PRICING = "https://help.aliyun.com/zh/model-studio/model-pricing"
CHECKED = "2026-11-06"
POLICY = "BAILIAN_ONE_SHOT"


def selected_provider():
    provider = os.environ.get("AIGC_MODEL_PROVIDER", "openai")
    if provider not in {"openai", "bailian"}:
        raise C01Error("PROVIDER_UNSUPPORTED", "ACCESS", "仅支持显式配置的供应商，不自动切换。")
    return provider


def details(model):
    if model not in {VISION_MODEL, IMAGE_MODEL}:
        raise C01Error("MODEL_NOT_SUPPORTED", "MODEL_PREFLIGHT", "未核实的百炼模型，禁止替换。")
    return {"provider": "Alibaba Bailian", "region": "cn-beijing",
            "endpoint": ORIGIN + (VISION_PATH if model == VISION_MODEL else IMAGE_PATH)}


def vision_request(text, images, schema, model, limit):
    # JSON Mode is provider-supported; the unchanged Layer 4 contract is strictly checked locally.
    return {"model": model, "stream": False, "enable_thinking": False,
            "temperature": 0, "max_tokens": limit, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": text},
                *[{"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," +
                   base64.b64encode(data).decode("ascii")}} for data, mime in images]]}]}


def response_text(raw, model):
    if raw.get("model") not in {model, "qwen3.7-plus-2026-05-26"}:
        raise C01Error("RESPONSE_MODEL_MISMATCH", "OUTPUT_PARSE", "响应不是指定的百炼视觉模型。")
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise C01Error("MODEL_RESPONSE_INCOMPLETE", "OUTPUT_PARSE", "百炼响应未完整完成，保留原始响应。")
    message = choices[0].get("message", {})
    if message.get("refusal") or not isinstance(message.get("content"), str):
        raise C01Error("MODEL_OUTPUT_MISSING", "OUTPUT_PARSE", "没有明确的结构化文本。")
    return message["content"]


def image_parameters(plan):
    # 1024x1536 satisfies official 512^2..2048^2 area and 1:8..8:1 aspect constraints.
    return {"model": IMAGE_MODEL, "size": plan["model_generation_size"], "n": 1,
            "prompt_extend": False, "watermark": False}


def image_request(parameters, prompt, images):
    if not 1 <= len(images) <= 3:
        raise C01Error("MODEL_INPUT_INVALID", "MODEL_PREFLIGHT", "指定图像模型仅允许1至3张输入图。")
    return {**parameters, "prompt": prompt, "image": [item["image_url"] for item in images]}


def quote(request):
    result = {**details(request["model"]), "currency": "CNY", "checked_on": CHECKED,
              "source": PRICING, "api_reference": API_REFERENCE if request["model"] == IMAGE_MODEL else VISION_REFERENCE,
              "provider_bill": None,
              "limitation": "标准公开价估算；非账单，不假定免费额度或缓存折扣。"}
    if request["model"] == IMAGE_MODEL:
        w, h = map(int, request["size"].split("x"))
        output = Decimal("0.25") if w * h <= 2250000 else Decimal("0.50")
        result.update(estimated_cny=str(Decimal("0.02") * len(request["image"]) + output),
                      input_images=len(request["image"]), output_images=1,
                      input_cny_per_image="0.02", output_cny_per_image=str(output))
    else:
        result.update(estimated_cny=None, input_cny_per_million="2", output_cny_per_million="8",
                      higher_input_tier="Over 256K input: 6 CNY input / 24 CNY output per million",
                      output_token_limit=request["max_tokens"],
                      note="无供应商预分词计量；不将文字字节或OpenAI图片Token公式冒充百炼精确估算。")
    return result


def usage_cost(raw):
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None
    if "input_image_count" in usage:
        i, o = usage.get("input_image_count"), usage.get("output_image_count")
        tier = usage.get("output_image_type")
        if type(i) is not int or type(o) is not int or min(i, o) < 0 or tier not in {"qima_output_1k", "qima_output_2k"}:
            return None
        amount = Decimal("0.02") * i + (Decimal("0.25") if tier.endswith("1k") else Decimal("0.50")) * o
    else:
        i, o = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if raw.get("model") not in {VISION_MODEL, "qwen3.7-plus-2026-05-26"} or type(i) is not int or type(o) is not int or min(i, o) < 0:
            return None
        factor = 1 if i <= 256000 else 3
        amount = (Decimal(i) * 2 + Decimal(o) * 8) * factor / 1000000
    return {"label": "USAGE_BASED_ESTIMATE_NOT_PROVIDER_BILL", "currency": "CNY", "cny": str(amount),
            "source": PRICING, "checked_on": CHECKED, "provider_bill": None}


def authorize(settings, confirmed, policy):
    from .config import load_provider_credentials
    if (selected_provider() != "bailian" or settings.environment != "development"
            or not settings.enable_c01_testing or not settings.enable_c01_real
            or not confirmed or policy != POLICY or settings.model_service_disabled_reason):
        raise C01Error("BAILIAN_EXECUTION_DISABLED", "ACCESS", "需开发环境、显式供应商及单次授权；不启用Pilot。")
    if load_provider_credentials() != "KEY_CONFIGURED":
        raise C01Error("MODEL_CREDENTIAL_MISSING", "ACCESS", "DASHSCOPE_API_KEY: KEY_MISSING。")


class BailianTransport(OpenAIResponsesTransport):
    origin = ORIGIN
    credential_name = "DASHSCOPE_API_KEY"
    allowed_requests = {("POST", VISION_PATH), ("POST", IMAGE_PATH)}

    def __init__(self, model):
        self.model = model
        details(model)

    def send(self, payload):
        request = json.loads(payload)
        if request.get("model") != self.model:
            raise C01Error("MODEL_TARGET_NOT_ALLOWED", "MODEL_TRANSPORT", "传输模型与请求不一致。")
        path = VISION_PATH if self.model == VISION_MODEL else IMAGE_PATH
        result, _ = self._request("POST", path, payload, timeout=600 if self.model == IMAGE_MODEL else 180)
        return result


def download_png(raw):
    """One unauthenticated GET to the documented result bucket; no redirects, no signed URL logging."""
    try:
        items = raw["data"]
        if len(items) != 1:
            raise ValueError()
        url = items[0]["url"]
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
                or parsed.fragment or not re.fullmatch(r"dashscope-result-[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com", parsed.hostname or "")):
            raise ValueError()
    except (ValueError, TypeError, KeyError, IndexError):
        raise C01Error("PROVIDER_OUTPUT_URL_INVALID", "IMAGE_DOWNLOAD", "图像结果URL不是允许的供应商HTTPS存储地址。") from None
    proxies, _ = _proxy_configuration(parsed.hostname)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=60) as response:
            content = response.read(MAX_UPLOAD_BYTES + 1)
            if response.status != 200 or len(content) > MAX_UPLOAD_BYTES:
                raise ValueError()
    except (OSError, urllib.error.URLError, ValueError):
        raise C01Error("PROVIDER_OUTPUT_DOWNLOAD_FAILED", "IMAGE_DOWNLOAD", "已收到供应商结果但图片下载失败；保留URL证据，不重新生图。") from None
    metadata = inspect_image(content, "image/png")
    if metadata.media_type != "image/png":
        raise C01Error("PROVIDER_OUTPUT_FORMAT_INVALID", "IMAGE_DOWNLOAD", "实际返回不是PNG，不伪造格式。")
    return content
