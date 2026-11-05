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
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

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
    return multimodal_request(snapshot["prompt"]["planned_model_input"]["text"], image,
                              "image/jpeg", schema, "c01_visual_observation", model, max_output_tokens)


def multimodal_request(text: str, image: bytes, media_type: str, schema: dict, schema_name: str,
                       model: str, max_output_tokens: int, *,
                       additional_images: tuple[tuple[bytes, str], ...] = (), output_ceiling: int = 4096) -> dict:
    """Shared serializer for the two explicit capabilities; no routing or Prompt registry."""
    if (model != MODEL or type(max_output_tokens) is not int or output_ceiling not in {4096, 8192}
            or not 256 <= max_output_tokens <= output_ceiling or len(additional_images) > 1):
        raise C01Error("MODEL_PARAMETERS_INVALID", "MODEL_PREFLIGHT", "模型或输出上限不受支持。")
    if media_type not in {"image/jpeg", "image/png", "image/webp"} or not text or not image:
        raise C01Error("MODEL_INPUT_INVALID", "MODEL_PREFLIGHT", "需要明确文字和已校验的实际图片。")
    images = ((image, media_type),) + additional_images
    if any(not data or mime not in {"image/jpeg", "image/png", "image/webp"} for data, mime in images):
        raise C01Error("MODEL_INPUT_INVALID", "MODEL_PREFLIGHT", "补充图片输入无效。")
    return {
        "model": model, "store": False, "stream": False, "service_tier": "default",
        "temperature": 0, "max_output_tokens": max_output_tokens,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": text},
            *[{"type": "input_image", "detail": "high",
               "image_url": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")}
              for data, mime in images],
        ]}],
        "text": {"format": {"type": "json_schema", "name": schema_name,
                            "strict": True, "schema": schema}},
    }


def estimate_cost(snapshot: dict, request: dict) -> dict:
    """Conservative planning envelope, NOT a provider-enforced billing cap or exact tokenizer."""
    source = snapshot["context"]["capability_specific_inputs"]
    # Use unresized patches: intentionally overestimates when the provider downsizes.
    image_tokens = sum(ceil(ceil(item["width"] / 32) * ceil(item["height"] / 32) * 1.62)
                       for item in snapshot["context"].get("image_inputs", [source]))
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Even same-origin redirects must not replay a paid POST.


def _proxy_configuration() -> tuple[dict, str]:
    """Explicit environment policy; no system-proxy discovery or failure-to-direct fallback."""
    proxies = urllib.request.getproxies_environment()
    if urllib.request.proxy_bypass_environment("api.openai.com:443", proxies):
        return {}, "DIRECT_NO_PROXY"
    selected = proxies.get("https") or proxies.get("all")
    if not selected:
        return {}, "DIRECT_NO_PROXY_CONFIGURED"
    try:
        parsed = urllib.parse.urlsplit(selected)
        if parsed.scheme != "http":
            raise C01Error("MODEL_PROXY_SCHEME_UNSUPPORTED", "MODEL_TRANSPORT",
                           "标准库入口只支持 HTTP CONNECT 代理；其他代理协议不转为直连。")
        if (not parsed.hostname or parsed.port == 0 or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or any(char.isspace() for char in selected)):
            raise ValueError()
        # urllib applies Basic proxy authentication only when both are present.
        if (parsed.username is None) != (parsed.password is None):
            raise ValueError()
    except C01Error:
        raise
    except ValueError:
        raise C01Error("MODEL_PROXY_INVALID", "MODEL_TRANSPORT", "代理配置格式无效；未转为直连。") from None
    return {"https": selected}, "HTTPS_PROXY" if proxies.get("https") else "ALL_PROXY"


def _redact_response(value: bytes, key: str, proxies: dict) -> bytes:
    secrets = [(key, b"[REDACTED_API_KEY]")]
    for proxy in proxies.values():
        parsed = urllib.parse.urlsplit(proxy)
        if parsed.username is not None and parsed.password is not None:
            user, password = urllib.parse.unquote(parsed.username), urllib.parse.unquote(parsed.password)
            secrets.extend((secret, b"[REDACTED_PROXY_CREDENTIAL]") for secret in
                           [proxy, user + ":" + password, user, password,
                            base64.b64encode((user + ":" + password).encode()).decode()])
    for secret, marker in sorted(set(secrets), key=lambda entry: len(entry[0]), reverse=True):
        if secret:
            for representation in {secret, json.dumps(secret, ensure_ascii=False)[1:-1],
                                   json.dumps(secret, ensure_ascii=True)[1:-1]}:
                value = value.replace(representation.encode(), marker)
    return value


