"""Offline S03 checks. Synthetic JPEG/SQLite and explicit fake HTTP responses, never paid calls."""

import base64
from dataclasses import replace
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import socket
import sqlite3
from uuid import UUID, uuid4

from PIL import Image
import pytest

from aigc_workbench.application import TaskService
from aigc_workbench.c01_cli import main
from aigc_workbench.c01_context import C01Error, canonical, fingerprint
from aigc_workbench.c01_execution import C01Service
from aigc_workbench.c01_model import (HTTPResult, MODEL, OpenAIResponsesTransport, OUTPUT_FIELDS,
                                      authorize, usage_cost)
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.c01_real import C01RealService, preflight_report
from aigc_workbench.config import Settings
from aigc_workbench.domain import AssetRole
from aigc_workbench.file_storage import LocalImageFiles
from aigc_workbench.persistence import SQLiteTaskStore


def fake_response():
    return {"id": "offline-response", "model": MODEL, "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(
                {key: [] for key in OUTPUT_FIELDS})}]}],
            "usage": {"input_tokens": 10, "output_tokens": 20}}


class OfflineTransport:
    label = "OFFLINE_TRANSPORT_TEST"

    def __init__(self, body=None, status=200, exception=None):
        self.calls, self.payloads = 0, []
        self.body, self.status, self.exception = body, status, exception

    def send(self, payload):
        self.calls += 1; self.payloads.append(payload)
        if self.exception:
            raise self.exception
        return HTTPResult(self.status, self.body if self.body is not None
                          else canonical(fake_response()).encode(), "offline-request")


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    # No test can reach a network, even if an accidental real transport is selected.
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in S03 offline tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-dummy-not-a-credential")
    monkeypatch.setattr("aigc_workbench.config.BACKEND_ENV_FILE", tmp_path / "missing-private-config")
    monkeypatch.setattr("aigc_workbench.c01_model.PRICING_CHECKED", date.today().isoformat())
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    settings = Settings(tmp_path / "runtime", enable_c01_testing=True, enable_c01_real=True)
    database = SQLiteTaskStore(settings.database_path); database.initialize()
    files = LocalImageFiles(settings.data_dir); files.initialize()
    tasks = TaskService(database, files)
    task = tasks.create_draft("no visual answer in task title", uuid4())
    stream = BytesIO(); Image.new("RGB", (80, 60), "red").save(stream, "JPEG")
    image = stream.getvalue()
    asset = tasks.upload_asset(task.id, uuid4(), AssetRole.PRODUCT_IMAGE,
                               "not-a-product-claim.jpg", image, "image/jpeg")
    service = C01Service(settings, tasks, C01EvidenceStore(database, settings.data_dir))
    snapshot = service.prepare(task.id, asset.id)
    # Synthetic immutable experiment identity, not the private production source or an answer.
    for key, value in {"SNAPSHOT_ID": snapshot["id"], "ASSET_ID": str(asset.id),
                       "SNAPSHOT_SHA256": fingerprint(snapshot), "IMAGE_SHA256": sha256(image).hexdigest()}.items():
        monkeypatch.setattr("aigc_workbench.c01_real." + key, value)
    real = C01RealService(settings, service)
    return real, snapshot, image


def run(real, snapshot, adapter=None, operation=None, **options):
    return real.run(UUID(snapshot["id"]), MODEL, operation or uuid4(), confirmed=True,
                    max_cost_usd=options.pop("max_cost_usd", "0.02"), transport=adapter or OfflineTransport(), **options)


def all_original_rows(real):
    with real.store.database.connection() as db:
        return {name: [dict(r) for r in db.execute("SELECT * FROM " + name + " ORDER BY 1")]
                for name in ("task_drafts", "task_creations", "assets", "image_slots", "c01_snapshots",
                             "c01_runs", "c01_attempts", "c01_evidence")}


def test_preflight_has_exact_four_layers_real_jpeg_and_no_writes(experiment):
    real, snap, image = experiment
    before = all_original_rows(real)
    request = real.preflight(UUID(snap["id"]), MODEL)
    text, attached = request["request"]["input"][0]["content"]
    assert text["text"] == snap["prompt"]["planned_model_input"]["text"]
    assert base64.b64decode(attached["image_url"].split(",", 1)[1], validate=True) == image
    assert attached["type"] == "input_image" and attached["detail"] == "high"
    assert request["request"]["text"]["format"]["schema"] == snap["prompt"]["layers"][3]["source"]["schema"]
    assert request["actual_model_input"] is request["actual_model_response"] is None
    assert request["network_requests"] == 0 and request["model_api_call_attempted"] is False
    assert request["request"]["store"] is False and "tools" not in request["request"]
    report = preflight_report(request)
    assert "base64,<omitted" in report["request"]["input"][0]["content"][1]["image_url"]
    assert "base64,<omitted" not in request["request"]["input"][0]["content"][1]["image_url"]
    assert request["snapshot"]["context"]["operator_confirmed_information"] == []
    assert all_original_rows(real) == before


