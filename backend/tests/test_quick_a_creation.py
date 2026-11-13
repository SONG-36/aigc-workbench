"""A new Task through HTTP only; all model tools are explicitly TEST_ONLY."""
import base64
from io import BytesIO
import json
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from quick_a_adapters import UnderstandingSubstitute, ImageSubstitute, source_png
from test_workspace_generation import finish
from aigc_workbench.app import create_app
from aigc_workbench.config import Settings
from aigc_workbench.persistence import SQLiteTaskStore


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('NETWORK_FORBIDDEN'))
    monkeypatch.setenv('OPENAI_API_KEY', 'TEST_ONLY_NOT_A_REAL_CREDENTIAL')
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.setenv('AIGC_ENABLE_C06_REAL', '1'); monkeypatch.setenv('AIGC_ENABLE_C07_REAL', '1')
    return Settings(tmp_path / 'runtime', enable_c01_testing=True, enable_c01_real=True, enable_workspace_generation=True)


def application(config, understanding, images):
    # 本文件保留历史多阶段回归；新默认路径由 test_quick_a_direct 直接使用 create_app 验证。
    from contextlib import asynccontextmanager
    app = create_app(config, generation_transport=images, understanding_transport=understanding)
    original = app.router.lifespan_context
    @asynccontextmanager
    async def legacy_lifespan(app):
        async with original(app):
            service = app.state.generation_service
            service._select = service._select_legacy
            yield
    app.router.lifespan_context = legacy_lifespan
    return app


def save_slot(client, path, values):
    # 现有业务回归显式读取当前版本；并发测试单独提交保留的旧版本。
    task_id, slot_id = path.split('/')[3], path.split('/')[5]
    task = client.get('/api/tasks/'+task_id).json()
    version = next((s['version'] for s in task.get('slots',[]) if s['id']==slot_id),0)
    return client.request('PUT',path,json={**values,'expected_version':version})


def asset(client, task_id, role, color):
    blob = source_png(color)
    response = client.post(f'/api/tasks/{task_id}/assets', params={'role': role, 'operation_id': str(uuid4()),
        'filename': f'TEST_ONLY_{color}.png'}, content=blob, headers={'Content-Type': 'image/png'})
    assert response.status_code == 201, response.text
    return response.json()['assets'][-1], blob


def new_task(client, size='1536x1536', kind='main'):
    response = client.post('/api/tasks', json={'title': 'TEST_ONLY new Quick-A task', 'operation_id': str(uuid4())})
    assert response.status_code == 201
    task = response.json()['id']
    product, product_bytes = asset(client, task, 'product_image', 'red')
    reference, reference_bytes = asset(client, task, 'marketing_reference', 'blue')
    response = client.post(f'/api/tasks/{task}/slots', json={'kind': kind, 'operation_id': str(uuid4())})
    assert response.status_code == 201
    slot = response.json()['slots'][-1]
    path = f'/api/tasks/{task}/slots/{slot["id"]}'
    config = {'reference_asset_id': reference['id'], 'output_size': size, 'requirements': 'TEST_ONLY preserve the supplied badge'}
    assert save_slot(client, path, config).status_code == 200
    return path, task, slot['id'], (product, reference), (product_bytes, reference_bytes), config


def submit(client, path, operation=None):
    ready = client.get(path + '/workspace').json()['readiness']
    assert ready['can_generate'], ready
    command = {'operation_id': str(operation or uuid4()), 'quote_token': ready['quote_token']}
    response = client.post(path + '/generations', json=command)
    assert response.status_code == 202, response.text
    return command


def image_bytes(request):
    parts = request['images'] if 'images' in request else request['input'][0]['content'][1:]
    return [base64.b64decode(p['image_url'].split(',', 1)[1]) for p in parts]


