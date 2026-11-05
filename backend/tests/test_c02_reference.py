"""C02 binding/input isolation and existing one-shot runtime regression; no external network."""

import base64
from hashlib import sha256
from io import BytesIO
import json
import socket
from uuid import UUID, uuid4

from PIL import Image
import pytest

from aigc_workbench.application import StorageUnavailable, TaskService
from aigc_workbench.c01_context import C01Error
from aigc_workbench.c01_execution import C01Service
from aigc_workbench.c01_model import HTTPResult, MODEL
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.c01_real import preflight_report
from aigc_workbench.c02_reference import C02RealService, OUTPUT_CONTRACT
from aigc_workbench.config import Settings
from aigc_workbench.domain import AssetRole, SlotKind
from aigc_workbench.file_storage import LocalImageFiles
from aigc_workbench.persistence import SQLiteTaskStore


@pytest.fixture
def source(tmp_path, monkeypatch):
    def forbidden(*a, **k): raise AssertionError("network forbidden in C02 unit tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-synthetic-credential")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    settings = Settings(tmp_path / "runtime", enable_c01_testing=True, enable_c01_real=True)
    db = SQLiteTaskStore(settings.database_path); db.initialize()
    files = LocalImageFiles(settings.data_dir); files.initialize()
    tasks = TaskService(db, files)
    task = tasks.create_draft("PRODUCT_FACT_CANARY_NOT_REFERENCE_INPUT", uuid4())
    assets, blobs = [], []
    for role, color in [(AssetRole.PRODUCT_IMAGE, "red"), (AssetRole.MARKETING_REFERENCE, "blue")]:
        stream = BytesIO(); Image.new("RGB", (20, 20), color).save(stream, "PNG")
        blob = stream.getvalue(); blobs.append(blob)
        assets.append(tasks.upload_asset(task.id, uuid4(), role, "FILENAME_CANARY.png", blob, "image/png"))
    slot = tasks.create_slot(task.id, uuid4(), SlotKind.MAIN)
    tasks.configure_slot(task.id, slot.id, assets[1].id, "1024x1024", "REQUIREMENTS_CANARY_NOT_REFERENCE_INPUT")
    old = C01Service(settings, tasks, C01EvidenceStore(db, settings.data_dir))
    c01_snapshot = old.prepare(task.id, assets[0].id)
    return {"real": C02RealService(settings, old), "task": task.id, "slot": slot.id,
            "product": assets[0].id, "reference": assets[1].id, "blobs": blobs, "c01": c01_snapshot}


def prepare(source):
    return source["real"].prepare(source["task"], source["slot"], source["reference"])


def empty_result(schema):
    if schema["type"] == "string": return "offline test only"
    if schema["type"] == "array": return []
    return {key: empty_result(value) for key, value in schema["properties"].items()}


class OfflineResponse:
    label = "OFFLINE_TRANSPORT_TEST"
    def __init__(self, result=None, exception=None):
        self.calls, self.result, self.exception = 0, result, exception
    def execute_response(self):
        return {"model": MODEL, "status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(self.result if self.result is not None
                                                       else empty_result(OUTPUT_CONTRACT))}]}]}
    def send(self, payload):
        self.calls += 1
        if self.exception: raise self.exception
        return HTTPResult(200, json.dumps(self.execute_response()).encode(), "offline-c02")