@pytest.mark.parametrize("damage", ["source", "frozen", "identity", "snapshot_hash"])
def test_mismatched_experiment_stops_before_transport(experiment, monkeypatch, damage):
    real, snap, image = experiment
    if damage == "source":
        (real.settings.data_dir / "assets" / UUID(snap["product_asset_id"]).hex).write_bytes(b"damaged")
    elif damage == "frozen":
        target = real.service.evidence.images / UUID(snap["id"]).hex
        target.chmod(0o600); target.write_bytes(b"damaged")
    elif damage == "identity": monkeypatch.setattr("aigc_workbench.c01_real.ASSET_ID", str(uuid4()))
    else: monkeypatch.setattr("aigc_workbench.c01_real.SNAPSHOT_SHA256", "0" * 64)
    adapter = OfflineTransport()
    with pytest.raises(C01Error): run(real, snap, adapter)
    assert adapter.calls == 0


@pytest.mark.parametrize("kind,code", [
    ("disabled", "REAL_EXECUTION_DISABLED"), ("production", "REAL_EXECUTION_DISABLED"),
    ("unconfirmed", "REAL_EXECUTION_DISABLED"), ("missing_key", "MODEL_CREDENTIAL_MISSING"),
    ("endpoint", "CUSTOM_ENDPOINT_NOT_SUPPORTED"), ("budget", "BUDGET_TOO_LOW"),
    ("nan", "BUDGET_INVALID"), ("infinity", "BUDGET_INVALID"), ("zero", "BUDGET_INVALID"),
])
def test_execution_gate_never_calls_or_creates_run(experiment, monkeypatch, kind, code):
    real, snap, _ = experiment
    quote = real.preflight(UUID(snap["id"]), MODEL)["cost"]
    settings, confirmed, budget = real.settings, True, "0.02"
    if kind == "disabled": settings = replace(settings, enable_c01_real=False)
    elif kind == "production": settings = replace(settings, environment="production")
    elif kind == "unconfirmed": confirmed = False
    elif kind == "missing_key": monkeypatch.delenv("OPENAI_API_KEY")
    elif kind == "endpoint": monkeypatch.setenv("OPENAI_BASE_URL", "https://not-authorized.invalid")
    elif kind == "budget": budget = "0.0000001"
    elif kind == "nan": budget = "NaN"
    elif kind == "infinity": budget = "Infinity"
    elif kind == "zero": budget = "0"
    with pytest.raises(C01Error) as exc: authorize(settings, confirmed, budget, quote)
    assert exc.value.code == code
    with real.store.database.connection() as db:
        assert db.execute("SELECT count(*) FROM c01_model_runs").fetchone()[0] == 0


def test_offline_execution_evidence_does_not_impersonate_model_or_change_business(experiment):
    real, snap, _ = experiment
    before = all_original_rows(real); adapter = OfflineTransport(); op = uuid4()
    result = run(real, snap, adapter, op)
    assert result["status"] == result["attempt_status"] == "SUCCEEDED" and adapter.calls == 1
    assert result["transport_label"] == "OFFLINE_TRANSPORT_TEST"
    assert result["model_api_call_attempted"] is False
    assert result["actual_model_input"] is result["actual_model_response"] is None
    assert result["provider_usage"] is result["provider_cost"] is None
    assert result["structured_output"]["label"] == "OFFLINE_TEST_ONLY"
    assert json.loads(adapter.payloads[0]) == result["planned_model_input"]
    assert sha256(adapter.payloads[0]).hexdigest() == result["request_sha256"]
    assert run(real, snap, adapter, op) == result and adapter.calls == 1
    assert all_original_rows(real) == before
    with real.store.database.connection(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM c01_model_runs WHERE id=?", (result["run_id"],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE c01_model_runs SET body_json='{}' WHERE id=?", (result["run_id"],))


@pytest.mark.parametrize("exception", [TimeoutError("secret must never be logged"),
                                         ConnectionResetError("lost after submit")])
def test_timeout_unknown_no_replay_or_exception_secret(experiment, exception):
    real, snap, _ = experiment
    adapter = OfflineTransport(exception=exception); op = uuid4()
    result = run(real, snap, adapter, op)
    assert result["status"] == result["attempt_status"] == "OUTCOME_UNKNOWN"
    assert result["raw_response_text"] is None and result["provider_receipt"] == "UNKNOWN"
    assert "secret must never" not in canonical(result)
    assert run(real, snap, adapter, op) == result and adapter.calls == 1


