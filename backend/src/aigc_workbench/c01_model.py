"""One explicit OpenAI Responses request. Pure preparation and one-shot HTTPS transport."""

import base64
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import http.client
import json
from math import ceil
import os

from .c01_context import C01Error, canonical
from .c01_prompt import OUTPUT_CONTRACT, OUTPUT_FIELDS

MODEL = "gpt-4.1-mini-2025-04-14"
ENDPOINT = "https://api.openai.com/v1/responses"
PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICING_CHECKED = "2026-11-05"
INPUT_RATE, OUTPUT_RATE = Decimal("0.40"), Decimal("1.60")  # USD / million tokens, standard.
TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def credential_status() -> dict:
    # Never auto-load env files, read another repository's credentials, or return their values.
    return {"OPENAI_API_KEY_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "custom_endpoint_configured": bool(os.environ.get("OPENAI_BASE_URL")),
            "account_access": "NOT_CHECKED_NO_NETWORK", "quota": "UNKNOWN"}


def build_request(snapshot: dict, image: bytes, model: str, max_output_tokens: int) -> dict:
    if model != MODEL:
        raise C01Error("MODEL_NOT_SUPPORTED", "MODEL_PREFLIGHT", "仅接入已核对的显式模型；不自动替换。")
    if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 4096:
        raise C01Error("OUTPUT_LIMIT_INVALID", "MODEL_PREFLIGHT", "max_output_tokens 必须在 256～4096。")
    source = snapshot["context"]["capability_specific_inputs"]
    if (source["media_type"] != "image/jpeg" or not image.startswith(b"\xff\xd8\xff")
            or sha256(image).hexdigest() != source["sha256"] or len(image) != source["size_bytes"]):
        raise C01Error("MODEL_IMAGE_MISMATCH", "MODEL_PREFLIGHT", "需要完整性正确的原始商品 JPEG。")
    schema = snapshot["prompt"]["layers"][3]["source"]["schema"]
    if schema != OUTPUT_CONTRACT:
        raise C01Error("OUTPUT_CONTRACT_CHANGED", "MODEL_PREFLIGHT", "快照输出契约与 S02 不一致。")
    return {
        "model": model, "store": False, "stream": False, "service_tier": "default",
        "temperature": 0, "max_output_tokens": max_output_tokens,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": snapshot["prompt"]["planned_model_input"]["text"]},
            {"type": "input_image", "detail": "high",
             "image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "c01_visual_observation",
                            "strict": True, "schema": schema}},
    }


def estimate_cost(snapshot: dict, request: dict) -> dict:
    """Conservative planning envelope, NOT a provider-enforced billing cap or exact tokenizer."""
    source = snapshot["context"]["capability_specific_inputs"]
    # Use unresized patches: intentionally overestimates when the provider downsizes.
    image_tokens = ceil(ceil(source["width"] / 32) * ceil(source["height"] / 32) * 1.62)
    text_bytes = len(request["input"][0]["content"][0]["text"].encode())
    schema_bytes = len(canonical(request["text"]).encode())
    input_envelope = text_bytes + schema_bytes + image_tokens + 4096
    amount = (Decimal(input_envelope) * INPUT_RATE
              + Decimal(request["max_output_tokens"]) * OUTPUT_RATE) / 1_000_000
    return {"currency": "USD", "planning_envelope_usd": str(amount),
            "input_token_envelope": input_envelope, "unresized_image_token_envelope": image_tokens,
            "output_token_limit": request["max_output_tokens"],
            "input_usd_per_million": str(INPUT_RATE), "output_usd_per_million": str(OUTPUT_RATE),
            "source": PRICING_SOURCE, "checked_on": PRICING_CHECKED,
            "method": "UTF8 text/schema bytes + unresized 32px patches x1.62 +4096 overhead; no cache discount",
            "limitation": "估算保护，非精确分词或供应商硬费用上限；实际账单、税费和额度未知。"}


