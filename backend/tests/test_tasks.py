from pathlib import Path
import ast
import sqlite3
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from aigc_workbench.app import create_app
from aigc_workbench.config import Settings
from aigc_workbench.domain import InvalidTask, TaskDraft
from aigc_workbench.persistence import SQLiteTaskStore


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "runtime")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        yield client


def test_domain_normalizes_title_without_claiming_readiness():
    task = TaskDraft.create("  sample product  ")
    assert task.title == "sample product"
    assert isinstance(task.id, UUID)
    assert task.mode == "quick_a" and task.status == "draft"
    assert task.can_generate is False


@pytest.mark.parametrize("title", ["", " \t\n", "x" * 121])
def test_invalid_draft_never_becomes_a_domain_task(title):
    with pytest.raises(InvalidTask):
        TaskDraft.create(title)


def test_create_query_list_and_new_app_restore_same_record(settings):
    with TestClient(create_app(settings), base_url="http://localhost") as first:
        response = first.post("/api/tasks", json={"title": "F01 persistence", "operation_id": str(uuid4())})
        assert response.status_code == 201
        saved = response.json()
        UUID(saved["id"])
        assert saved["can_generate"] is False
        assert saved["status"] == "draft"
        assert first.get(f"/api/tasks/{saved['id']}").json() == saved
        assert first.get("/api/tasks").json() == [saved]
    with TestClient(create_app(settings), base_url="http://localhost") as restarted:
        assert restarted.get(f"/api/tasks/{saved['id']}").json() == saved
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM task_drafts").fetchone()[0] == 1


@pytest.mark.parametrize("payload", [
    {}, {"title": "  "}, {"title": "x" * 121}, {"title": 123},
    {"title": "test", "mode": "quick_b"},
    {"title": "test", "status": "ready"},
    {"title": "test", "can_generate": True},
])
def test_invalid_input_does_not_save_a_record(client, payload):
    response = client.post("/api/tasks", json={**payload, "operation_id": str(uuid4())})
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"INVALID_REQUEST", "INVALID_TASK"}
    assert client.get("/api/tasks").json() == []


def test_missing_and_malformed_identifiers_return_clear_errors(client):
    response = client.get(f"/api/tasks/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"
    assert client.get("/api/tasks/not-a-uuid").status_code == 422


def test_no_generation_review_or_ai_endpoint_exists(client):
    task = client.post("/api/tasks", json={"title": "unprepared", "operation_id": str(uuid4())}).json()
    assert task["can_generate"] is False
    for action in ["generate", "review", "context", "ai"]:
        assert client.post(f"/api/tasks/{task['id']}/{action}", json={}).status_code == 404
    assert client.get(f"/api/tasks/{task['id']}").json() == task


def test_cross_origin_writes_and_untrusted_hosts_are_denied(client):
    response = client.post("/api/tasks", json={"title": "blocked", "operation_id": str(uuid4())},
                           headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_DENIED"
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/health", headers={"Host": "untrusted.example"}).status_code == 403
    assert client.post("/api/tasks", json={"title": "local", "operation_id": str(uuid4())},
                       headers={"Origin": "http://127.0.0.1:8021"}).status_code == 201


def test_sql_parameter_binding_keeps_title_as_data(client):
    title = "sample'); DROP TABLE task_drafts; --"
    assert client.post("/api/tasks", json={"title": title, "operation_id": str(uuid4())}).json()["title"] == title
    assert len(client.get("/api/tasks").json()) == 1


def test_storage_failure_is_not_reported_as_success(client, monkeypatch):
    def fail(self, task, operation_id):
        raise sqlite3.OperationalError("test storage failure")
    from aigc_workbench.persistence import SQLiteChanges
    monkeypatch.setattr(SQLiteChanges, "add_task", fail)
    response = client.post("/api/tasks", json={"title": "not saved", "operation_id": str(uuid4())})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert client.get("/api/tasks").json() == []


def test_newer_schema_fails_closed_without_reset(settings):
    settings.data_dir.mkdir()
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        SQLiteTaskStore(settings.database_path).initialize()
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_unexpected_failure_has_a_safe_error_response(settings, monkeypatch):
    with TestClient(create_app(settings), base_url="http://localhost",
                    raise_server_exceptions=False) as client:
        def fail(title, operation_id):
            raise RuntimeError("internal detail must not reach the browser")
        monkeypatch.setattr(client.app.state.task_service, "create_draft", fail)
        response = client.post("/api/tasks", json={"title": "not saved", "operation_id": str(uuid4())})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "internal detail" not in response.text
        assert client.get("/api/tasks").json() == []


def import_roots(source):
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add(node.module.split(".")[0] if node.module and not node.level else "relative")
    return roots


def test_domain_has_no_framework_database_or_model_imports():
    source = Path(__file__).parents[1] / "src/aigc_workbench/domain.py"
    assert import_roots(source.read_text()) <= {"dataclasses", "datetime", "enum", "uuid"}


@pytest.mark.parametrize("source", [
    "import fastapi", "import sqlalchemy as sa", "import openai", "import sqlite3",
    "from fastapi import FastAPI", "from openai import OpenAI",
])
def test_domain_import_guard_rejects_both_import_forms(source):
    assert not import_roots(source) <= {"dataclasses", "datetime", "enum", "uuid"}