@pytest.mark.parametrize("kind,code", [
    ("invalid_json", "MODEL_RESPONSE_INVALID_JSON"), ("http", "MODEL_HTTP_ERROR"),
    ("incomplete", "MODEL_RESPONSE_INCOMPLETE"), ("refusal", "MODEL_REFUSAL"),
    ("missing", "MODEL_OUTPUT_MISSING"), ("bad_fields", "MODEL_OUTPUT_INVALID"),
    ("wrong_model", "RESPONSE_MODEL_MISMATCH"), ("duplicate", "MODEL_OUTPUT_INVALID"),
])
def test_response_failures_preserve_raw_and_do_not_retry(experiment, kind, code):
    real, snap, _ = experiment
    raw = fake_response(); status = 200
    if kind == "http": status = 429; raw = {"error": {"message": "offline rate limit"}}
    elif kind == "incomplete": raw["status"] = "incomplete"
    elif kind == "refusal": raw["output"][0]["content"] = [{"type": "refusal", "refusal": "offline"}]
    elif kind == "missing": raw["output"] = []
    elif kind == "bad_fields": raw["output"][0]["content"][0]["text"] = '{"visible_text":42}'
    elif kind == "wrong_model": raw["model"] = "not-the-selected-model"
    elif kind == "duplicate": raw["output"][0]["content"][0]["text"] = '{"visible_text":[],"visible_text":[]}'
    body = b"not json" if kind == "invalid_json" else canonical(raw).encode()
    adapter = OfflineTransport(body=body, status=status)
    result = run(real, snap, adapter)
    assert result["status"] == "FAILED" and result["error"]["code"] == code
    assert result["raw_response_text"].encode() == body and result["structured_output"] is None
    assert adapter.calls == 1


def test_changed_request_conflicts_and_pending_or_save_failure_never_reexecute(experiment, monkeypatch):
    real, snap, _ = experiment
    adapter = OfflineTransport(); op = uuid4()
    original_save = real.store.save
    def fail_terminal(record):
        if record["status"] != "RUNNING": raise OSError("injected evidence failure")
        original_save(record)
    monkeypatch.setattr(real.store, "save", fail_terminal)
    with pytest.raises(C01Error) as exc: run(real, snap, adapter, op)
    assert exc.value.code == "MODEL_EVIDENCE_OUTCOME_UNKNOWN" and adapter.calls == 1
    pending = run(real, snap, adapter, op)
    assert pending["status"] == "RUNNING" and pending["dispatch_state"] == "DISPATCH_INTENT_RECORDED"
    assert pending["actual_model_input"] is None and pending["provider_receipt"] == "UNKNOWN"
    assert adapter.calls == 1
    with pytest.raises(C01Error) as conflict: run(real, snap, adapter, op, max_output_tokens=512)
    assert conflict.value.code == "MODEL_OPERATION_CONFLICT" and adapter.calls == 1


def test_dispatch_evidence_failure_prevents_transport(experiment, monkeypatch):
    real, snap, _ = experiment
    def fail(record): raise OSError("injected storage failure")
    monkeypatch.setattr(real.store, "save", fail)
    adapter = OfflineTransport()
    with pytest.raises(C01Error) as exc: run(real, snap, adapter)
    assert exc.value.code == "MODEL_DISPATCH_NOT_CONFIRMED" and adapter.calls == 0


@pytest.mark.parametrize("status", [408, 500, 503])
def test_http_uncertain_errors_keep_response_and_never_retry(experiment, status):
    real, snap, _ = experiment
    adapter = OfflineTransport(body=b'{"error":{"message":"offline uncertain execution"}}', status=status)
    op = uuid4(); result = run(real, snap, adapter, op)
    assert result["status"] == "OUTCOME_UNKNOWN" and result["response_http_status"] == status
    assert result["raw_response_text"] and result["error"]["code"] == "MODEL_HTTP_ERROR"
    assert run(real, snap, adapter, op) == result and adapter.calls == 1


@pytest.mark.parametrize("model,limit", [("unverified-model", 4096), (MODEL, 0), (MODEL, 4097)])
def test_unverified_model_or_unbounded_output_fails_preflight(experiment, model, limit):
    real, snap, _ = experiment
    with pytest.raises(C01Error): real.preflight(UUID(snap["id"]), model, limit)


def test_real_service_missing_credentials_stops_before_migration(experiment, monkeypatch):
    real, snap, _ = experiment
    monkeypatch.delenv("OPENAI_API_KEY")
    def migration_forbidden(): raise AssertionError("must not migrate before authorization")
    monkeypatch.setattr(real.store.database, "initialize", migration_forbidden)
    adapter = OfflineTransport()
    with pytest.raises(C01Error) as exc: run(real, snap, adapter)
    assert exc.value.code == "MODEL_CREDENTIAL_MISSING" and adapter.calls == 0