class OpenAIResponsesTransport:
    """One proxy-aware HTTPS operation, fixed origin, verified TLS, no redirects or retries."""
    label = "REAL_PROVIDER_TRANSPORT"

    def send(self, payload: bytes) -> HTTPResult:
        result, _ = self._request("POST", "/v1/responses", payload)
        return result

    def _request(self, method: str, path: str, payload: bytes | None, *,
                 timeout: int = TIMEOUT_SECONDS, max_response_bytes: int = MAX_RESPONSE_BYTES) -> tuple[HTTPResult, str]:
        if (method, path) not in {("POST", "/v1/responses"), ("POST", "/v1/images/edits"),
                                  ("GET", "/v1/models/" + MODEL)}:
            raise C01Error("MODEL_TARGET_NOT_ALLOWED", "MODEL_TRANSPORT", "仅允许固定官方 API 目标。")
        if os.environ.get("OPENAI_BASE_URL"):
            raise C01Error("CUSTOM_ENDPOINT_NOT_SUPPORTED", "MODEL_TRANSPORT", "仅支持官方固定 API 目标。")
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key or "\r" in key or "\n" in key:
            raise C01Error("MODEL_CREDENTIAL_INVALID", "MODEL_TRANSPORT", "凭证缺失或格式无效。")
        proxies, route = _proxy_configuration()
        response = None
        try:
            context = ssl.create_default_context()  # CERT_REQUIRED + hostname verification.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies),
                                                 urllib.request.HTTPSHandler(context=context), _NoRedirect())
            headers = {"Authorization": "Bearer " + key}
            if method == "POST":
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request("https://api.openai.com" + path, data=payload,
                                             headers=headers, method=method)
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc  # Preserve actual error status/body for existing Run/Evidence handling.
            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise C01Error("RESPONSE_TOO_LARGE", "MODEL_TRANSPORT", "响应超过本实验读取上限；结果不完整。")
            body = _redact_response(body, key, proxies)
            request_id = response.headers.get("x-request-id")
            if request_id:
                request_id = _redact_response(request_id.encode(), key, proxies).decode()
            return HTTPResult(response.status, body, request_id), route
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                code, message = "MODEL_NETWORK_TIMEOUT", "连接或读取超时；远端执行状态未知，不自动重试。"
            elif isinstance(reason, ssl.SSLError):
                code, message = "MODEL_TLS_FAILED", "TLS 验证或握手失败；未关闭验证，不自动重试。"
            elif proxies:
                code, message = "MODEL_PROXY_CONNECTION_FAILED", "代理连接或读取失败；未转为直连，不自动重试。"
            else:
                code, message = "MODEL_NETWORK_FAILED", "直接网络连接或读取失败；不自动重试。"
            raise C01Error(code, "MODEL_TRANSPORT", message) from None
        finally:
            if response is not None:
                response.close()

    def check_model_access(self, model: str) -> dict:
        """One free metadata GET, using exactly the same network path as send()."""
        if model != MODEL:
            raise C01Error("MODEL_NOT_SUPPORTED", "MODEL_TRANSPORT", "不自动替换实验模型。")
        started = time.monotonic()
        report = {"model": model, "operation": "MODEL_METADATA_GET", "inference_calls": 0,
                  "automatic_retries": 0, "http_status": None}
        try:
            response, route = self._request("GET", "/v1/models/" + model, None)
            report.update(http_status=response.status, network_route=route, request_id=response.request_id)
            try:
                body = json.loads(response.body)
            except ValueError:
                body = None
            accessible = response.status == 200 and isinstance(body, dict) and body.get("id") == model
            report["classification"] = "MODEL_METADATA_ACCESSIBLE" if accessible else "MODEL_ACCESS_NOT_CONFIRMED"
            if not accessible:
                report["error"] = {"code": "MODEL_METADATA_HTTP_ERROR", "stage": "MODEL_TRANSPORT",
                                   "message": "已收到 HTTP 响应，未确认指定模型可访问；没有重试。"}
        except C01Error as exc:
            report["classification"] = "MODEL_ACCESS_NOT_CONFIRMED"
            report["error"] = exc.record()
        report["elapsed_s"] = round(time.monotonic() - started, 4)
        return report


def response_object(raw: dict, model: str) -> dict:
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
        if not isinstance(result, dict):
            raise ValueError("object required")
    except (ValueError, TypeError) as exc:
        raise C01Error("MODEL_OUTPUT_INVALID", "OUTPUT_PARSE", "响应不是有效的唯一键 JSON 对象。") from exc
    return result


def parse_response(raw: dict, model: str) -> dict:
    result = response_object(raw, model)
    if (set(result) != set(OUTPUT_FIELDS)
            or any(not isinstance(value, list) or any(not isinstance(x, str) for x in value)
                   for value in result.values())):
        raise C01Error("MODEL_OUTPUT_INVALID", "OUTPUT_PARSE", "响应不是六字段 string 数组 JSON。")
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
