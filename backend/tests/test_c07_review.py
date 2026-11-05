"""C07, manual review and delivery against isolated synthetic stores; never paid inference."""
import base64
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from test_creation_decision import source, decision_output
from test_c06 import generation, SyntheticWire, run
from test_workspace_generation import route, finish, settings
from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error
from aigc_workbench.c07_revision import C07Service
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.c06_generation import MODEL


@pytest.fixture
def revision(generation, monkeypatch):
    service, snapshot, directory, original = generation
    path = directory / '05_C06_EXECUTION_HANDOFF.json'
    handoff = json.loads(path.read_text())
    decision = decision_output()['02_IMAGE_CREATION_DECISION']
    decision['C06_creation_input'] = handoff['image_creation_decision']['C06_creation_input']
    handoff['image_creation_decision'] = decision
    path.write_text(json.dumps(handoff))
    manifest = directory / 'delivery-manifest-final.json'
    hashes = json.loads(manifest.read_text()); hashes[path.name]['sha256'] = sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(hashes))
    snapshot = service.prepare(UUID(snapshot['decision_snapshot_id']), directory, MODEL)
    new_generation = service, snapshot, directory, original
    v1 = run(new_generation, SyntheticWire())['candidate']
    monkeypatch.setenv('AIGC_ENABLE_C07_REAL', '1')
    return new_generation, C07Service(service.settings, service.service), v1


def quote(client, path, base, text='Only change Wide Voltage to #17365D; preserve all other elements.'):
    result = client.post(path + '/versions/' + base['image_version_id'] + '/revision-quote', json={'revision_text': text})
    assert result.status_code == 200, result.text
    return result.json()


def revise(client, path, q, op=None):
    return client.post(path + '/revisions', json={'operation_id': str(op or uuid4()), 'snapshot_id': q['snapshot_id'],
        'quote_token': q['quote_token'], 'confirm_paid': True, 'max_cost_usd': '1.00'})


def review(client, path, version, decision='PASS', current=None, op=None):
    return client.post(path + '/versions/' + version['image_version_id'] + '/reviews', json={
        'operation_id': str(op or uuid4()), 'decision': decision, 'reviewer': 'Synthetic human tester',
        'feedback': 'TEST ONLY, no business acceptance', 'expected_current_review_id': current})


def test_selected_base_and_three_exact_images_no_history_replay(revision):
    generation, service, v1 = revision
    snapshot = service.prepare(UUID(v1['task_id']), UUID(v1['slot_id']), UUID(v1['image_version_id']), 'CURRENT_REVISION_ONLY')
    before = service.service.evidence.load_snapshot(UUID(snapshot['id']))
    flight = service.preflight(UUID(snapshot['id']), MODEL)
    assert flight['network_requests'] == 0 and len(flight['request']['images']) == 3
    data = [base64.b64decode(i['image_url'].split(',', 1)[1]) for i in flight['request']['images']]
    assert data == [CandidateStore(service.store.database, service.settings.data_dir)._read_png(UUID(v1['image_version_id'])), *generation[3]['blobs']]
    assert 'CURRENT_REVISION_ONLY' in flight['request']['prompt']
    assert snapshot['context']['historical_revision_instructions'] == 'NOT_REPLAYED'
    assert len(snapshot['prompt']['layers']) == 4
    assert service.service.evidence.load_snapshot(UUID(snapshot['id'])) == before