def test_c01_debug_prints_only_first_call_without_secrets_or_image_data(config, monkeypatch, capsys):
    secret = 'TEST_ONLY_DEBUG_API_KEY_CANARY'
    image_data = 'QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo='
    monkeypatch.setenv('AIGC_DEBUG_C01', '1')
    monkeypatch.setenv('OPENAI_API_KEY', secret)

    class EchoCanaries(UnderstandingSubstitute):
        def send(self, payload):
            response = super().send(payload)
            if json.loads(payload)['text']['format']['name'] == 'c01_visual_observation':
                body = json.loads(response.body)
                part = body['output'][0]['content'][0]
                parsed = json.loads(part['text'])
                parsed['uncertainties'] = [secret, 'Bearer PRIVATE_TOKEN_CANARY',
                    'data:image/png;base64,' + image_data]
                part['text'] = json.dumps(parsed)
                return type(response)(response.status, json.dumps(body).encode(), response.request_id)
            return response

    understanding, images = EchoCanaries(), ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url='http://localhost') as client:
        path, task, slot, *_ = new_task(client)
        submit(client, path); body = finish(client, path)
        assert body['state'] == 'SUCCEEDED', body
        first = body['executions'][0]['progress']['steps'][0]
        record = app.state.generation_service.runs.inspect(UUID(first['run_id']))
        # Debug output must not redact or otherwise rewrite persisted model Evidence.
        assert record['structured_output']['content']['uncertainties'][0] == secret
        assert record['actual_test_adapter_input']['request'] == understanding.requests[0]
    output = capsys.readouterr().err
    headers = [line for line in output.splitlines() if '[QUICK_A_C01_DEBUG]' in line]
    assert len(headers) == 6 and all(f'Task={task} ImageSlot={slot} Step=C01' in h for h in headers)
    for label in ['INPUT', 'PROMPT', 'RESPONSE', 'PARSED RESULT', 'DECISION', 'NEXT STEP INPUT']:
        assert any(h.endswith(' ' + label) for h in headers)
    assert understanding.requests[0]['input'][0]['content'][0]['text'] in output
    assert 'OFFLINE_TRANSPORT_TEST' in output and 'C02 does not consume C01 output' in output
    assert secret not in output and 'PRIVATE_TOKEN_CANARY' not in output and image_data not in output
    assert understanding.requests[0]['input'][0]['content'][1]['image_url'] not in output
    assert len(understanding.requests) == 3 and len(images.requests) == 1


@pytest.mark.parametrize('environment,flag,capability', [
    ('development', '0', 'C01'), ('production', '1', 'C01'),
    ('pilot', '1', 'C01'), ('development', '1', 'C02'),
])
def test_c01_debug_is_silent_without_local_opt_in(config, monkeypatch, capsys, environment, flag, capability):
    from dataclasses import replace
    from aigc_workbench.c01_real import C01RealService
    service = object.__new__(C01RealService)
    service.settings = replace(config, environment=environment)
    monkeypatch.setenv('AIGC_DEBUG_C01', flag)
    # Empty inputs are deliberate: disabled logging must not inspect private payloads.
    service._debug_c01('RESPONSE', {'capability': capability}, {}, 'PRIVATE_PAYLOAD_CANARY')
    service._debug_c01_return({'capability': capability}, {})
    assert capsys.readouterr().err == ''


def test_c01_debug_marks_cached_result_without_claiming_a_new_call(config, monkeypatch, capsys):
    monkeypatch.setenv('AIGC_DEBUG_C01', '1')
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, _, _, _, _, values = new_task(client)
        submit(client, path); assert finish(client, path)['state'] == 'SUCCEEDED'
        capsys.readouterr()
        save_slot(client, path, {**values, 'requirements': 'TEST_ONLY new slot requirement'}).raise_for_status()
        submit(client, path); assert finish(client, path)['state'] == 'SUCCEEDED'
    output = capsys.readouterr().err
    assert 'REUSE_SAVED_RUN_NO_MODEL_CALL' in output and 'NEXT STEP INPUT' in output
    assert '] Task=' in output and ' Step=C01 ' in output
    assert ' PROMPT\n' not in output and ' RESPONSE\n' not in output
    assert len(understanding.requests) == 4 and len(images.requests) == 2


def test_c01_debug_keeps_failed_response_separate_from_a_parsed_result(config, monkeypatch, capsys):
    monkeypatch.setenv('AIGC_DEBUG_C01', '1')
    understanding, images = UnderstandingSubstitute(fail='c01_visual_observation'), ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, *_ = new_task(client)
        submit(client, path); assert finish(client, path)['state'] == 'FAILED'
    output = capsys.readouterr().err
    assert 'TEST_ONLY_FAILURE' in output and 'PARSED RESULT\nnull' in output
    assert 'STOP_FAILED_OR_UNKNOWN_C01' in output and '"continuation_allowed": false' in output
    assert len(understanding.requests) == 1 and not images.requests


