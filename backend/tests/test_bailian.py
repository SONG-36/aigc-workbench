"""Offline provider protocol, safe transport and durable candidate tests. No real network or AI."""

import base64
import json
import ssl
import urllib.error
from uuid import UUID, uuid4

import pytest

from aigc_workbench import bailian_model as provider
from aigc_workbench import config
from aigc_workbench.c01_context import C01Error, canonical, fingerprint
from aigc_workbench.c01_model import HTTPResult, multimodal_request, parse_response
from aigc_workbench.c01_prompt import OUTPUT_CONTRACT, OUTPUT_FIELDS
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.c06_generation import C06Service
from aigc_workbench.c07_revision import C07Service
from aigc_workbench.quick_a_sources import ProductUnderstandingService
from test_c01_real import experiment
from test_c06 import generation, png
from test_creation_decision import source


def chat_response(result=None):
    return {"model": provider.VISION_MODEL, "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(result if result is not None else {k: [] for k in OUTPUT_FIELDS})}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}


class OfflineWire:
    label = "OFFLINE_TRANSPORT_TEST"

    def __init__(self, raw=None, status=200, error=None):
        self.calls, self.raw, self.status, self.error = 0, raw or chat_response(), status, error

    def send(self, payload):
        self.calls += 1
        self.payload = json.loads(payload)
        if self.error:
            raise self.error
        return HTTPResult(self.status, canonical(self.raw).encode(), "TEST_ONLY_REQUEST")


def test_private_credentials_coexist_without_overwriting_or_logging(tmp_path, monkeypatch):
    path = tmp_path / ".env.local"
    path.write_text("OPENAI_API_KEY=synthetic-openai\nDASHSCOPE_API_KEY=synthetic-bailian\n")
    path.chmod(0o600)
    monkeypatch.setattr(config, "BACKEND_ENV_FILE", path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-process-wins")
    original = path.read_bytes()
    assert config.load_provider_credentials() == "KEY_CONFIGURED"
    assert config.os.environ["DASHSCOPE_API_KEY"] == "synthetic-bailian"
    assert config.load_openai_credentials() == "KEY_CONFIGURED"
    assert config.os.environ["OPENAI_API_KEY"] == "synthetic-process-wins"
    assert original == path.read_bytes()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-bailian-process")
    path.unlink()
    assert config.load_provider_credentials() == "KEY_CONFIGURED"


def test_vision_wire_preserves_prompt_schema_meaning_and_order():
    req = multimodal_request("Unchanged four layers; JSON required", b"first", "image/jpeg",
        OUTPUT_CONTRACT, "c01", provider.VISION_MODEL, 4096, additional_images=((b"second", "image/png"),))
    assert req["response_format"] == {"type": "json_object"} and req["enable_thinking"] is False
    assert req["messages"][0]["content"][0]["text"] == "Unchanged four layers; JSON required"
    images = req["messages"][0]["content"][1:]
    assert [base64.b64decode(x["image_url"]["url"].split(",", 1)[1]) for x in images] == [b"first", b"second"]
    assert not {"store", "service_tier", "max_output_tokens"} & req.keys()
    assert parse_response(chat_response(), provider.VISION_MODEL) == {k: [] for k in OUTPUT_FIELDS}


@pytest.mark.parametrize("case", ["missing", "wrong_type", "duplicate", "truncated", "model", "invalid_json"])
def test_structure_failures_remain_parse_failures(case):
    raw = chat_response()
    if case == "missing": raw["choices"][0]["message"]["content"] = '{}'
    if case == "wrong_type": raw = chat_response({k: "bad" for k in OUTPUT_FIELDS})
    if case == "duplicate": raw["choices"][0]["message"]["content"] = '{"x":1,"x":2}'
    if case == "truncated": raw["choices"][0]["finish_reason"] = "length"
    if case == "model": raw["model"] = "another-model"
    if case == "invalid_json": raw["choices"][0]["message"]["content"] = "```json\n{}\n```"
    with pytest.raises(C01Error): parse_response(raw, provider.VISION_MODEL)


def test_native_cny_costs_are_not_mislabelled_usd():
    assert provider.usage_cost(chat_response())["cny"] == "0.0028"
    raw = {"usage": {"input_image_count": 2, "output_image_count": 1, "output_image_type": "qima_output_1k"}}
    assert provider.usage_cost(raw)["cny"] == "0.29"
    request = provider.image_request(provider.image_parameters({"model_generation_size": "1024x1536"}), "prompt",
                                     [{"image_url": "a"}, {"image_url": "b"}])
    assert request["image"] == ["a", "b"] and request["prompt_extend"] is False
    assert provider.quote(request)["estimated_cny"] == "0.29"
    assert not {"quality", "complexity", "images", "response_format"} & request.keys()


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}
    def read(self, limit): return self.body[:limit]
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def test_official_transport_tls_proxy_redaction_no_retries(monkeypatch):
    import urllib.request
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-user:proxy-password@127.0.0.1:8123")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "other.example")
    monkeypatch.delenv("no_proxy", raising=False)
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.full_url == provider.ORIGIN + provider.VISION_PATH
            assert request.data == b'{"model":"qwen3.7-plus"}'
            return Response(b'{"error":"synthetic-secret proxy-password"}', status=403)
    def opener(*handlers):
        assert handlers[0].proxies["https"].endswith("127.0.0.1:8123")
        assert handlers[1]._context.verify_mode == ssl.CERT_REQUIRED and handlers[1]._context.check_hostname
        assert handlers[2].redirect_request(None, None, None, None, None, None) is None
        return Opener()
    monkeypatch.setattr(urllib.request, "build_opener", opener)
    result = provider.BailianTransport(provider.VISION_MODEL).send(b'{"model":"qwen3.7-plus"}')
    assert result.status == 403 and len(calls) == 1
    assert b"synthetic-secret" not in result.body and b"proxy-password" not in result.body


def test_no_proxy_and_no_proxy_configuration_are_explicit(monkeypatch):
    for key in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
    assert provider._proxy_configuration("dashscope.aliyuncs.com") == ({}, "DIRECT_NO_PROXY_CONFIGURED")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8123")
    monkeypatch.setenv("NO_PROXY", "dashscope.aliyuncs.com")
    assert provider._proxy_configuration("dashscope.aliyuncs.com") == ({}, "DIRECT_NO_PROXY")


@pytest.mark.parametrize("timeout", [False, True])
def test_proxy_failure_never_falls_back_or_retries(monkeypatch, timeout):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret")
    calls = []
    class Opener:
        def open(self, *args, **kwargs):
            calls.append(1)
            raise urllib.error.URLError(TimeoutError() if timeout else OSError())
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    with pytest.raises(C01Error):
        provider.BailianTransport(provider.VISION_MODEL).send(canonical({"model": provider.VISION_MODEL}).encode())
    assert calls == [1]


@pytest.mark.parametrize("url", ["http://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/x",
    "https://127.0.0.1/x", "https://evil.example/x", "https://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com.evil.example/x",
    "https://user:password@dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/x"])
def test_output_url_rejects_non_provider_targets_without_network(url, monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("must reject before network"))
    with pytest.raises(C01Error): provider.download_png({"data": [{"url": url}]})


@pytest.mark.parametrize("body,valid", [(png(), True), (b"not a PNG", False)])
def test_download_real_bytes_never_forwards_api_credentials(monkeypatch, body, valid):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert not request.has_header("Authorization") and request.method == "GET"
            return Response(body)
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    raw = {"data": [{"url": "https://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/x.png?Expires=123"}]}
    if valid: assert provider.download_png(raw) == body
    else:
        with pytest.raises((C01Error, ValueError)): provider.download_png(raw)
    assert len(calls) == 1


def test_vision_runtime_uses_existing_snapshot_and_keeps_test_distinct(experiment, monkeypatch):
    old, snapshot, image = experiment
    monkeypatch.setenv("AIGC_MODEL_PROVIDER", "bailian")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-key")
    service = ProductUnderstandingService(old.settings, old.service)
    wire = OfflineWire()
    result = service.run(UUID(snapshot["id"]), provider.VISION_MODEL, uuid4(), confirmed=True,
                         max_cost_usd=provider.POLICY, transport=wire)
    assert result["status"] == "SUCCEEDED" and wire.calls == 1
    assert result["provider"] == "Alibaba Bailian" and result["region"] == "cn-beijing"
    assert result["user_max_cost_usd"] is None
    assert result["actual_model_response"] is None and result["model_api_call_attempted"] is False
    assert result["offline_test_response"] == chat_response()
    assert old.service.evidence.load_snapshot(UUID(snapshot["id"])) == snapshot


@pytest.mark.parametrize("failure", ["http", "timeout", "wrong_dimensions", "download"])
def test_image_failures_preserve_evidence_and_do_not_retry(generation, monkeypatch, failure):
    old, original, directory, _ = generation
    monkeypatch.setenv("AIGC_MODEL_PROVIDER", "bailian")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-key")
    service = C06Service(old.settings, old.service, test_mode=True)
    snapshot = service.prepare(UUID(original["decision_snapshot_id"]), directory, provider.IMAGE_MODEL)
    wire = OfflineWire(raw={"data": [{"url": "private-test-url"}]}, status=403 if failure == "http" else 200,
                       error=TimeoutError() if failure == "timeout" else None)
    def download(raw):
        if failure == "download": raise C01Error("PROVIDER_OUTPUT_DOWNLOAD_FAILED", "IMAGE_DOWNLOAD", "TEST_ONLY")
        return png((1024, 1024)) if failure == "wrong_dimensions" else png()
    monkeypatch.setattr(provider, "download_png", download)
    operation = uuid4()
    result = service.run(UUID(snapshot["id"]), provider.IMAGE_MODEL, operation, confirmed=True,
                         max_cost_usd=provider.POLICY, transport=wire)
    assert result["status"] == ("OUTCOME_UNKNOWN" if failure == "timeout" else "FAILED")
    assert "candidate" not in result
    replay = service.run(UUID(snapshot["id"]), provider.IMAGE_MODEL, operation, confirmed=True,
                         max_cost_usd=provider.POLICY, transport=wire)
    assert replay == result and wire.calls == 1


def test_two_image_generation_keeps_history_and_rejects_changed_revision_provenance(generation, monkeypatch):
    old, original, directory, sources = generation
    monkeypatch.setenv("AIGC_MODEL_PROVIDER", "bailian")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-key")
    monkeypatch.setenv("AIGC_ENABLE_C07_REAL", "1")
    service = C06Service(old.settings, old.service, test_mode=True)
    snapshot = service.prepare(UUID(original["decision_snapshot_id"]), directory, provider.IMAGE_MODEL)
    monkeypatch.setattr(provider, "download_png", lambda raw: png())
    wire = OfflineWire(raw={"data": [{"url": "TEST_ONLY_URL"}]})
    op = uuid4()
    result = service.run(UUID(snapshot["id"]), provider.IMAGE_MODEL, op, confirmed=True,
                         max_cost_usd=provider.POLICY, transport=wire)
    assert result["status"] == "SUCCEEDED" and result["actual_model_response"] is None
    assert [base64.b64decode(x.split(",", 1)[1]) for x in wire.payload["image"]] == sources["blobs"]
    candidate = result["candidate"]
    assert candidate["execution_mode"] == "TEST_ONLY" and candidate["pass"] is False
    assert candidate["image"]["width"] == 1024 and candidate["image"]["height"] == 1536
    assert result["downloaded_output"]["source"].endswith("TEST_ONLY")
    assert result["provider"] == "Alibaba Bailian" and result["planned_model_input"] == wire.payload
    assert result["endpoint"].endswith("/images/generations")
    before = fingerprint(candidate)
    # Supply complete existing handoff constraints for this synthetic source fixture.
    # Revision tests below use the existing C07 _sources implementation, not another data model.
    handoff = directory / "05_C06_EXECUTION_HANDOFF.json"
    payload = json.loads(handoff.read_text())
    from test_creation_decision import decision_output
    payload["image_creation_decision"] = decision_output()["02_IMAGE_CREATION_DECISION"]
    # Immutable provenance prevents mutating the handoff after generation, as intended.
    handoff.write_text(json.dumps(payload))
    revision = C07Service(old.settings, old.service, test_mode=True)
    with pytest.raises(C01Error) as exc:
        revision.prepare(sources["task"], sources["slot"], UUID(candidate["image_version_id"]), "title only")
    assert exc.value.record()["code"] == "REVISION_PROVENANCE_CHANGED"
    assert fingerprint(CandidateStore(old.store.database, old.settings.data_dir).inspect(UUID(candidate["image_version_id"]))) == before
    assert wire.calls == 1


def test_real_revision_protocol_keeps_three_input_order_and_selected_base(generation, monkeypatch):
    old, original, directory, sources = generation
    # Populate the same complete existing handoff shape used by original C07 tests BEFORE freezing it.
    from hashlib import sha256
    from test_creation_decision import decision_output
    handoff = directory / "05_C06_EXECUTION_HANDOFF.json"
    payload = json.loads(handoff.read_text())
    decision = decision_output()["02_IMAGE_CREATION_DECISION"]
    decision["C06_creation_input"] = payload["image_creation_decision"]["C06_creation_input"]
    payload["image_creation_decision"] = decision
    handoff.write_text(json.dumps(payload))
    manifest = directory / "delivery-manifest-final.json"
    hashes = json.loads(manifest.read_text())
    hashes[handoff.name]["sha256"] = sha256(handoff.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(hashes))
    monkeypatch.setenv("AIGC_MODEL_PROVIDER", "bailian")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-key")
    monkeypatch.setenv("AIGC_ENABLE_C07_REAL", "1")
    monkeypatch.setattr(provider, "download_png", lambda raw: png())
    generator = C06Service(old.settings, old.service, test_mode=True)
    snapshot = generator.prepare(UUID(original["decision_snapshot_id"]), directory, provider.IMAGE_MODEL)
    wire = OfflineWire(raw={"data": [{"url": "TEST_ONLY_URL"}]})
    first = generator.run(UUID(snapshot["id"]), provider.IMAGE_MODEL, uuid4(), confirmed=True,
                          max_cost_usd=provider.POLICY, transport=wire)
    base = first["candidate"]
    revision = C07Service(old.settings, old.service, test_mode=True)
    snapshot = revision.prepare(sources["task"], sources["slot"], UUID(base["image_version_id"]), "title dark blue only")
    assert len(snapshot["prompt"]["layers"]) == 4
    wire = OfflineWire(raw={"data": [{"url": "TEST_ONLY_URL"}]})
    op = uuid4()
    result = revision.run(UUID(snapshot["id"]), provider.IMAGE_MODEL, op, confirmed=True,
                          max_cost_usd=provider.POLICY, transport=wire)
    assert result["status"] == "SUCCEEDED"
    assert result["candidate"]["base_image_version_id"] == base["image_version_id"]
    assert result["candidate"]["image_version_id"] != base["image_version_id"]
    assert result["candidate"]["pass"] is False and result["candidate"]["execution_mode"] == "TEST_ONLY"
    assert [base64.b64decode(x.split(",", 1)[1]) for x in wire.payload["image"]] == [png(), *sources["blobs"]]
    assert revision.run(UUID(snapshot["id"]), provider.IMAGE_MODEL, op, confirmed=True,
                        max_cost_usd=provider.POLICY, transport=wire) == result
    assert wire.calls == 1
    assert CandidateStore(old.store.database, old.settings.data_dir).inspect(UUID(base["image_version_id"])) == base


def test_c02_and_combined_decision_strict_parser_accept_native_chat_protocol(source):
    from aigc_workbench.c02_reference import C02RealService, OUTPUT_CONTRACT as reference_schema
    from aigc_workbench.quick_a_creation_decision import QuickACreationDecisionService
    from test_creation_decision import shape, decision_output
    service = C02RealService(source["real"].settings, source["real"].service)
    expected = shape(reference_schema)
    assert service.parse_output(chat_response(expected), provider.VISION_MODEL) == expected
    decision = QuickACreationDecisionService(source["real"].settings, source["real"].service)
    expected = decision_output()
    assert decision.parse_output(chat_response(expected), provider.VISION_MODEL) == expected
    expected["02_IMAGE_CREATION_DECISION"]["C04_MARKETING_CONTENT"]["claim_decisions"].pop()
    with pytest.raises(C01Error): decision.parse_output(chat_response(expected), provider.VISION_MODEL)


def test_current_slot_c01_c02_combined_decision_without_historical_answers(source, monkeypatch):
    from aigc_workbench.c02_reference import C02RealService, OUTPUT_CONTRACT as reference_schema
    from aigc_workbench.quick_a_creation_decision import QuickACreationDecisionService
    from aigc_workbench.quick_a_decision_prompt import contract
    from test_creation_decision import shape
    monkeypatch.setenv("AIGC_MODEL_PROVIDER", "bailian")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-key")
    old = source["real"]
    product = ProductUnderstandingService(old.settings, old.service)
    snapshot = old.service.prepare(source["task"], source["assets"][0].id)
    c01 = product.run(UUID(snapshot["id"]), provider.VISION_MODEL, uuid4(), confirmed=True,
                      max_cost_usd=provider.POLICY, transport=OfflineWire())
    reference = C02RealService(old.settings, old.service)
    snapshot = reference.prepare(source["task"], source["slot"], source["assets"][1].id)
    c02 = reference.run(UUID(snapshot["id"]), provider.VISION_MODEL, uuid4(), confirmed=True,
                        max_cost_usd=provider.POLICY, transport=OfflineWire(chat_response(shape(reference_schema))))
    service = QuickACreationDecisionService(old.settings, old.service, workspace=True, test_mode=True)
    snapshot = service.prepare(source["task"], source["slot"], *[a.id for a in source["assets"]],
                               UUID(c01["run_id"]), UUID(c02["run_id"]))
    wire = OfflineWire(chat_response(shape(contract(snapshot["context"]))))
    result = service.run(UUID(snapshot["id"]), provider.VISION_MODEL, uuid4(), confirmed=True,
                         max_cost_usd=provider.POLICY, max_output_tokens=8192, transport=wire)
    assert result["status"] == "SUCCEEDED" and result["actual_model_input"] is None
    assert len(wire.payload["messages"][0]["content"]) == 3
    assert wire.payload["messages"][0]["content"][0]["text"] == snapshot["prompt"]["planned_model_input"]["text"]
    assert snapshot["context"]["operator_confirmed_information"] == []
    assert snapshot["context"]["preliminary_quality_reviews"] == []
    assert result["structured_output"]["label"] == "OFFLINE_TEST_ONLY"
