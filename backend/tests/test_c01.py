"""S02 real temporary business files/SQLite; mocks only for named fault injection."""
from hashlib import sha256
from io import BytesIO
import json
import sqlite3
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.c01_cli import main
from aigc_workbench.c01_context import C01Error, fingerprint
from aigc_workbench.c01_execution import C01Service, C01TestAdapter, require_development
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.c01_prompt import validate_composition, readable_request
from aigc_workbench.config import Settings
from aigc_workbench.persistence import SQLiteTaskStore, V1_TASKS, V2_TABLES


@pytest.fixture
def source(tmp_path):
    settings = Settings(tmp_path / "runtime", environment="development", enable_c01_testing=True)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        task = client.post("/api/tasks", json={
            "title": "title_is_not_a_product_fact_9000W", "operation_id": str(uuid4()),
        }).json()
        blobs, assets = [], []
        for role, color in [("product_image", "red"), ("marketing_reference", "blue")]:
            stream = BytesIO(); Image.new("RGB", (9, 7), color).save(stream, "PNG")
            blob = stream.getvalue(); blobs.append(blob)
            response = client.post(f"/api/tasks/{task['id']}/assets", params={
                "role": role, "operation_id": str(uuid4()), "filename": role + "_999W.png",
            }, content=blob, headers={"Content-Type": "image/png"})
            assert response.status_code == 201
            assets.append(next(a for a in response.json()["assets"] if a["role"] == role))
        tasks = client.app.state.task_service
        store = C01EvidenceStore(tasks.store, settings.data_dir)
        service = C01Service(settings, tasks, store)
        yield {"settings": settings, "client": client, "service": service, "store": store,
               "task": UUID(task["id"]), "product": UUID(assets[0]["id"]),
               "reference": UUID(assets[1]["id"]), "blobs": blobs}


def prepare(source):
    return source["service"].prepare(source["task"], source["product"])


def business_rows(source):
    with source["service"].tasks.store.connection() as db:
        return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
                for table in ["task_drafts", "task_creations", "assets", "image_slots"]}


def snapshot_count(source):
    with source["service"].tasks.store.connection() as db:
        return db.execute("SELECT count(*) FROM c01_snapshots").fetchone()[0]


def test_real_source_context_four_layers_and_no_reference_contamination(source):
    before = business_rows(source)
    snap = prepare(source)
    assert snap["source_selection"]["label"] == "REAL BUSINESS SOURCE"
    assert snap["product_asset_id"] == str(source["product"])
    assert snap["context"]["operator_confirmed_information"] == []
    assert snap["effective_state"]["operator_confirmed_information"] == []
    assert snap["source_selection"]["excluded_sources"][0]["asset_id"] == str(source["reference"])
    layers = snap["prompt"]["layers"]
    assert [x["index"] for x in layers] == [1, 2, 3, 4]
    assert all(x["content"] and x["source"] for x in layers)
    planned = snap["prompt"]["planned_model_input"]
    assert planned["label"] == "PLANNED MODEL INPUT" and len(planned["images"]) == 1
    assert planned["images"][0]["source_asset_id"] == str(source["product"])
    assert planned["images"][0]["sha256"] == sha256(source["blobs"][0]).hexdigest()
    assert sha256(source["blobs"][1]).hexdigest() not in planned["text"]
    assert "title_is_not_a_product_fact_9000W" not in planned["text"]
    assert "_999W.png" not in planned["text"]
    assert snap["actual_model_input"] is None and snap["prompt"]["model_api_call_attempted"] is False
    assert "visible_structure" in layers[3]["content"] and "inferences" in layers[3]["content"]
    assert source["store"].read_snapshot_image(snap) == source["blobs"][0]
    assert business_rows(source) == before
    assert "NOT SENT TO A MODEL" in readable_request(snap)