def test_c07_replay_and_old_base_preserved(revision):
    generation, service, v1 = revision
    task, slot = UUID(v1['task_id']), UUID(v1['slot_id'])
    snap = service.prepare(task, slot, UUID(v1['image_version_id']), 'OLD_FEEDBACK_CANARY')
    wire = SyntheticWire(); operation = uuid4()
    v2run = service.run(UUID(snap['id']), MODEL, operation, confirmed=True, max_cost_usd='1.00', transport=wire)
    assert v2run['candidate']['image_version_id'] != v1['image_version_id']
    assert v2run['candidate']['base_image_version_id'] == v1['image_version_id']
    assert service.run(UUID(snap['id']), MODEL, operation, confirmed=True, max_cost_usd='1.00', transport=wire) == v2run
    assert wire.calls == 1
    v3 = service.prepare(task, slot, UUID(v2run['candidate']['image_version_id']), 'NEW_REVISION_ONLY')
    assert 'OLD_FEEDBACK_CANARY' not in service.preflight(UUID(v3['id']), MODEL)['request']['prompt']
    assert service.service.evidence.load_snapshot(UUID(snap['id'])) == snap
    assert CandidateStore(service.store.database, service.settings.data_dir).inspect(UUID(v1['image_version_id'])) == v1


@pytest.mark.parametrize('error', [TimeoutError('synthetic'), None])
def test_failed_or_unknown_revision_keeps_v1_without_retry(revision, error):
    generation, service, v1 = revision
    path = route(generation); wire = SyntheticWire(error=error, status=403)
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        q = quote(client, path, v1); operation = uuid4()
        assert revise(client, path, q, operation).status_code == 202
        body = finish(client, path)
        assert body['state'] == ('OUTCOME_UNKNOWN' if error else 'FAILED')
        assert [c['image_version_id'] for c in body['candidates']] == [v1['image_version_id']]
        assert revise(client, path, q, operation).status_code == 202
        assert wire.calls == 1
        assert client.get(path + '/versions/' + v1['image_version_id'] + '/content').status_code == 200


def test_review_delivery_v1_remains_until_v2_explicit_pass_and_restart(revision):
    generation, service, v1 = revision
    path = route(generation); wire = SyntheticWire()
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        assert client.get(path + '/delivery?review_id=' + str(uuid4())).status_code == 404
        op = uuid4(); passed = review(client, path, v1, op=op)
        assert passed.status_code == 201
        r1 = passed.json(); assert review(client, path, v1, op=op).json() == r1
        assert r1['source'] == 'TEST_REVIEW'
        first = client.get(path + '/workspace').json()['delivery']
        png1 = client.get(first['url']); assert png1.content == service.service.evidence.read_snapshot_image(
            service.prepare(UUID(v1['task_id']), UUID(v1['slot_id']), UUID(v1['image_version_id']), 'nonpaid quote'))
        assert png1.headers['content-disposition'].startswith('attachment;')
        q = quote(client, path, v1)
        assert revise(client, path, q).status_code == 202
        body = finish(client, path); v2 = body['candidates'][0]
        assert v2['image_version_id'] != v1['image_version_id'] and v2['review_status'] == 'PENDING'
        assert body['delivery']['id'] == r1['id']
        assert review(client, path, v2, 'REJECT', r1['id']).status_code == 201
        body = client.get(path + '/workspace').json()
        assert body['candidates'][0]['review_status'] == 'REJECT' and body['delivery']['id'] == r1['id']
        # CAS prevents a stale browser tab from replacing an effective delivery.
        assert review(client, path, v2).status_code == 409
        r2 = review(client, path, v2, current=r1['id']).json()
        assert r2['decision'] == 'PASS'
        body = client.get(path + '/workspace').json()
        assert body['delivery']['image_version_id'] == v2['image_version_id']
        assert len(body['reviews']) == 3 and body['candidates'][1]['has_historical_pass']
        assert client.get(first['url']).status_code == 409
        assert review(client, path, v1, 'REJECT', r2['id']).status_code == 409
        assert wire.calls == 1
        delivery_url = body['delivery']['url']
        expected = client.get(v2['url']).content
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        assert client.get(delivery_url).content == expected
        assert len(client.get(path + '/workspace').json()['reviews']) == 3
        assert wire.calls == 1
    # Synthetic reviews may not become real operator acceptance when opened without the substitute.
    with TestClient(create_app(settings(generation)), base_url='http://localhost') as client:
        assert client.get(delivery_url).status_code == 422


