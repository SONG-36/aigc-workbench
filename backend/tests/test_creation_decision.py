"""Two-image decision boundaries using temporary files and explicit synthetic upstream fixtures."""

import base64
from hashlib import sha256
from io import BytesIO
import json
from math import ceil
import socket
from uuid import UUID, uuid4

from PIL import Image
import pytest

from aigc_workbench.application import TaskService
from aigc_workbench.c01_context import C01Error, fingerprint, now
from aigc_workbench.c01_execution import C01Service
from aigc_workbench.c01_model import HTTPResult, MODEL
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.c01_real import preflight_report
from aigc_workbench.c02_reference import C02RealService
from aigc_workbench.config import Settings
from aigc_workbench.domain import AssetRole, SlotKind
from aigc_workbench.file_storage import LocalImageFiles
from aigc_workbench.persistence import SQLiteTaskStore
from aigc_workbench.quick_a_creation_decision import QuickACreationDecisionService, OUTPUT_CONTRACT


def shape(schema):
    if schema["type"] == "string": return schema.get("enum", ["SYNTHETIC_TEST_ONLY"])[0]
    if schema["type"] == "array": return []
    return {key: shape(child) for key, child in schema["properties"].items()}


def decision_output():
    result = shape(OUTPUT_CONTRACT)
    claims = result["02_IMAGE_CREATION_DECISION"]["C04_MARKETING_CONTENT"]["claim_decisions"]
    for identity in ("mc1", "mc2", "mc3", "mc4"):
        claims.append({"claim_id": identity, "adoption": "ADOPT", "reason": "SYNTHETIC_TEST_ONLY",
                       "proposed_copy": "SYNTHETIC_TEST_ONLY", "source_basis": ["C02_RESULT"]})
    return result


class OfflineTransport:
    label = "OFFLINE_TRANSPORT_TEST"
    def __init__(self, result=None, exception=None): self.calls, self.result, self.exception = 0, result, exception
    def send(self, payload):
        self.calls += 1
        if self.exception: raise self.exception
        return HTTPResult(200, json.dumps({"model": MODEL, "status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(
                self.result if self.result is not None else decision_output())}]}]}).encode(), "offline-decision")


@pytest.fixture
def source(tmp_path, monkeypatch):
    def forbidden(*a, **k): raise AssertionError("network forbidden in decision tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-offline-credential")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    settings = Settings(tmp_path / "runtime", enable_c01_testing=True, enable_c01_real=True)
    database = SQLiteTaskStore(settings.database_path); database.initialize()
    files = LocalImageFiles(settings.data_dir); files.initialize(); tasks = TaskService(database, files)
    task = tasks.create_draft("synthetic task", uuid4()); assets, blobs = [], []
    for role, color, fmt, mime in [(AssetRole.PRODUCT_IMAGE, "red", "JPEG", "image/jpeg"),
                                   (AssetRole.MARKETING_REFERENCE, "blue", "PNG", "image/png")]:
        stream = BytesIO(); Image.new("RGB", (20, 20), color).save(stream, fmt); data = stream.getvalue()
        assets.append(tasks.upload_asset(task.id, uuid4(), role, "synthetic", data, mime)); blobs.append(data)
    slot = tasks.create_slot(task.id, uuid4(), SlotKind.MAIN)
    tasks.configure_slot(task.id, slot.id, assets[1].id, "1024x1536", "PERSISTED_OPERATOR_REQUIREMENT_CANARY")
    base = C01Service(settings, tasks, C01EvidenceStore(database, settings.data_dir))
    c01 = base.prepare(task.id, assets[0].id)
    c02 = C02RealService(settings, base).prepare(task.id, slot.id, assets[1].id)
    real = QuickACreationDecisionService(settings, base); upstream = []
    # Counterfactual completed-provider records are local unit fixtures only, never real runtime Evidence.
    for snapshot in (c01, c02):
        request = {"model": MODEL, "synthetic_upstream_fixture": snapshot["capability"]}
        record, _ = real.store.start(snapshot, uuid4(), request, {}, "0.05", "REAL_PROVIDER_TRANSPORT")
        record.update(status="SUCCEEDED", attempt_status="SUCCEEDED", finished_at=now(),
                      model_api_call_attempted=True, structured_output={"label": "SYNTHETIC_UPSTREAM_FIXTURE_ONLY",
                          "content": {"synthetic_upstream": snapshot["capability"]}})
        real.store.save(record); upstream.append(record)
    reviews = [tmp_path / "c01-review.json", tmp_path / "c02-review.json"]
    reviews[0].write_text(json.dumps({"c01_run_id": upstream[0]["run_id"],
                                    "c01_evidence_sha256": fingerprint(upstream[0]), "status": "PRELIMINARY"}))
    reviews[1].write_text(json.dumps({"source_image_sha256": assets[1].image.sha256, "status": "PRELIMINARY"}))
    return {"real": real, "task": task.id, "slot": slot.id, "assets": assets, "blobs": blobs,
            "runs": upstream, "reviews": reviews}