@pytest.mark.parametrize("target,code", [
    ("missing_task", "TASK_NOT_FOUND"), ("missing_asset", "ASSET_NOT_IN_TASK"),
    ("reference", "WRONG_ASSET_ROLE"), ("foreign", "ASSET_NOT_IN_TASK"),
])
def test_source_identity_errors_never_create_snapshot(source, target, code):
    task_id, asset_id = source["task"], source["product"]
    if target == "missing_task": task_id = uuid4()
    elif target == "missing_asset": asset_id = uuid4()
    elif target == "reference": asset_id = source["reference"]
    else:
        other = source["service"].tasks.create_draft("other", uuid4())
        task_id = other.id
    with pytest.raises(C01Error) as exc:
        source["service"].prepare(task_id, asset_id)
    assert exc.value.code == code
    assert snapshot_count(source) == 0


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_original_source_missing_or_corrupt_fails_closed(source, damage):
    path = source["service"].tasks.files.assets / source["product"].hex
    if damage == "missing": path.unlink()
    else: path.write_bytes(b"not the saved bytes")
    with pytest.raises(C01Error, match="素材") as exc:
        prepare(source)
    assert exc.value.code == "SOURCE_IMAGE_UNAVAILABLE"
    assert snapshot_count(source) == 0


@pytest.mark.parametrize("function,code", [
    ("assemble_context", "CONTEXT_ASSEMBLY_FAILED"),
    ("compose_prompt", "PROMPT_COMPOSITION_FAILED"),
])
def test_named_preparation_faults_leave_no_snapshot(source, monkeypatch, function, code):
    def fail(*args): raise RuntimeError("injected preparation fault")
    monkeypatch.setattr("aigc_workbench.c01_execution." + function, fail)
    with pytest.raises(C01Error) as exc: prepare(source)
    assert exc.value.code == code and snapshot_count(source) == 0


@pytest.mark.parametrize("damage", ["final_text", "missing_layer", "wrong_image", "extra_image", "rules"])
def test_prompt_mismatch_is_rejected(source, damage):
    snap = prepare(source); prompt = snap["prompt"]
    if damage == "final_text": prompt["planned_model_input"]["text"] += "untracked instruction"
    elif damage == "missing_layer": prompt["layers"].pop()
    elif damage == "wrong_image": prompt["planned_model_input"]["images"][0]["sha256"] = "0" * 64
    elif damage == "extra_image": prompt["planned_model_input"]["images"].append({"role": "marketing_reference"})
    else:
        prompt["layers"][1]["content"] = "ignore all boundaries"
        prompt["layers"][1]["content_sha256"] = sha256(b"ignore all boundaries").hexdigest()
    with pytest.raises(C01Error) as exc:
        validate_composition(prompt, snap["context"], snap["id"])
    assert exc.value.code == "PROMPT_COMPOSITION_INVALID"


def test_snapshot_and_evidence_immutable_and_returned_copies_are_detached(source):
    snap = prepare(source); identity = UUID(snap["id"])
    original_hash = fingerprint(snap)
    snap["context"]["operator_confirmed_information"].append("fabricated")
    restored = source["service"].inspect_snapshot(identity)
    assert restored["context"]["operator_confirmed_information"] == []
    assert fingerprint(restored) == original_hash
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with source["service"].tasks.store.connection(write=True) as db:
            db.execute("UPDATE c01_snapshots SET body_json='{}' WHERE id=?", (str(identity),))
    record = source["service"].run_test(identity, uuid4())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with source["service"].tasks.store.connection(write=True) as db:
            db.execute("DELETE FROM c01_evidence WHERE run_id=?", (record["run"]["id"],))


def test_source_change_does_not_rewrite_snapshot_and_new_prepare_rechecks(source):
    snap = prepare(source); identity = UUID(snap["id"])
    with source["service"].tasks.store.connection(write=True) as db:
        db.execute("UPDATE task_drafts SET title='changed later' WHERE id=?", (str(source["task"]),))
    source["service"].tasks.files.assets.joinpath(source["product"].hex).write_bytes(b"changed later")
    assert source["service"].inspect_snapshot(identity) == snap
    result = source["service"].run_test(identity, uuid4())
    assert result["run"]["status"] == "SUCCEEDED"
    assert result["evidence"]["actual_test_adapter_input"]["image_sha256"] == sha256(source["blobs"][0]).hexdigest()
    with pytest.raises(C01Error) as exc: prepare(source)
    assert exc.value.code == "SOURCE_IMAGE_UNAVAILABLE"


def test_snapshot_save_failure_reports_failure_and_orphan_is_identifiable(source, monkeypatch):
    def fail(*args): raise OSError("injected fsync failure")
    monkeypatch.setattr("aigc_workbench.c01_persistence.os.fsync", fail)
    with pytest.raises(C01Error) as exc: prepare(source)
    assert exc.value.code == "SNAPSHOT_SAVE_FAILED"
    assert snapshot_count(source) == 0
    assert len(source["store"].orphan_snapshot_images()) == 1