@pytest.mark.parametrize('size', ['1024x1536', '1536x1536'])
def test_new_task_http_to_c01_c02_decision_c06_and_restart(config, size):
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url='http://localhost') as client:
        path, task, slot, assets, originals, values = new_task(client, size)
        for _ in range(2): assert client.get(path + '/workspace').json()['readiness']['can_generate']
        assert not understanding.requests and not images.requests
        command = submit(client, path)
        body = finish(client, path)
        assert body['state'] == 'SUCCEEDED', body
        candidate = body['candidates'][0]
        assert candidate['task_id'] == task and candidate['slot_id'] == slot
        assert candidate['execution_mode'] == 'TEST_ONLY' and candidate['not_for_real_delivery']
        assert not candidate['pass'] and not body['delivery'] and body['reviews'] == []
        assert candidate['product_asset_id'] == assets[0]['id'] and candidate['reference_asset_id'] == assets[1]['id']
        with Image.open(BytesIO(client.get(candidate['url']).content)) as image:
            assert image.size == tuple(map(int, size.split('x')))
        assert len(understanding.requests) == 3 and len(images.requests) == 1
        assert all(r['model'] == 'gpt-6.1-terra' and r['reasoning'] == {'effort': 'medium'}
                   and 'temperature' not in r for r in understanding.requests)
        assert [r['max_output_tokens'] for r in understanding.requests] == [8192, 8192, 16384]
        assert images.requests[0]['model'] == 'gpt-image-2.6-sunburst'
        assert image_bytes(understanding.requests[0]) == [originals[0]]
        assert image_bytes(understanding.requests[1]) == [originals[1]]
        assert 'TEST_ONLY_PRODUCT_COMPONENT' not in understanding.requests[1]['input'][0]['content'][0]['text']
        assert image_bytes(understanding.requests[2]) == list(originals)
        assert image_bytes(images.requests[0]) == list(originals)
        assert images.requests[0]['size'] == ('1024x1024' if size == '1536x1536' else size)
        progress = body['executions'][0]['progress']
        assert progress['stage'] == 'REVIEW_PENDING'
        assert [s['capability'] for s in progress['steps']] == ['C01', 'C02', 'C03_C04_C05', 'C06']
        for step in progress['steps']:
            record = app.state.generation_service.runs.inspect(UUID(step['run_id']))
            assert record['model_api_call_attempted'] is False and record['actual_model_response'] is None
            assert record['offline_test_response'] and record['structured_output']['label'] == 'OFFLINE_TEST_ONLY'
            assert record['actual_test_adapter_input']['request'] == record['planned_model_input']
            assert record['provider_usage'] is None and record['provider_cost'] is None
            assert record['cost_preflight'] and step['attempt_id']
        assert client.post(path + '/generations', json=command).status_code == 202
        assert len(images.requests) == 1 and len(understanding.requests) == 3
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        restored = client.get(path + '/workspace').json()
        assert restored['candidates'][0]['image_version_id'] == candidate['image_version_id']
        assert restored['executions'][0]['progress'] == progress
        assert len(images.requests) == 1 and len(understanding.requests) == 3


def test_effective_requirement_and_source_changes_reuse_only_valid_understanding(config):
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, task, slot, assets, originals, values = new_task(client)
        submit(client, path); first = finish(client, path)
        values['requirements'] = 'TEST_ONLY new explicit requirement'
        assert save_slot(client, path, values).status_code == 200
        submit(client, path); second = finish(client, path)
        assert second['state'] == 'SUCCEEDED', second
        assert len(understanding.requests) == 4
        assert [s['reused'] for s in second['executions'][0]['progress']['steps']] == [True, True, False, False]
        assert first['candidates'][0]['image_version_id'] in [c['image_version_id'] for c in second['candidates']]
        reference, ref_bytes = asset(client, task, 'marketing_reference', 'green')
        values['reference_asset_id'] = reference['id']; save_slot(client, path, values)
        submit(client, path); third = finish(client, path)
        assert third['state'] == 'SUCCEEDED', third
        assert len(understanding.requests) == 6 and image_bytes(images.requests[-1]) == [originals[0], ref_bytes]
        product, product_bytes = asset(client, task, 'product_image', 'yellow')
        submit(client, path); fourth = finish(client, path)
        assert fourth['state'] == 'SUCCEEDED', fourth
        assert len(understanding.requests) == 8
        assert fourth['candidates'][0]['product_asset_id'] == product['id']
        assert image_bytes(images.requests[-1]) == [product_bytes, ref_bytes]