def prepare(source):
    return source["real"].prepare(source["task"], source["slot"], source["assets"][0].id, source["assets"][1].id,
        UUID(source["runs"][0]["run_id"]), UUID(source["runs"][1]["run_id"]), *source["reviews"])


def execute(source, snapshot, adapter, operation=None, budget="0.05"):
    return source["real"].run(UUID(snapshot["id"]), MODEL, operation or uuid4(), confirmed=True,
        max_cost_usd=budget, max_output_tokens=8192, transport=adapter)


def test_two_real_byte_inputs_order_requirements_and_both_image_costs(source):
    snapshot = prepare(source); preflight = source["real"].preflight(UUID(snapshot["id"]), MODEL)
    parts = preflight["request"]["input"][0]["content"]
    assert len(parts) == 3
    for index, image in enumerate(source["blobs"]):
        assert base64.b64decode(parts[index + 1]["image_url"].split(",", 1)[1]) == image
    assert "PERSISTED_OPERATOR_REQUIREMENT_CANARY" in parts[0]["text"]
    assert snapshot["context"]["slot_state"]["output_size"] == "1024x1536"
    assert snapshot["context"]["upstream_results"][0]["status"].startswith("AI_DERIVED")
    expected = sum(ceil(ceil(a.image.width / 32) * ceil(a.image.height / 32) * 1.62) for a in source["assets"])
    assert preflight["cost"]["unresized_image_token_envelope"] == expected
    assert preflight["request"]["store"] is False
    report = preflight_report(preflight)
    assert all("<omitted" in part["image_url"] for part in report["request"]["input"][0]["content"][1:])


def test_upstream_records_unchanged_and_same_operation_never_reruns(source):
    snapshot = prepare(source); adapter = OfflineTransport(); operation = uuid4()
    result = execute(source, snapshot, adapter, operation)
    assert result["status"] == "SUCCEEDED" and result["capability"] == "C03_C04_C05"
    assert len(result["image_sources"]) == 2 and result["product_asset_id"] == str(source["assets"][0].id)
    assert execute(source, snapshot, adapter, operation) == result and adapter.calls == 1
    for old in source["runs"]: assert source["real"].store.inspect(UUID(old["run_id"])) == old


@pytest.mark.parametrize("change", ["requirements", "size", "reference"])
def test_changed_slot_state_stops_before_execution(source, change):
    snapshot = prepare(source)
    source["real"].service.tasks.configure_slot(source["task"], source["slot"],
        None if change == "reference" else source["assets"][1].id,
        "1024x1024" if change == "size" else "1024x1536",
        "NEW_REQUIREMENT" if change == "requirements" else "PERSISTED_OPERATOR_REQUIREMENT_CANARY")
    adapter = OfflineTransport()
    with pytest.raises(C01Error): execute(source, snapshot, adapter)
    assert adapter.calls == 0


def test_wrong_review_does_not_create_decision_snapshot(source):
    source["reviews"][0].write_text('{}')
    with pytest.raises(C01Error) as exc: prepare(source)
    assert exc.value.code == "DECISION_REVIEW_MISMATCH"


def test_timeout_is_unknown_and_not_replayed(source):
    snapshot = prepare(source); adapter = OfflineTransport(exception=TimeoutError("offline")); operation = uuid4()
    result = execute(source, snapshot, adapter, operation)
    assert result["status"] == "OUTCOME_UNKNOWN"
    assert execute(source, snapshot, adapter, operation) == result and adapter.calls == 1


def test_all_four_claim_decisions_required_and_raw_response_retained(source):
    snapshot = prepare(source); output = decision_output()
    output["02_IMAGE_CREATION_DECISION"]["C04_MARKETING_CONTENT"]["claim_decisions"].pop()
    result = execute(source, snapshot, OfflineTransport(result=output))
    assert result["status"] == "FAILED" and result["error"]["code"] == "DECISION_CLAIM_COVERAGE_INVALID"
    assert result["raw_response_text"]


def test_budget_rejection_before_dispatch(source):
    snapshot = prepare(source); adapter = OfflineTransport()
    with pytest.raises(C01Error) as exc: execute(source, snapshot, adapter, budget="0.000001")
    assert exc.value.code == "BUDGET_TOO_LOW" and adapter.calls == 0
