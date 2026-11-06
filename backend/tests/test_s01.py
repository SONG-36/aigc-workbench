"""Real temporary files/SQLite; fault injection is explicitly named."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import sqlite3
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.application import StorageUnavailable
from aigc_workbench.config import Settings
from aigc_workbench.persistence import SQLiteTaskStore, SQLiteChanges


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "runtime")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        yield client


@pytest.fixture
def png():
    output = BytesIO()
    Image.new("RGB", (12, 9), "#ccddee").save(output, format="PNG")
    return output.getvalue()


def task(client, title="S01"):
    response = client.post("/api/tasks", json={"title": title, "operation_id": str(uuid4())})
    assert response.status_code == 201
    return response.json()


def upload(client, task_id, data, role="product_image", operation_id=None, filename="sample.png"):
    return client.post(f"/api/tasks/{task_id}/assets", params={
        "role": role, "operation_id": str(operation_id or uuid4()), "filename": filename,
    }, content=data, headers={"Content-Type": "image/png"})


def slot(client, task_id, kind="main", operation_id=None):
    return client.post(f"/api/tasks/{task_id}/slots",
                       json={"kind": kind, "operation_id": str(operation_id or uuid4())})


def bind(client, task_id, slot_id, asset_id):
    return client.put(f"/api/tasks/{task_id}/slots/{slot_id}", json={
        "reference_asset_id": asset_id, "output_size": "1024x1024", "requirements": "保留本图要求",
    })


def seed_v1(settings):
    settings.data_dir.mkdir(parents=True)
    identity, created = str(uuid4()), "2026-11-04T00:00:00+00:00"
    with sqlite3.connect(settings.database_path) as db:
        db.execute("""CREATE TABLE task_drafts (
            id TEXT PRIMARY KEY, title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
            mode TEXT NOT NULL CHECK(mode = 'quick_a'),
            status TEXT NOT NULL CHECK(status = 'draft'), created_at TEXT NOT NULL)""")
        db.execute("INSERT INTO task_drafts VALUES (?,?,?,?,?)",
                   (identity, "existing F01", "quick_a", "draft", created))
        db.execute("PRAGMA user_version = 1")
    return identity, created


def test_v1_upgrade_preserves_identity_fields_and_supports_new_operations(settings, png):
    identity, created = seed_v1(settings)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        saved = client.get(f"/api/tasks/{identity}").json()
        assert saved["id"] == identity and saved["title"] == "existing F01"
        assert saved["created_at"] == "2026-11-04T00:00:00Z"
        assert saved["assets"] == saved["slots"] == []
        assert upload(client, identity, png).status_code == 201
        reference = upload(client, identity, png, "marketing_reference").json()["assets"][-1]
        configured = slot(client, identity).json()
        slot_id = configured["slots"][0]["id"]
        assert slot_id != reference["id"]
        assert bind(client, identity, slot_id, reference["id"]).status_code == 200
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert db.execute("SELECT id,title,created_at FROM task_drafts").fetchone() == (
            identity, "existing F01", created)
    with TestClient(create_app(settings), base_url="http://localhost") as restarted:
        restored = restarted.get(f"/api/tasks/{identity}").json()
        assert restored["id"] == identity and len(restored["assets"]) == 2
        assert restored["slots"][0]["reference_asset_id"] == reference["id"]
        assert restored["slots"][0]["requirements"] == "保留本图要求"
        assert restored["materials_ready"] is True and restored["can_generate"] is False
        response = restarted.get(reference["url"])
        assert response.content == png
        assert sha256(response.content).hexdigest() == reference["sha256"]


def test_failed_migration_rolls_back_schema_and_preserves_v1(settings, monkeypatch):
    identity, created = seed_v1(settings)
    original = SQLiteTaskStore._upgrade_v1
    def broken(self, db):
        original(self, db)
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(SQLiteTaskStore, "_upgrade_v1", broken)
    with pytest.raises(RuntimeError, match="migration failure"):
        SQLiteTaskStore(settings.database_path).initialize()
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT id,created_at FROM task_drafts").fetchone() == (identity, created)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='assets'").fetchone() is None


def test_task_creation_replay_and_conflict_and_same_name_allowed(client):
    op = str(uuid4())
    payload = {"title": "same", "operation_id": op}
    first = client.post("/api/tasks", json=payload)
    assert client.post("/api/tasks", json=payload).json() == first.json()
    assert client.post("/api/tasks", json={**payload, "title": "different"}).status_code == 409
    second = task(client, "same")
    assert second["id"] != first.json()["id"]
    assert len(client.get("/api/tasks").json()) == 2


def test_upload_replay_conflicts_and_no_accidental_hash_dedup(client, png):
    identity = task(client)["id"]
    op = uuid4()
    first = upload(client, identity, png, operation_id=op)
    assert first.status_code == 201
    assert upload(client, identity, png, operation_id=op).json() == first.json()
    assert upload(client, identity, png, "marketing_reference", op).status_code == 409
    assert upload(client, identity, png, operation_id=op, filename="changed.png").status_code == 409
    other = BytesIO(); Image.new("RGB", (4, 4), "red").save(other, "PNG")
    assert upload(client, identity, other.getvalue(), operation_id=op).status_code == 409
    assert len(upload(client, identity, png).json()["assets"]) == 2


def test_slots_are_independent_idempotent_and_scoped(client, png):
    a, b = task(client)["id"], task(client)["id"]
    product = upload(client, a, png).json()["assets"][0]
    reference = upload(client, a, png, "marketing_reference").json()["assets"][-1]
    foreign = upload(client, b, png, "marketing_reference").json()["assets"][0]
    op = uuid4()
    created = slot(client, a, operation_id=op).json()
    sid = created["slots"][0]["id"]
    assert sid not in {product["id"], reference["id"]}
    assert slot(client, a, operation_id=op).json() == created
    assert slot(client, a).status_code == 409
    assert slot(client, a, "scene", op).status_code == 409
    assert bind(client, a, sid, foreign["id"]).status_code == 422
    assert bind(client, a, sid, product["id"]).status_code == 422
    assert bind(client, b, sid, foreign["id"]).status_code == 404
    assert client.get(f"/api/tasks/{b}/assets/{reference['id']}/content").status_code == 404
    good = bind(client, a, sid, reference["id"])
    assert good.status_code == 200
    assert bind(client, a, sid, reference["id"]).json() == good.json()
    for _ in range(2):
        assert slot(client, a, "selling_point").status_code == 201
    final = client.get(f"/api/tasks/{a}").json()
    assert len({s["id"] for s in final["slots"]}) == 3
    assert [s["position"] for s in final["slots"]] == [0, 1, 2]
    assert final["slots"][0]["inputs_ready"] is True
    assert final["slots"][1]["inputs_ready"] is False
    assert final["materials_ready"] is True and final["can_generate"] is False


@pytest.mark.parametrize("data", [b"", b"not an image", b"<svg></svg>", b"\x89PNG\r\n\x1a\n"])
def test_invalid_images_never_leave_saved_assets(client, data):
    identity = task(client)["id"]
    assert upload(client, identity, data).status_code == 422
    assert client.get(f"/api/tasks/{identity}").json()["assets"] == []
    files = client.app.state.task_service.files
    assert list(files.assets.iterdir()) == list(files.staging.iterdir()) == []


def test_byte_pixel_and_actual_type_limits(client, png, monkeypatch):
    identity = task(client)["id"]
    assert upload(client, identity, b"x" * (10 * 1024 * 1024 + 1)).status_code == 413
    response = client.post(f"/api/tasks/{identity}/assets", params={
        "role": "product_image", "operation_id": str(uuid4()), "filename": "fake.jpg",
    }, content=png, headers={"Content-Type": "image/jpeg"})
    assert response.status_code == 422
    monkeypatch.setattr("aigc_workbench.file_storage.MAX_IMAGE_PIXELS", 1)
    assert upload(client, identity, png).status_code == 422
    assert client.get(f"/api/tasks/{identity}").json()["assets"] == []


def test_truncated_decodable_header_is_rejected(client):
    out = BytesIO(); Image.new("RGB", (50, 50)).save(out, "JPEG")
    identity = task(client)["id"]
    response = client.post(f"/api/tasks/{identity}/assets", params={
        "role": "product_image", "operation_id": str(uuid4()), "filename": "broken.jpg",
    }, content=out.getvalue()[:-50], headers={"Content-Type": "image/jpeg"})
    assert response.status_code == 422


def test_missing_runtime_db_get_fails_without_creating_file(client, settings):
    identity = task(client)["id"]
    settings.database_path.rename(settings.data_dir / "preserved.sqlite3")
    assert client.get(f"/api/tasks/{identity}").status_code == 503
    assert not settings.database_path.exists()


def test_db_failure_before_publication_cleans_staging(client, png, monkeypatch):
    identity = task(client)["id"]
    def fail(*args):
        raise sqlite3.OperationalError("injected insert failure")
    monkeypatch.setattr(SQLiteChanges, "add_asset", fail)
    assert upload(client, identity, png).status_code == 503
    assert client.get(f"/api/tasks/{identity}").json()["assets"] == []
    files = client.app.state.task_service.files
    assert list(files.staging.iterdir()) == list(files.assets.iterdir()) == []


def test_file_publication_failure_rolls_back_db(client, png, monkeypatch):
    identity = task(client)["id"]
    files = client.app.state.task_service.files
    def fail(*args):
        raise StorageUnavailable("injected file failure")
    monkeypatch.setattr(files, "publish", fail)
    assert upload(client, identity, png).status_code == 503
    assert client.get(f"/api/tasks/{identity}").json()["assets"] == []
    assert list(files.staging.iterdir()) == list(files.assets.iterdir()) == []


@pytest.mark.parametrize("commit_happened", [False, True])
def test_published_file_retained_on_ambiguous_commit_and_replay_safe(client, png, monkeypatch, commit_happened):
    identity = task(client)["id"]
    service = client.app.state.task_service
    original = service.store.write
    @contextmanager
    def broken():
        with original() as changes:
            yield changes
            if not commit_happened:
                raise sqlite3.OperationalError("injected pre-commit failure")
        raise sqlite3.OperationalError("injected lost commit acknowledgment")
    op = uuid4()
    monkeypatch.setattr(service.store, "write", broken)
    assert upload(client, identity, png, operation_id=op).status_code == 503
    assert len(list(service.files.assets.iterdir())) == 1
    assert list(service.files.staging.iterdir()) == []
    assert len(service.orphan_files()) == (0 if commit_happened else 1)
    monkeypatch.setattr(service.store, "write", original)
    retry = upload(client, identity, png, operation_id=op)
    assert retry.status_code == 201 and len(retry.json()["assets"]) == 1
    assert client.get(retry.json()["assets"][0]["url"]).content == png


def test_file_integrity_and_symlink_are_not_served(client, png, tmp_path):
    identity = task(client)["id"]
    asset = upload(client, identity, png).json()["assets"][0]
    path = client.app.state.task_service.files.assets / UUID(asset["id"]).hex
    path.write_bytes(b"changed")
    assert client.get(asset["url"]).status_code == 503
    path.unlink()
    external = tmp_path / "outside"; external.write_bytes(png)
    path.symlink_to(external)
    assert client.get(asset["url"]).status_code == 503
    assert client.get(f"/api/tasks/{identity}/assets/../../etc/passwd/content").status_code in {404, 422}


def test_foreign_keys_are_enabled_for_every_write(client, png):
    a, b = task(client)["id"], task(client)["id"]
    reference = upload(client, a, png, "marketing_reference").json()["assets"][0]
    sid = slot(client, b).json()["slots"][0]["id"]
    with pytest.raises(sqlite3.IntegrityError):
        with client.app.state.task_service.store.write() as changes:
            assert changes.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            changes.connection.execute("UPDATE image_slots SET reference_asset_id=? WHERE id=?",
                                       (reference["id"], sid))
    assert client.get(f"/api/tasks/{b}").json()["slots"][0]["reference_asset_id"] is None


def test_concurrent_task_and_upload_retries_share_one_identity(client, png):
    from concurrent.futures import ThreadPoolExecutor
    from aigc_workbench.domain import AssetRole
    service = client.app.state.task_service
    operation = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = list(pool.map(lambda _: service.create_draft("concurrent", operation), range(4)))
    assert len({t.id for t in tasks}) == 1 and len(service.list_drafts()) == 1
    upload_op = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(pool.map(lambda _: service.upload_asset(
            tasks[0].id, upload_op, AssetRole.PRODUCT_IMAGE, "same.png", png, "image/png"), range(4)))
    assert len({a.id for a in assets}) == 1
    assert len(service.get_draft(tasks[0].id).assets) == 1
    assert len(list(service.files.assets.iterdir())) == 1
    assert list(service.files.staging.iterdir()) == []


def test_disconnected_upload_does_not_reach_application(client):
    import asyncio
    from fastapi import HTTPException
    from starlette.requests import Request
    from aigc_workbench.api import upload_asset
    from aigc_workbench.domain import AssetRole
    messages = iter([{"type": "http.request", "body": b"partial", "more_body": True},
                     {"type": "http.disconnect"}])
    async def receive():
        return next(messages)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    identity = UUID(task(client)["id"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(upload_asset(identity, request, client.app.state.task_service,
                                 AssetRole.PRODUCT_IMAGE, uuid4(), "partial.png"))
    assert exc.value.status_code == 400
    assert client.app.state.task_service.get_draft(identity).assets == ()
    assert list(client.app.state.task_service.files.staging.iterdir()) == []


def test_staging_failure_does_not_create_asset(client, png, monkeypatch):
    identity = task(client)["id"]
    files = client.app.state.task_service.files
    def fail(*args, **kwargs):
        raise OSError("injected filesystem failure")
    monkeypatch.setattr("aigc_workbench.file_storage.os.fsync", fail)
    assert upload(client, identity, png).status_code == 503
    assert client.get(f"/api/tasks/{identity}").json()["assets"] == []
    assert list(files.staging.iterdir()) == list(files.assets.iterdir()) == []


def test_reference_replacement_keeps_slot_identity_and_original_files(client, png):
    identity = task(client)["id"]
    upload(client, identity, png)
    first = upload(client, identity, png, "marketing_reference").json()["assets"][-1]
    second = upload(client, identity, png, "marketing_reference").json()["assets"][-1]
    sid = slot(client, identity).json()["slots"][0]["id"]
    assert bind(client, identity, sid, first["id"]).status_code == 200
    replaced = bind(client, identity, sid, second["id"]).json()
    assert replaced["slots"][0]["id"] == sid
    assert replaced["slots"][0]["reference_asset_id"] == second["id"]
    assert len(replaced["assets"]) == 3
    assert client.get(first["url"]).content == client.get(second["url"]).content == png