def authorize(settings, confirmed: bool, max_cost_usd: str, quote: dict) -> None:
    if settings.environment != "development" or not settings.enable_c01_real or not confirmed:
        raise C01Error("REAL_EXECUTION_DISABLED", "ACCESS",
                       "真实调用需 development、AIGC_ENABLE_C01_REAL=1 和明确付费确认。")
    try:
        budget = Decimal(max_cost_usd)
        if not budget.is_finite() or budget <= 0:
            raise ValueError()
    except (ValueError, ArithmeticError, TypeError) as exc:
        raise C01Error("BUDGET_INVALID", "ACCESS", "需要明确、有限、正数的 USD 上限。") from exc
    if budget < Decimal(quote["planning_envelope_usd"]):
        raise C01Error("BUDGET_TOO_LOW", "ACCESS", "预算低于保守预检估算；未调用。")
    if not 0 <= (date.today() - date.fromisoformat(PRICING_CHECKED)).days <= 7:
        raise C01Error("PRICING_RECHECK_REQUIRED", "ACCESS", "价格核对已过期，先重新核对官方依据。")
    if os.environ.get("OPENAI_BASE_URL"):
        raise C01Error("CUSTOM_ENDPOINT_NOT_SUPPORTED", "ACCESS", "本适配器仅支持官方固定端点。")
    if not credential_status()["OPENAI_API_KEY_configured"]:
        raise C01Error("MODEL_CREDENTIAL_MISSING", "ACCESS", "进程未配置 OPENAI_API_KEY。")


@dataclass(frozen=True)
class HTTPResult:
    status: int
    body: bytes
    request_id: str | None


class OpenAIResponsesTransport:
    """No SDK, retries, redirects, proxy/env endpoints, tools, file uploads or follow-up requests."""
    label = "REAL_PROVIDER_TRANSPORT"

    def send(self, payload: bytes) -> HTTPResult:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key or "\r" in key or "\n" in key:
            raise C01Error("MODEL_CREDENTIAL_INVALID", "MODEL_TRANSPORT", "凭证缺失或格式无效。")
        connection = http.client.HTTPSConnection("api.openai.com", timeout=TIMEOUT_SECONDS)
        try:
            connection.request("POST", "/v1/responses", body=payload,
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise C01Error("RESPONSE_TOO_LARGE", "MODEL_TRANSPORT", "响应超过本实验读取上限；结果不完整。")
            # A provider may echo a key in an error. Preserve a redacted raw body, never log headers.
            body = body.replace(key.encode(), b"[REDACTED_API_KEY]")
            request_id = response.getheader("x-request-id")
            if request_id:
                request_id = request_id.replace(key, "[REDACTED_API_KEY]")
            return HTTPResult(response.status, body, request_id)
        finally:
            connection.close()


def parse_response(raw: dict, model: str) -> dict:
    if raw.get("model") != model:
        raise C01Error("RESPONSE_MODEL_MISMATCH", "OUTPUT_PARSE", "响应模型与显式选择不一致。")
    if raw.get("status") != "completed":
        raise C01Error("MODEL_RESPONSE_INCOMPLETE", "OUTPUT_PARSE", "模型响应未完整完成，保留原始响应。")
    texts = []
    for item in raw.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise C01Error("MODEL_REFUSAL", "OUTPUT_PARSE", "模型拒绝，未生成结构化观察。")
            if part.get("type") == "output_text":
                texts.append(part["text"])
    if len(texts) != 1:
        raise C01Error("MODEL_OUTPUT_MISSING", "OUTPUT_PARSE", "需要一个明确的结构化文本输出。")
    try:
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate output key")
                result[key] = value
            return result
        result = json.loads(texts[0], object_pairs_hook=unique_pairs)
        if not isinstance(result, dict) or set(result) != set(OUTPUT_FIELDS):
            raise ValueError("six exact fields required")
        if any(not isinstance(value, list) or any(not isinstance(x, str) for x in value)
               for value in result.values()):
            raise ValueError("string arrays required")
    except (ValueError, TypeError) as exc:
        raise C01Error("MODEL_OUTPUT_INVALID", "OUTPUT_PARSE", "响应不是六字段 string 数组 JSON。") from exc
    return result


def usage_cost(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict) or raw.get("model") != MODEL:
        return None  # Do not price an unexpected model using the candidate's rates.
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return None
    inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
    if type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0:
        return None
    return {"label": "USAGE_BASED_ESTIMATE_NOT_PROVIDER_BILL", "currency": "USD",
            "usd": str((inputs * INPUT_RATE + outputs * OUTPUT_RATE) / 1_000_000),
            "source": PRICING_SOURCE, "checked_on": PRICING_CHECKED,
            "note": "按全部输入标准价估算，未扣缓存优惠；不是实际账单。"}