def test_snapshot_db_failure_does_not_return_success(source, monkeypatch):
    def fail(*args): raise sqlite3.OperationalError("injected save fault")
    monkeypatch.setattr(source["store"], "save_snapshot", fail)
    with pytest.raises(C01Error) as exc: prepare(source)
    assert exc.value.code == "SNAPSHOT_SAVE_FAILED"
    assert snapshot_count(source) == 0


def test_adapter_receives_real_frozen_bytes_and_no_network_or_business_write(source, monkeypatch):
    import socket
    def forbidden(*args, **kwargs): raise AssertionError("network forbidden in S02")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    snap = prepare(source); before = business_rows(source)
    class Capturing(C01TestAdapter):
        def execute(self, text, image_bytes, image_ref, mode):
            assert image_bytes == source["blobs"][0] and image_bytes != source["blobs"][1]
            assert text == snap["prompt"]["planned_model_input"]["text"]
            return super().execute(text, image_bytes, image_ref, mode)
    result = source["service"].run_test(UUID(snap["id"]), uuid4(), adapter=Capturing())
    assert result["run"]["status"] == result["attempt"]["status"] == "SUCCEEDED"
    assert result["run"]["evidence_status"] == "AVAILABLE"
    evidence = result["evidence"]
    assert evidence["adapter"] == "TEST_ONLY / MOCK_ADAPTER"
    assert evidence["raw_test_result"]["visual_understanding"] is None
    assert evidence["raw_test_result"]["AI_DERIVED"] is False
    assert evidence["model_api_call_attempted"] is False
    assert evidence["actual_model_input"] is evidence["actual_model_response"] is None
    assert business_rows(source) == before


def test_adapter_failure_is_persisted_as_failure_with_error(source):
    snap = prepare(source)
    result = source["service"].run_test(UUID(snap["id"]), uuid4(), mode="fail")
    assert result["run"]["status"] == result["attempt"]["status"] == "FAILED"
    assert result["evidence"]["error"]["code"] == "TEST_ADAPTER_FAILURE"
    assert result["evidence"]["raw_test_result"] is None
    assert result["evidence"]["adapter_invoked"] is True


def test_bad_test_adapter_result_cannot_be_a_success(source):
    snap = prepare(source)
    class Invalid(C01TestAdapter):
        def execute(self, *args):
            return {"label": "real vision", "AI_DERIVED": True}
    result = source["service"].run_test(UUID(snap["id"]), uuid4(), adapter=Invalid())
    assert result["run"]["status"] == "FAILED"
    assert result["evidence"]["error"]["code"] == "ADAPTER_RESULT_INVALID"


def test_run_replay_never_reexecutes_and_conflicting_intent_fails(source):
    snap = prepare(source); operation = uuid4()
    first = source["service"].run_test(UUID(snap["id"]), operation)
    class MustNotCall(C01TestAdapter):
        def execute(self, *args): raise AssertionError("duplicate execution")
    repeated = source["service"].run_test(UUID(snap["id"]), operation, adapter=MustNotCall())
    assert repeated["run"]["id"] == first["run"]["id"]
    assert repeated["attempt"]["id"] == first["attempt"]["id"]
    with pytest.raises(C01Error) as exc:
        source["service"].run_test(UUID(snap["id"]), operation, mode="fail")
    assert exc.value.code == "OPERATION_CONFLICT"


def test_evidence_failure_does_not_report_success(source, monkeypatch):
    snap = prepare(source)
    def fail(*args): raise sqlite3.OperationalError("injected evidence failure")
    monkeypatch.setattr(source["store"], "finish_run", fail)
    result = source["service"].run_test(UUID(snap["id"]), uuid4())
    assert result["run"]["status"] == result["attempt"]["status"] == "FAILED"
    assert result["run"]["evidence_status"] == "MISSING" and result["evidence"] is None
    assert result["run"]["error"]["code"] == "EVIDENCE_SAVE_FAILED"
    assert result["attempt"]["raw_result"]["receipt"]["image_byte_count"] == len(source["blobs"][0])