@pytest.mark.parametrize('stage', ['c01_visual_observation', 'c02_reference_observation', 'quick_a_creation_decision', 'image'])
@pytest.mark.parametrize('unknown', [False, True])
def test_each_stage_failure_or_unknown_is_persisted_without_retries(config, stage, unknown):
    understanding = UnderstandingSubstitute(fail=stage if not unknown else None, unknown=stage if unknown else None)
    images = ImageSubstitute(fail=stage == 'image' and not unknown, unknown=stage == 'image' and unknown)
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, *_ = new_task(client)
        command = submit(client, path); body = finish(client, path)
        assert body['state'] == ('OUTCOME_UNKNOWN' if unknown else 'FAILED'), body
        assert not body['candidates'] and not body['delivery']
        counts = len(understanding.requests), len(images.requests)
        for _ in range(2):
            client.get(path + '/workspace')
            assert client.post(path + '/generations', json=command).status_code == 202
        assert (len(understanding.requests), len(images.requests)) == counts
        if unknown:
            ready = client.get(path + '/workspace').json()['readiness']
            assert not ready['can_generate']


def test_server_phase_is_durable_and_queries_never_start_another_operation(config):
    entered, release = Event(), Event()
    understanding = UnderstandingSubstitute(pause=('c02_reference_observation', entered, release))
    images = ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, *_ = new_task(client)
        command = submit(client, path)
        try:
            assert entered.wait(3)
            for _ in range(2):
                body = client.get(path + '/workspace').json()
                assert body['state'] == 'RUNNING' and body['executions'][0]['progress']['stage'] == 'C02'
                assert client.post(path + '/generations', json=command).status_code == 202
            assert len(understanding.requests) == 2 and not images.requests
        finally: release.set()
        assert finish(client, path)['state'] == 'SUCCEEDED'


def test_selected_base_revision_and_explicit_test_review_preserve_version(config):
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url='http://localhost') as client:
        path, *_ = new_task(client)
        submit(client, path); body = finish(client, path); v1 = body['candidates'][0]
        original = client.get(v1['url']).content
        response = client.post(path + '/versions/' + v1['image_version_id'] + '/revision-quote',
                               json={'revision_text': 'TEST_ONLY change title color'})
        assert response.status_code == 200, response.text
        q = response.json()
        assert client.post(path + '/revisions', json={'operation_id': str(uuid4()),
            'snapshot_id': q['snapshot_id'], 'quote_token': q['quote_token']}).status_code == 202
        body = finish(client, path); v2 = body['candidates'][0]
        assert v2['image_version_id'] != v1['image_version_id'] and v2['base_image_version_id'] == v1['image_version_id']
        assert images.requests[-1]['model'] == 'gpt-image-2.6-sunburst'
        assert image_bytes(images.requests[-1])[0] == original
        assert client.get(v1['url']).content == original
        assert not v2['pass'] and not body['delivery']
        assert '12V/24V' not in images.requests[-1]['prompt']
        assert (v2['image']['width'], v2['image']['height']) == (1536, 1536)
        review = {'operation_id': str(uuid4()), 'decision': 'PASS', 'reviewer': 'TEST_ONLY human interaction',
                  'feedback': 'TEST_ONLY, not business acceptance', 'expected_current_review_id': None}
        response = client.post(path + '/versions/' + v2['image_version_id'] + '/reviews', json=review)
        assert response.status_code == 201 and response.json()['source'] == 'TEST_REVIEW'
    # A normal app must never turn the saved substitute into real operator delivery or a paid edit.
    with TestClient(create_app(config), base_url='http://localhost') as client:
        review['operation_id'] = str(uuid4())
        assert client.post(path + '/versions/' + v1['image_version_id'] + '/reviews', json=review).status_code == 422


def test_stale_quote_and_cross_task_binding_cannot_start_a_model(config):
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, task, _, assets, _, values = new_task(client)
        ready = client.get(path + '/workspace').json()['readiness']
        values['requirements'] = 'changed'; save_slot(client, path, values)
        assert client.post(path + '/generations', json={'operation_id': str(uuid4()), 'quote_token': ready['quote_token']}).status_code == 409
        other, _, _, other_assets, _, _ = new_task(client)
        assert save_slot(client, path, {**values, 'reference_asset_id': other_assets[1]['id']}).status_code == 422
        assert not understanding.requests and not images.requests