def test_scope_stale_constraints_and_operation_conflicts_stop_before_payment(revision):
    generation, service, v1 = revision
    path = route(generation); wire = SyntheticWire()
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        assert client.post(path + '/versions/' + str(uuid4()) + '/revision-quote', json={'revision_text': 'edit'}).status_code == 404
        assert review(client, path, {**v1, 'image_version_id': str(uuid4())}).status_code == 404
        q = quote(client, path, v1)
        generation[0].service.tasks.configure_slot(UUID(v1['task_id']), UUID(v1['slot_id']),
            generation[3]['assets'][1].id, '1024x1536', 'CHANGED_EFFECTIVE_REQUIREMENT')
        assert revise(client, path, q).status_code == 503
        assert wire.calls == 0


def test_reviews_and_delivery_are_scope_safe_and_append_only(revision):
    generation, service, v1 = revision
    path = route(generation); wire = SyntheticWire()
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        r = review(client, path, v1).json()
        with pytest.raises(sqlite3.IntegrityError):
            with service.store.database.connection(write=True) as db:
                db.execute('UPDATE image_reviews SET feedback=?', ('tamper',))
        with pytest.raises(sqlite3.IntegrityError):
            with service.store.database.connection(write=True) as db:
                db.execute('DELETE FROM image_reviews')
        other = generation[0].service.tasks.create_draft('unrelated', uuid4())
        wrong = f'/api/tasks/{other.id}/slots/{v1["slot_id"]}'
        assert client.get(wrong + '/delivery?review_id=' + r['id']).status_code == 404
        assert wire.calls == 0


def test_v7_additive_migration_rollback_and_no_existing_record_changes(revision, monkeypatch):
    import aigc_workbench.persistence as persistence
    _, service, _ = revision; database = service.store.database
    with database.connection(write=True) as db:
        db.execute('DROP TABLE slot_deliveries'); db.execute('DROP TABLE image_reviews'); db.execute('ALTER TABLE c06_dispatches DROP COLUMN progress_json'); db.execute('PRAGMA user_version=6')
        tables = ['task_drafts','assets','image_slots','c01_snapshots','c01_model_runs','image_versions','c06_dispatches']
        before = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}
    original = persistence.V7_TABLES
    monkeypatch.setattr(persistence, 'V7_TABLES', original + ['INVALID SQL'])
    with pytest.raises(sqlite3.Error): database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='image_reviews'").fetchall()
    monkeypatch.setattr(persistence, 'V7_TABLES', original); database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert all([tuple(r) for r in db.execute('SELECT * FROM ' + t)] == before[t] for t in tables)


@pytest.mark.parametrize('case', ['disabled', 'budget', 'http500', 'wrong_size'])
def test_revision_guards_and_failed_results_never_create_false_candidate(revision, monkeypatch, case):
    generation, service, v1 = revision
    snapshot = service.prepare(UUID(v1['task_id']), UUID(v1['slot_id']), UUID(v1['image_version_id']), 'edit only color')
    wire = SyntheticWire(status=500 if case == 'http500' else 200, size=(1024,1024) if case == 'wrong_size' else (1024,1536))
    if case == 'disabled': monkeypatch.delenv('AIGC_ENABLE_C07_REAL')
    kwargs = dict(confirmed=True, max_cost_usd='0.01' if case == 'budget' else '1.00', transport=wire)
    if case in {'disabled','budget'}:
        with pytest.raises(C01Error): service.run(UUID(snapshot['id']), MODEL, uuid4(), **kwargs)
        assert wire.calls == 0
    else:
        result = service.run(UUID(snapshot['id']), MODEL, uuid4(), **kwargs)
        assert result['status'] == ('OUTCOME_UNKNOWN' if case == 'http500' else 'FAILED')
        assert 'candidate' not in result and wire.calls == 1
    assert CandidateStore(service.store.database, service.settings.data_dir).inspect(UUID(v1['image_version_id'])) == v1
