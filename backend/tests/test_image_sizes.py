"""Offline pixel-size integration; no provider calls or writes to existing runtime data."""

import base64
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from test_creation_decision import source
from test_c06 import generation, SyntheticWire, run
from test_c07_review import revision, quote, revise
from test_workspace_generation import settings, route, finish
from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.generation_application import GenerationApplication
from aigc_workbench.image_output import final_png
from aigc_workbench.image_sizes import OUTPUT_SIZES, output_plan


def resize_slot(generation, size, requirements=None):
    service, _, _, original = generation
    task = service.service.tasks.get_draft(original['task'])
    slot = next(s for s in task.slots if s.id == original['slot'])
    return service.service.tasks.configure_slot(original['task'], slot.id, slot.reference_asset_id,
        size, slot.requirements if requirements is None else requirements)


@pytest.mark.parametrize('size', sorted(OUTPUT_SIZES))
def test_api_size_persists_across_restart_without_rewriting_history(revision, size):
    generation, service, v1 = revision
    path = route(generation)
    before = CandidateStore(service.store.database, service.settings.data_dir).inspect(UUID(v1['image_version_id']))
    with service.store.database.connection() as db:
        history = {name: [tuple(r) for r in db.execute('SELECT * FROM ' + name)] for name in
                   ('task_drafts', 'assets', 'c01_snapshots', 'c01_model_runs', 'image_versions')}
    with TestClient(create_app(settings(generation)), base_url='http://localhost') as client:
        task_url = '/api/tasks/' + v1['task_id']
        task = client.get(task_url).json()
        slot = next(s for s in task['slots'] if s['id'] == v1['slot_id'])
        payload = {k: slot[k] for k in ('reference_asset_id', 'output_size', 'requirements')}
        payload['output_size'] = size
        payload['expected_version'] = slot['version']
        response = client.put(path, json=payload)
        assert response.status_code == 200
        assert next(s for s in response.json()['slots'] if s['id'] == slot['id'])['output_size'] == size
        for illegal in ('1500x1500', '1536x1537', 'auto', '', 1536):
            assert client.put(path, json={**payload, 'output_size': illegal}).status_code == 422
    with TestClient(create_app(settings(generation)), base_url='http://localhost') as client:
        restored = client.get(task_url).json()
        assert next(s for s in restored['slots'] if s['id'] == slot['id'])['output_size'] == size
    assert CandidateStore(service.store.database, service.settings.data_dir).inspect(UUID(v1['image_version_id'])) == before
    with service.store.database.connection() as db:
        for name, rows in history.items():
            assert [tuple(r) for r in db.execute('SELECT * FROM ' + name)] == rows


@pytest.mark.parametrize('size', sorted(OUTPUT_SIZES))
def test_c06_actual_png_and_evidence_follow_the_model_and_output_sizes(generation, size):
    service, old, directory, original = generation
    old_before = service.service.evidence.load_snapshot(UUID(old['id']))
    decision_before = service.service.evidence.load_snapshot(UUID(old['decision_snapshot_id']))
    resize_slot(generation, size)
    snapshot = service.prepare(UUID(old['decision_snapshot_id']), directory, MODEL)
    plan = output_plan(size)
    flight = service.preflight(UUID(snapshot['id']), MODEL)
    assert flight['request']['size'] == plan['model_generation_size']
    assert flight['cost']['model_generation_size'] == plan['model_generation_size']
    assert [base64.b64decode(i['image_url'].split(',', 1)[1]) for i in flight['request']['images']] == original['blobs']
    wire = SyntheticWire(size=tuple(map(int, plan['model_generation_size'].split('x'))))
    operation = uuid4()
    result = run((service, snapshot, directory, original), wire, operation)
    assert result['status'] == 'SUCCEEDED'
    candidate = result['candidate']
    for key, value in plan.items():
        assert snapshot['output_plan'][key] == result['output_plan'][key] == result['structured_output']['content'][key] == candidate[key] == value
    body, blob = CandidateStore(service.store.database, service.settings.data_dir).read_for_slot(
        UUID(snapshot['task_id']), UUID(snapshot['slot_id']), UUID(candidate['image_version_id']))
    with Image.open(BytesIO(blob)) as image:
        assert image.size == tuple(map(int, size.split('x')))
    assert body['image']['sha256'] == sha256(blob).hexdigest()
    if size == '1536x1536':
        with Image.open(candidate['native_file']) as image:
            assert image.size == (1024, 1024)
        assert candidate['native_image']['sha256'] != candidate['image']['sha256']
        assert Path(candidate['native_file']).read_bytes() == base64.b64decode(result['actual_model_response']['data'][0]['b64_json'])
        assert 'OUTPUT PIXEL SIZE OVERRIDE' in wire.request['prompt']
        # Corrupt native evidence cannot be served as a healthy final candidate.
        native_path = Path(candidate['native_file']); native = native_path.read_bytes()
        native_path.write_bytes(b'corrupt')
        with pytest.raises((C01Error, ValueError)):
            CandidateStore(service.store.database, service.settings.data_dir).read_for_slot(
                UUID(snapshot['task_id']), UUID(snapshot['slot_id']), UUID(candidate['image_version_id']))
        native_path.write_bytes(native)
    else:
        assert 'native_file' not in candidate
    assert run((service, snapshot, directory, original), wire, operation) == result
    assert wire.calls == 1
    assert service.service.evidence.load_snapshot(UUID(old['id'])) == old_before
    assert service.service.evidence.load_snapshot(UUID(old['decision_snapshot_id'])) == decision_before