def test_concurrent_same_operation_starts_only_one_full_creation(config):
    understanding, images = UnderstandingSubstitute(delay=.05), ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, *_ = new_task(client)
        ready = client.get(path + '/workspace').json()['readiness']
        command = {'operation_id': str(uuid4()), 'quote_token': ready['quote_token']}
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda _: client.post(path + '/generations', json=command), range(3)))
        assert all(r.status_code == 202 for r in responses)
        assert finish(client, path)['state'] == 'SUCCEEDED'
        assert len(understanding.requests) == 3 and len(images.requests) == 1


def test_changed_requirements_during_understanding_stop_later_calls(config):
    entered, release = Event(), Event()
    understanding = UnderstandingSubstitute(pause=('c01_visual_observation', entered, release))
    images = ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, _, _, _, _, values = new_task(client)
        submit(client, path)
        try:
            assert entered.wait(3)
            save_slot(client, path, {**values, 'requirements': 'CHANGED_WHILE_RUNNING'})
        finally: release.set()
        body = finish(client, path)
        assert body['state'] == 'FAILED' and not body['candidates']
        assert len(understanding.requests) == 1 and not images.requests


@pytest.mark.parametrize('state', ['QUEUED', 'RUNNING'])
def test_new_creation_restart_never_replays_an_unfinished_operation(config, monkeypatch, state):
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url='http://localhost') as client:
        path, *_ = new_task(client)
        monkeypatch.setattr(app.state.generation_service.dispatcher.pool, 'submit', lambda *a: None)
        command = submit(client, path)
        database = app.state.generation_service.database
        with database.connection(write=True) as db:
            db.execute('UPDATE c06_dispatches SET state=?,progress_json=? WHERE operation_id=?',
                       (state, json.dumps({'stage': 'C01', 'steps': []}), command['operation_id']))
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        body = client.get(path + '/workspace').json()
        assert body['state'] == ('FAILED' if state == 'QUEUED' else 'OUTCOME_UNKNOWN')
        assert not understanding.requests and not images.requests
        assert client.post(path + '/generations', json=command).status_code == 202
        assert not understanding.requests and not images.requests


def test_v8_migration_rolls_back_and_preserves_v7_business_and_evidence(config, monkeypatch):
    import aigc_workbench.persistence as persistence
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url='http://localhost') as client:
        path, *_ = new_task(client)
        submit(client, path); assert finish(client, path)['state'] == 'SUCCEEDED'
    database = SQLiteTaskStore(config.database_path)
    with database.connection(write=True) as db:
        db.execute('DROP TABLE task_requirements')
        db.execute('ALTER TABLE task_creations DROP COLUMN initial_overall_requirement')
        db.execute('ALTER TABLE c06_dispatches DROP COLUMN progress_json')
        db.execute('PRAGMA user_version=7')
        names = ['task_drafts', 'assets', 'image_slots', 'c01_snapshots', 'c01_model_runs', 'image_versions']
        before = {name: [tuple(r) for r in db.execute('SELECT * FROM ' + name)] for name in names}
    statements = persistence.V8_TABLES
    monkeypatch.setattr(persistence, 'V8_TABLES', statements + ['INVALID SQL'])
    with pytest.raises(sqlite3.Error): database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 7
        assert 'progress_json' not in [r[1] for r in db.execute('PRAGMA table_info(c06_dispatches)')]
    monkeypatch.setattr(persistence, 'V8_TABLES', statements); database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 12
        for name, rows in before.items(): assert [tuple(r) for r in db.execute('SELECT * FROM ' + name)] == rows


@pytest.mark.parametrize('kind', ['comparison', 'selling_point', 'scene'])
def test_new_creation_uses_the_actual_supported_slot_kind(config, kind):
    understanding, images = UnderstandingSubstitute(), ImageSubstitute()
    with TestClient(application(config, understanding, images), base_url='http://localhost') as client:
        path, *_ = new_task(client, kind=kind)
        submit(client, path); body = finish(client, path)
        assert body['state'] == 'SUCCEEDED', body
        assert f'"kind":"{kind}"' in understanding.requests[2]['input'][0]['content'][0]['text']