def test_stale_pricing_denies_paid_execution(experiment, monkeypatch):
    real, snap, _ = experiment
    monkeypatch.setattr("aigc_workbench.c01_model.PRICING_CHECKED", "2000-01-01")
    with pytest.raises(C01Error) as exc: run(real, snap)
    assert exc.value.code == "PRICING_RECHECK_REQUIRED"


def test_usage_estimate_is_distinct_from_invoice_and_unknown_model_is_not_priced():
    raw = fake_response()
    estimate = usage_cost(raw)
    assert estimate["label"] == "USAGE_BASED_ESTIMATE_NOT_PROVIDER_BILL"
    assert estimate["usd"] == "0.000036"
    raw["model"] = "unexpected-model"
    assert usage_cost(raw) is None
    assert usage_cost({}) is None


def test_transport_serializes_one_post_and_redacts_key_without_network(monkeypatch):
    key = "offline-not-a-real-key"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    calls = []
    class Response:
        status = 401
        headers = {"x-request-id": "req-offline"}
        def read(self, limit): return json.dumps({"error": key}).encode()
        def close(self): pass
    class Opener:
        def open(self, request, timeout):
            assert timeout == 60 and request.get_header("Authorization") == "Bearer " + key
            calls.append((request.get_method(), request.selector, request.data))
            return Response()
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener())
    response = OpenAIResponsesTransport().send(b'{"model":"offline"}')
    assert calls == [("POST", "/v1/responses", b'{"model":"offline"}')]
    assert key not in response.body.decode() and "REDACTED_API_KEY" in response.body.decode()


@pytest.mark.parametrize("status,expected", [(429, "FAILED"), (503, "OUTCOME_UNKNOWN")])
def test_urllib_http_error_is_saved_by_existing_evidence_without_retry(experiment, monkeypatch, status, expected):
    import urllib.error
    real, snap, _ = experiment
    raw = b'{"error":{"message":"explicit offline HTTP error"}}'
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.data)
            raise urllib.error.HTTPError(request.full_url, status, "offline error", {}, BytesIO(raw))
    class OfflineHTTPTransport(OpenAIResponsesTransport):
        label = "OFFLINE_TRANSPORT_TEST"
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener())
    operation = uuid4(); adapter = OfflineHTTPTransport()
    result = run(real, snap, adapter, operation)
    assert result["status"] == expected and result["response_http_status"] == status
    assert result["raw_response_text"].encode() == raw and result["error"]["code"] == "MODEL_HTTP_ERROR"
    assert result["actual_model_response"] is None and result["model_api_call_attempted"] is False
    assert run(real, snap, adapter, operation) == result and len(calls) == 1


def test_cli_preflight_has_no_network_or_credential_requirement(experiment, monkeypatch, capsys):
    real, snap, _ = experiment
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("AIGC_DATA_DIR", str(real.settings.data_dir))
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    monkeypatch.setenv("AIGC_ENV", "development")
    assert main(["preflight-real", "--snapshot-id", snap["id"], "--model", MODEL]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests"] == 0 and result["credentials"]["OPENAI_API_KEY_configured"] is False
    assert main(["run-real", "--snapshot-id", snap["id"], "--model", MODEL,
                 "--operation-id", str(uuid4()), "--max-cost-usd", "0.02"]) == 2
    assert "REAL_EXECUTION_DISABLED" in capsys.readouterr().err


def test_v3_to_v4_migration_preserves_s02_and_rolls_back(experiment, monkeypatch):
    import aigc_workbench.persistence as persistence
    real, _, _ = experiment
    before = all_original_rows(real)
    dbpath = real.settings.database_path
    with sqlite3.connect(dbpath) as db:
        db.execute('DROP TABLE task_requirements')
        db.execute('ALTER TABLE task_creations DROP COLUMN initial_overall_requirement')
        db.execute("DROP TRIGGER c01_model_runs_terminal")
        db.execute("DROP TRIGGER c01_model_runs_retain")
        db.execute('DROP TABLE slot_deliveries')
        db.execute('DROP TABLE image_reviews')
        db.execute("DROP TABLE c01_model_runs")
        db.execute("DROP TABLE image_versions")  # Reconstruct an actual pre-C06 v3 test schema.
        db.execute("DROP TABLE c06_dispatches")
        db.execute("PRAGMA user_version=3")
    original = persistence.V4_TABLES
    monkeypatch.setattr(persistence, "V4_TABLES", original + ["INVALID SQL"])
    with pytest.raises(sqlite3.Error): real.store.database.initialize()
    with sqlite3.connect(dbpath) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='c01_model_runs'").fetchone()[0] == 0
    expected_v3 = {**before, "task_creations": [{k:v for k,v in r.items() if k != "initial_overall_requirement"} for r in before["task_creations"]]}
    assert all_original_rows(real) == expected_v3
    monkeypatch.setattr(persistence, "V4_TABLES", original)
    real.store.database.initialize()
    assert all_original_rows(real) == before