def test_square_workspace_generates_revises_and_restores_without_paid_replay(revision):
    generation, revision_service, v1 = revision
    service, old_snapshot, directory, original = generation
    v1_bytes = Path(v1['file']).read_bytes()
    resize_slot(generation, '1536x1536')
    wire = SyntheticWire(size=(1024, 1024))
    path = route(generation)
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        ready = client.get(path + '/workspace').json()['readiness']
        assert ready['can_generate'] and ready['parameters']['size'] == '1024x1024'
        operation = str(uuid4()); command = {'operation_id': operation, 'quote_token': ready['quote_token']}
        assert client.post(path + '/generations', json=command).status_code == 202
        result = finish(client, path)
        assert result['state'] == 'SUCCEEDED'
        square = result['candidates'][0]
        assert (square['image']['width'], square['image']['height']) == (1536, 1536)
        assert square['postprocess'] == 'LANCZOS_UPSCALE'
        assert square['model_generation_size'] == '1024x1024'
        # Keep the original C06 candidate selectable and unchanged.
        assert any(c['image_version_id'] == v1['image_version_id'] for c in result['candidates'])
        q = quote(client, path, square, 'TEST_ONLY: change title color, preserve all other content')
        snapshot = revision_service.service.evidence.load_snapshot(UUID(q['snapshot_id']))
        assert snapshot['output_plan'] == output_plan('1536x1536')
        assert snapshot['parameters']['size'] == '1024x1024'
        assert snapshot['context']['effective_constraints']['current_slot_size'] == '1536x1536'
        assert snapshot['context']['image_inputs'][0]['sha256'] == square['image']['sha256']
        assert revise(client, path, q).status_code == 202
        final = finish(client, path)
        v2 = final['candidates'][0]
        assert final['state'] == 'SUCCEEDED' and v2['base_image_version_id'] == square['image_version_id']
        assert (v2['image']['width'], v2['image']['height']) == (1536, 1536)
        assert v2['model_generation_size'] == '1024x1024' and not v2['pass']
        assert client.post(path + '/generations', json=command).status_code == 202
        for _ in range(2): assert client.get(path + '/workspace').status_code == 200
        assert wire.calls == 2  # Two synthetic tools, zero provider calls.
    with TestClient(create_app(settings(generation), generation_transport=wire), base_url='http://localhost') as client:
        final = client.get(path + '/workspace').json()
        assert len(final['candidates']) == 3 and wire.calls == 2
        for candidate in final['candidates'][:2]:
            with Image.open(BytesIO(client.get(candidate['url']).content)) as image:
                assert image.size == (1536, 1536)
    assert Path(v1['file']).read_bytes() == v1_bytes
    assert service.service.evidence.load_snapshot(UUID(old_snapshot['id'])) == old_snapshot