def test_reference_only_png_four_layers_and_historical_c01_unchanged(source):
    real = source["real"]; snapshot = prepare(source)
    p = real.preflight(UUID(snapshot["id"]), MODEL)
    assert snapshot["capability"] == "C02" and "product_asset_id" not in snapshot
    assert snapshot["slot_id"] == str(source["slot"]) and snapshot["reference_asset_id"] == str(source["reference"])
    assert snapshot["context"]["operator_confirmed_information"] == []
    assert len(snapshot["prompt"]["layers"]) == 4
    parts = p["request"]["input"][0]["content"]
    assert len(parts) == 2 and parts[1]["image_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(parts[1]["image_url"].split(",", 1)[1]) == source["blobs"][1]
    assert sha256(source["blobs"][0]).hexdigest() not in parts[0]["text"]
    assert not any(canary in parts[0]["text"] for canary in ["PRODUCT_FACT_CANARY", "FILENAME_CANARY", "REQUIREMENTS_CANARY"])
    assert p["request"]["store"] is False and p["network_requests"] == 0
    assert preflight_report(p)["request"]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,<omitted")
    assert real.service.evidence.load_snapshot(UUID(source["c01"]["id"])) == source["c01"]


@pytest.mark.parametrize("wrong", ["slot", "binding", "role", "file"])
def test_wrong_binding_role_and_missing_file_stop_before_snapshot(source, wrong):
    real = source["real"]
    if wrong == "slot": source["slot"] = uuid4()
    elif wrong == "binding": source["reference"] = source["product"]
    elif wrong == "role":
        with real.service.evidence.database.connection(write=True) as db:
            db.execute("UPDATE assets SET role='product_image' WHERE id=?", (str(source["reference"]),))
    else: (real.service.tasks.files.assets / source["reference"].hex).unlink()
    with pytest.raises((C01Error, StorageUnavailable)): prepare(source)
    with real.service.evidence.database.connection() as db:
        assert db.execute("SELECT count(*) FROM c01_snapshots").fetchone()[0] == 1


def test_current_binding_change_invalidates_execution_before_adapter(source):
    real = source["real"]; snapshot = prepare(source)
    real.service.tasks.configure_slot(source["task"], source["slot"], None, "1024x1024", "")
    adapter = OfflineResponse()
    with pytest.raises(C01Error) as exc:
        real.run(UUID(snapshot["id"]), MODEL, uuid4(), confirmed=True, max_cost_usd="0.02", transport=adapter)
    assert exc.value.code == "C02_BINDING_MISMATCH" and adapter.calls == 0


def test_c02_uses_existing_run_evidence_and_operation_replay(source):
    real = source["real"]; snapshot = prepare(source); operation = uuid4(); adapter = OfflineResponse()
    result = real.run(UUID(snapshot["id"]), MODEL, operation, confirmed=True, max_cost_usd="0.02", transport=adapter)
    assert result["label"] == "C02 MODEL EXECUTION" and result["status"] == "SUCCEEDED"
    assert result["task_id"] == str(source["task"]) and result["slot_id"] == str(source["slot"])
    assert result["reference_asset_id"] == str(source["reference"])
    assert result["structured_output"]["label"] == "OFFLINE_TEST_ONLY"
    assert result["actual_model_response"] is None and result["model_api_call_attempted"] is False
    assert real.run(UUID(snapshot["id"]), MODEL, operation, confirmed=True,
                    max_cost_usd="0.02", transport=adapter) == result
    assert adapter.calls == 1


def test_c02_timeout_retains_unknown_no_second_call(source):
    real = source["real"]; snapshot = prepare(source); operation = uuid4()
    adapter = OfflineResponse(exception=TimeoutError("offline timeout"))
    result = real.run(UUID(snapshot["id"]), MODEL, operation, confirmed=True, max_cost_usd="0.02", transport=adapter)
    assert result["status"] == "OUTCOME_UNKNOWN"
    assert real.run(UUID(snapshot["id"]), MODEL, operation, confirmed=True,
                    max_cost_usd="0.02", transport=adapter) == result and adapter.calls == 1


@pytest.mark.parametrize("damage", ["missing", "nested", "extra"])
def test_c02_output_contract_failures_are_recorded(source, damage):
    real = source["real"]; snapshot = prepare(source); output = empty_result(OUTPUT_CONTRACT)
    if damage == "missing": output.pop("visual_relationships")
    elif damage == "nested": output["visible_text"] = [{"id": "T1", "text": 42, "location": "top"}]
    else: output["invented_fact"] = "not allowed"
    result = real.run(UUID(snapshot["id"]), MODEL, uuid4(), confirmed=True, max_cost_usd="0.02",
                      transport=OfflineResponse(result=output))
    assert result["status"] == "FAILED" and result["error"]["code"] == "C02_OUTPUT_INVALID"
    assert result["raw_response_text"]


def test_c02_low_budget_never_invokes_adapter(source):
    real = source["real"]; snapshot = prepare(source); adapter = OfflineResponse()
    with pytest.raises(C01Error) as exc:
        real.run(UUID(snapshot["id"]), MODEL, uuid4(), confirmed=True, max_cost_usd="0.000001", transport=adapter)
    assert exc.value.code == "BUDGET_TOO_LOW" and adapter.calls == 0