def test_complete_persistence_failure_leaves_visible_pending_not_success(source, monkeypatch):
    snap = prepare(source)
    def fail(*args): raise sqlite3.OperationalError("storage unavailable")
    monkeypatch.setattr(source["store"], "finish_run", fail)
    monkeypatch.setattr(source["store"], "mark_evidence_failure", fail)
    with pytest.raises(C01Error) as exc:
        source["service"].run_test(UUID(snap["id"]), uuid4())
    assert exc.value.code == "EVIDENCE_OUTCOME_UNKNOWN"
    history = source["store"].history(source["task"])
    assert history["runs"][0]["status"] == "RUNNING"
    assert history["runs"][0]["evidence_status"] == "PENDING"


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_frozen_image_failure_retains_failed_run_for_inspection(source, damage):
    snap = prepare(source); image = source["store"].images / UUID(snap["id"]).hex
    if damage == "missing": image.unlink()
    else: image.chmod(0o600); image.write_bytes(b"corrupt")
    result = source["service"].run_test(UUID(snap["id"]), uuid4())
    assert result["run"]["status"] == "FAILED"
    assert result["evidence"]["adapter_invoked"] is False
    assert result["snapshot_image_integrity"]["status"] == "UNAVAILABLE"


def test_inconsistent_run_attempt_state_is_rejected(source):
    snap = prepare(source)
    run_id, _ = source["store"].start_run(UUID(snap["id"]), uuid4(), "success")
    with source["service"].tasks.store.connection(write=True) as db:
        db.execute("UPDATE c01_attempts SET status='FAILED' WHERE run_id=?", (str(run_id),))
    with pytest.raises(C01Error) as exc: source["service"].inspect_run(run_id)
    assert exc.value.code == "EXECUTION_STATE_INCONSISTENT"


@pytest.mark.parametrize("flag,environment", [(False, "development"), (True, "production"), (True, "test")])
def test_developer_entry_fails_closed(flag, environment, tmp_path):
    settings = Settings(tmp_path, environment=environment, enable_c01_testing=flag)
    with pytest.raises(C01Error) as exc: require_development(settings)
    assert exc.value.code == "DEVELOPER_ENTRY_DISABLED"
    assert not settings.database_path.exists()