def test_size_change_does_not_bypass_other_creation_requirements(generation):
    service, old, directory, _ = generation
    resize_slot(generation, '1536x1536', requirements='changed protected requirement')
    with pytest.raises(C01Error, match='制作要求'):
        service.prepare(UUID(old['decision_snapshot_id']), directory, MODEL)


@pytest.mark.parametrize('size', sorted(OUTPUT_SIZES))
def test_revision_keeps_target_size_with_an_explicit_older_portrait_base(revision, size):
    generation, service, base = revision
    old_bytes = Path(base['file']).read_bytes()
    resize_slot(generation, size)
    snapshot = service.prepare(UUID(base['task_id']), UUID(base['slot_id']), UUID(base['image_version_id']),
                               'SYNTHETIC ONLY: retain all non-target content')
    flight = service.preflight(UUID(snapshot['id']), MODEL)
    assert base64.b64decode(flight['request']['images'][0]['image_url'].split(',', 1)[1]) == old_bytes
    assert flight['request']['size'] == output_plan(size)['model_generation_size']
    wire = SyntheticWire(size=tuple(map(int, flight['request']['size'].split('x'))))
    result = service.run(UUID(snapshot['id']), MODEL, uuid4(), confirmed=True, max_cost_usd='1.00', transport=wire)
    assert result['status'] == 'SUCCEEDED' and wire.calls == 1
    candidate = result['candidate']
    assert candidate['base_image_version_id'] == base['image_version_id']
    with Image.open(candidate['file']) as image:
        assert image.size == tuple(map(int, size.split('x')))
    assert Path(base['file']).read_bytes() == old_bytes


def test_square_rejects_wrong_native_dimensions_before_candidate_save(generation):
    service, old, directory, original = generation
    resize_slot(generation, '1536x1536')
    snapshot = service.prepare(UUID(old['decision_snapshot_id']), directory, MODEL)
    result = run((service, snapshot, directory, original), SyntheticWire(size=(1536, 1536)))
    assert result['status'] == 'FAILED' and result['error']['code'] == 'C06_OUTPUT_FORMAT_INVALID'
    assert 'candidate' not in result and result['actual_model_response']
    assert result['structured_output'] is None and 'postprocess' not in result
    assert result['output_plan'] == output_plan('1536x1536')  # Plan is not actual postprocess evidence.


def test_lanczos_result_matches_resampler_without_cropping_or_stretching():
    stream = BytesIO()
    image = Image.new('RGB', (1024, 1024), 'white')
    image.paste('red', (0, 0, 400, 1024)); image.paste('blue', (900, 0, 1024, 1024))
    image.save(stream, 'PNG')
    result = final_png(stream.getvalue(), output_plan('1536x1536'))
    with Image.open(BytesIO(result)) as actual:
        expected = image.resize((1536, 1536), Image.Resampling.LANCZOS)
        assert actual.size == (1536, 1536) and actual.tobytes() == expected.tobytes()
        assert actual.getpixel((0, 0)) == (255, 0, 0) and actual.getpixel((1535, 1535)) == (0, 0, 255)


def test_legacy_snapshots_and_candidates_remain_compatible(generation):
    service, old, _, original = generation
    snapshot = {**old, 'id': str(uuid4())}
    del snapshot['output_plan']
    service.service.evidence.save_snapshot(snapshot, original['blobs'][0])
    record = service.run(UUID(snapshot['id']), MODEL, uuid4(), confirmed=True,
                         max_cost_usd='1.00', transport=SyntheticWire())
    assert record['status'] == 'SUCCEEDED' and 'postprocess' not in record['candidate']
    CandidateStore(service.store.database, service.settings.data_dir).read_for_slot(
        UUID(snapshot['task_id']), UUID(snapshot['slot_id']), UUID(record['candidate']['image_version_id']))


def test_completed_png_publication_does_not_show_false_failure_for_old_projection(generation):
    service, _, _, original = generation
    record = run(generation, SyntheticWire())
    app = GenerationApplication(service.settings, service.service.tasks, service.store.database)
    history = app._history(original['task'], original['slot'], [])
    assert history[0]['run_id'] == record['run_id']
    assert history[0]['state'] == 'RUNNING' and history[0]['error'] is None