def test_cli_inspection_history_and_query_failure(source, monkeypatch, capsys):
    monkeypatch.setenv("AIGC_ENV", "development")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    monkeypatch.setenv("AIGC_DATA_DIR", str(source["settings"].data_dir))
    snap = prepare(source)
    assert main(["inspect-snapshot", "--snapshot-id", snap["id"], "--format", "readable"]) == 0
    assert "PLANNED MODEL INPUT" in capsys.readouterr().out
    assert main(["run", "--snapshot-id", snap["id"], "--operation-id", str(uuid4())]) == 0
    executed = json.loads(capsys.readouterr().out)
    assert main(["inspect-run", "--run-id", executed["run"]["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["label"] == "TEST_ONLY EXECUTION"
    assert main(["history", "--task-id", str(source["task"])]) == 0
    assert len(json.loads(capsys.readouterr().out)["runs"]) == 1
    def fail(*args): raise sqlite3.OperationalError("query failure")
    monkeypatch.setattr(C01EvidenceStore, "inspect_run", fail)
    assert main(["inspect-run", "--run-id", executed["run"]["id"]]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "C01_STORAGE_OR_RECORD_ERROR" in captured.err


def test_missing_snapshot_and_run_have_explicit_errors(source):
    with pytest.raises(C01Error) as snapshot: source["service"].inspect_snapshot(uuid4())
    with pytest.raises(C01Error) as run: source["service"].inspect_run(uuid4())
    assert snapshot.value.code == "SNAPSHOT_NOT_FOUND" and run.value.code == "RUN_NOT_FOUND"


def test_v2_to_v3_additive_migration_and_rollback(tmp_path, monkeypatch):
    import aigc_workbench.persistence as persistence
    dbpath = tmp_path / "legacy.sqlite3"
    identity = str(uuid4())
    with sqlite3.connect(dbpath) as db:
        db.execute(V1_TASKS)
        for statement in V2_TABLES: db.execute(statement)
        db.execute("INSERT INTO task_drafts VALUES (?,?,?,?,?)",
                   (identity, "S01 preserved", "quick_a", "draft", "2026-11-04T00:00:00+00:00"))
        db.execute("PRAGMA user_version=2")
    original = persistence.V3_TABLES
    monkeypatch.setattr(persistence, "V3_TABLES", original + ["INVALID MIGRATION SQL"])
    with pytest.raises(sqlite3.Error): SQLiteTaskStore(dbpath).initialize()
    with sqlite3.connect(dbpath) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='c01_snapshots'").fetchone()[0] == 0
    monkeypatch.setattr(persistence, "V3_TABLES", original)
    SQLiteTaskStore(dbpath).initialize()
    with sqlite3.connect(dbpath) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("SELECT id,title FROM task_drafts").fetchone() == (identity, "S01 preserved")


def test_rule_source_missing_and_source_change_during_selection_fail_closed(source, tmp_path, monkeypatch):
    service = C01Service(source["settings"], source["service"].tasks, source["store"], source_root=tmp_path)
    with pytest.raises(C01Error) as missing:
        service.prepare(source["task"], source["product"])
    assert missing.value.code == "RULE_SOURCE_UNAVAILABLE"
    original = source["service"].tasks.read_asset
    def changing(task_id, asset_id):
        result = original(task_id, asset_id)
        with source["service"].tasks.store.connection(write=True) as db:
            db.execute("UPDATE task_drafts SET title='raced' WHERE id=?", (str(task_id),))
        return result
    monkeypatch.setattr(source["service"].tasks, "read_asset", changing)
    with pytest.raises(C01Error) as changed: prepare(source)
    assert changed.value.code == "SOURCE_CHANGED" and snapshot_count(source) == 0


def test_cli_disabled_before_any_storage_access(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AIGC_DATA_DIR", str(tmp_path / "must-not-exist"))
    monkeypatch.setenv("AIGC_ENV", "production")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    assert main(["history", "--task-id", str(uuid4())]) == 2
    assert "DEVELOPER_ENTRY_DISABLED" in capsys.readouterr().err
    assert not (tmp_path / "must-not-exist").exists()


def test_cli_missing_task_and_adapter_failure_are_nonzero(source, monkeypatch, capsys):
    monkeypatch.setenv("AIGC_DATA_DIR", str(source["settings"].data_dir))
    monkeypatch.setenv("AIGC_ENV", "development")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    assert main(["history", "--task-id", str(uuid4())]) == 2
    assert "TASK_NOT_FOUND" in capsys.readouterr().err
    snap = prepare(source)
    assert main(["run", "--snapshot-id", snap["id"], "--operation-id", str(uuid4()),
                 "--adapter-mode", "fail"]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["run"]["status"] == "FAILED" and record["evidence"]["error"]


def test_tampered_snapshot_and_attempt_data_are_detected(source):
    snap = prepare(source)
    result = source["service"].run_test(UUID(snap["id"]), uuid4())
    with source["service"].tasks.store.connection(write=True) as db:
        db.execute("DROP TRIGGER c01_attempts_terminal")
        db.execute("UPDATE c01_attempts SET raw_result_json='{}' WHERE run_id=?", (result["run"]["id"],))
    with pytest.raises(C01Error) as evidence:
        source["service"].inspect_run(UUID(result["run"]["id"]))
    assert evidence.value.code == "EVIDENCE_INTEGRITY_FAILED"
    with source["service"].tasks.store.connection(write=True) as db:
        db.execute("DROP TRIGGER c01_snapshots_immutable_update")
        db.execute("UPDATE c01_snapshots SET body_json='{}' WHERE id=?", (snap["id"],))
    with pytest.raises(C01Error) as snapshot:
        source["service"].inspect_snapshot(UUID(snap["id"]))
    assert snapshot.value.code == "SNAPSHOT_INTEGRITY_FAILED"


def test_unexpected_adapter_exception_keeps_type_without_leaking_internal_details(source):
    snap = prepare(source)
    class Broken(C01TestAdapter):
        def execute(self, *args):
            raise RuntimeError("PRIVATE_INTERNAL_DETAIL")
    result = source["service"].run_test(UUID(snap["id"]), uuid4(), adapter=Broken())
    assert result["run"]["status"] == "FAILED"
    assert result["evidence"]["error"]["cause_type"] == "RuntimeError"
    assert "PRIVATE_INTERNAL_DETAIL" not in json.dumps(result)
