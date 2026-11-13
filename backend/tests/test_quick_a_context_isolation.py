"""Quick-A 专项验收：仅临时数据库、可区分的测试图片和 OFFLINE_TRANSPORT_TEST。

按共享输入、Slot 输入、执行身份分别断言。KNOWN GAP 使用独立测试明确记录，
不修改业务实现，不把模型输入隔离等同于底层 Task 聚合完全不读取兄弟元数据。
"""
import base64
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.application import TaskService
from aigc_workbench.c01_context import C01Error, fingerprint
from aigc_workbench.c01_model import HTTPResult
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.file_storage import LocalImageFiles
from aigc_workbench.image_review import ReviewStore
from quick_a_adapters import ImageSubstitute, UnderstandingSubstitute, source_png
from test_quick_a_creation import save_slot, config, asset, new_task, submit, image_bytes
from test_quick_a_direct import app_for, overall, snapshot_for
from test_workspace_generation import finish

G1, G2 = 'GLOBAL_G1_CANARY', 'GLOBAL_G2_CANARY'
A1, A2, B1 = 'SLOT_A_REQUIREMENT_1', 'SLOT_A_REQUIREMENT_2', 'SLOT_B_REQUIREMENT_1'


class DistinctImages(ImageSubstitute):
    """每次成功响应使用不同像素，避免 V1/V2 字节相同造成“选中基础正确”的假阳性。"""
    invalid_json = False

    def send(self, payload):
        if self.invalid_json:
            self.requests.append(json.loads(payload))
            return HTTPResult(200, b'NOT_JSON_TEST_ONLY', 'test-only-invalid')
        response = super().send(payload)
        if response.status == 200:
            request = self.requests[-1]
            png = source_png((len(self.requests) * 31 % 255, 80, 140),
                             tuple(map(int, request['size'].split('x'))))
            body = json.loads(response.body)
            body['data'][0]['b64_json'] = base64.b64encode(png).decode()
            return HTTPResult(200, json.dumps(body).encode(), response.request_id)
        return response


class NoUnderstanding(UnderstandingSubstitute):
    def send(self, payload):
        self.requests.append(json.loads(payload))
        raise AssertionError('C01_TO_C05_MUST_NOT_BE_CALLED')


@pytest.fixture
def pair(config):
    images, understanding = DistinctImages(), NoUnderstanding()
    app = app_for(config, images, understanding)
    with TestClient(app, base_url='http://localhost') as client:
        path_a, task, slot_a, assets, originals, values = new_task(client, '1024x1536')
        save_slot(client,path_a,{**values, 'requirements': A1}).raise_for_status()
        ref_b, bytes_b = asset(client, task, 'marketing_reference', 'green')
        slot_b = client.post(f'/api/tasks/{task}/slots', json={
            'kind': 'scene', 'operation_id': str(uuid4())}).json()['created_slot_id']
        path_b = f'/api/tasks/{task}/slots/{slot_b}'
        save_slot(client,path_b,{**values, 'reference_asset_id': ref_b['id'], 'requirements': B1}).raise_for_status()
        overall(client, task, G1, 0)
        yield SimpleNamespace(app=app, client=client, task=task, images=images, understanding=understanding,
            paths={'A': path_a, 'B': path_b}, slots={'A': slot_a, 'B': slot_b}, product=assets[0],
            product_bytes=originals[0], refs={'A': assets[1], 'B': ref_b},
            ref_bytes={'A': originals[1], 'B': bytes_b}, requirements={'A': A1, 'B': B1})


def prepare(p, name):
    return p.app.state.generation_service._direct_service().prepare(UUID(p.task), UUID(p.slots[name]), uuid4())


def assert_context(p, snapshot, name, global_text=G1, global_version=1):
    """不比较整个 Context：分别检查共享字段、图位字段和操作身份。"""
    context, state = snapshot['context'], snapshot['context']['source_state']
    assert snapshot['task_id'] == state['task_id'] == p.task
    assert snapshot['product_asset_id'] == state['product_asset_id'] == p.product['id']
    assert state['mode'] == 'quick_a' and state['business_documents'] and state['output_plan']
    assert snapshot['slot_id'] == state['slot_id'] == p.slots[name]
    assert state['slot_state']['id'] == p.slots[name]
    assert snapshot['reference_asset_id'] == state['reference_asset_id'] == p.refs[name]['id']
    assert state['slot_state']['requirements'] == p.requirements[name]
    assert [i['asset_id'] for i in context['image_inputs']] == [p.product['id'], p.refs[name]['id']]
    assert [i['sha256'] for i in context['image_inputs']] == [sha256(p.product_bytes).hexdigest(), sha256(p.ref_bytes[name]).hexdigest()]
    requirements = context['effective_requirements']
    assert requirements['overall']['text'] == global_text and requirements['overall']['version'] == global_version
    assert requirements['overall']['scope'] == 'TASK_ALL_SLOTS'
    assert requirements['slot']['slot_id'] == p.slots[name] and requirements['slot']['scope'] == 'ONE_SLOT'
    assert requirements['slot']['text'] == p.requirements[name]
    assert requirements['slot']['content_sha256'] == fingerprint(p.requirements[name])
    assert context['capability_specific_inputs']['asset_id'] == p.product['id']
    assert snapshot['context_sha256'] == fingerprint(context)
    assert UUID(context['operation_id']) and UUID(snapshot['id'])
    other = 'B' if name == 'A' else 'A'
    prompt = snapshot['prompt']['planned_model_input']['text']
    assert p.requirements[name] in prompt and global_text in prompt
    assert p.requirements[other] not in prompt
    assert p.slots[other] not in json.dumps(context) and p.refs[other]['id'] not in json.dumps(context)


def rows_for(p, snapshots):
    with p.app.state.generation_service.database.connection() as db:
        return {s['id']: tuple(db.execute('SELECT body_json,body_sha256 FROM c01_snapshots WHERE id=?',
                                         (s['id'],)).fetchone()) for s in snapshots}


def execute(p, name):
    command = submit(p.client, p.paths[name])
    body = finish(p.client, p.paths[name])
    assert body['state'] == 'SUCCEEDED', body
    return body['candidates'][0], command


def pass_version(p, name, candidate):
    workspace = p.client.get(p.paths[name]+'/workspace').json()
    response = p.client.post(p.paths[name]+'/versions/'+candidate['image_version_id']+'/reviews', json={
        'operation_id': str(uuid4()), 'decision': 'PASS', 'reviewer': 'TEST_ONLY human',
        'feedback': 'TEST_ONLY explicit review',
        'expected_current_review_id': workspace['delivery']['id'] if workspace['delivery'] else None})
    assert response.status_code == 201, response.text
    assert response.json()['source'] == 'TEST_REVIEW'


def slot_state(p, name):
    """保存业务行及图片哈希，精确检查候选、Run、审核、交付未被其他图位改写。"""
    service = p.app.state.generation_service
    with service.database.connection() as db:
        rows = {table: [tuple(r) for r in db.execute(
            f'SELECT * FROM {table} WHERE task_id=? AND slot_id=? ORDER BY rowid',
            (p.task, p.slots[name]))] for table in ('image_versions', 'image_reviews', 'slot_deliveries', 'c06_dispatches')}
        rows['image_slots'] = [tuple(r) for r in db.execute('SELECT * FROM image_slots WHERE task_id=? AND id=?', (p.task,p.slots[name]))]
        rows['runs'] = [tuple(r) for r in db.execute(
            "SELECT r.* FROM c01_model_runs r JOIN c01_snapshots s ON s.id=r.snapshot_id "
            "WHERE s.task_id=? AND json_extract(s.body_json,'$.slot_id')=? ORDER BY r.rowid", (p.task,p.slots[name]))]
        rows['snapshots'] = [tuple(r) for r in db.execute(
            "SELECT * FROM c01_snapshots WHERE task_id=? AND json_extract(body_json,'$.slot_id')=? ORDER BY rowid",
            (p.task,p.slots[name]))]
    workspace = p.client.get(p.paths[name]+'/workspace').json()
    return {'rows': rows, 'delivery': workspace['delivery'], 'files': {
        v['image_version_id']: sha256(p.client.get(v['url']).content).hexdigest() for v in workspace['candidates']},
        'source_files': {identity: sha256(p.client.get(f'/api/tasks/{p.task}/assets/{identity}/content').content).hexdigest()
                         for identity in (p.product['id'],p.refs[name]['id'])}}


def assert_trace(p):
    service = p.app.state.generation_service
    with service.database.connection() as db:
        run_rows = db.execute('SELECT body_json FROM c01_model_runs').fetchall()
        version_rows = db.execute('SELECT body_json FROM image_versions').fetchall()
    attempts = set()
    for row in run_rows:
        run = json.loads(row['body_json'])
        snapshot = service._direct_service().service.evidence.load_snapshot(UUID(run['snapshot_id']))
        assert run['task_id'] == snapshot['task_id'] == p.task
        assert run['slot_id'] == snapshot['slot_id'] and run['slot_id'] in p.slots.values()
        assert run['capability'] == snapshot['capability'] and run['capability'] in {'C06','C07'}
        assert run['snapshot_sha256'] == fingerprint(snapshot)
        assert run['request_sha256'] == fingerprint(run['actual_test_adapter_input']['request'])
        actual_images=image_bytes(run['actual_test_adapter_input']['request'])
        expected_images=snapshot['prompt']['planned_model_input']['images']
        assert len(actual_images)==len(expected_images)
        assert [sha256(blob).hexdigest() for blob in actual_images]==[meta['sha256'] for meta in expected_images]
        assert run['planned_model_input'] in p.images.requests
        assert run['transport_label'] == 'OFFLINE_TRANSPORT_TEST' and not run['model_api_call_attempted']
        assert run['actual_model_input'] is None and run['automatic_retries'] == 0
        assert run['attempt_id'] not in attempts
        attempts.add(run['attempt_id'])
        if run['capability'] == 'C06':
            assert run['operation_id'] == snapshot['context']['operation_id']
        receipt=service.dispatcher.find(UUID(run['operation_id']))
        if receipt:
            assert receipt['task_id']==run['task_id'] and receipt['slot_id']==run['slot_id']
            assert receipt['snapshot_id']==run['snapshot_id']
    for row in version_rows:
        version = json.loads(row['body_json']); run = service.runs.inspect(UUID(version['run_id']))
        assert version['task_id'] == run['task_id'] == p.task and version['slot_id'] == run['slot_id']
        assert version['attempt_id'] == run['attempt_id'] and version['snapshot_id'] == run['snapshot_id']
        assert version['product_asset_id'] == run['product_asset_id'] == p.product['id']
        assert version['reference_asset_id'] == run['reference_asset_id']
        assert run['status'] == 'SUCCEEDED'
    assert not p.understanding.requests


def test_01_context_reference_requirement_and_request_isolation(pair, record_property):
    p = pair
    old = {name: prepare(p,name) for name in ('A','B')}
    frozen = rows_for(p,old.values())
    for name in ('A','B'):
        assert_context(p,old[name],name)
        s=old[name]
        p.app.state.generation_service._direct_service().run(UUID(s['id']),MODEL,UUID(s['context']['operation_id']),
            confirmed=True,max_cost_usd='1.00',transport=p.images)
    before_b = p.images.requests[-1]
    replacement, replacement_bytes = asset(p.client,p.task,'marketing_reference','purple')
    p.refs['A'], p.ref_bytes['A'], p.requirements['A'] = replacement, replacement_bytes, A2
    save_slot(p.client,p.paths['A'],{'reference_asset_id':replacement['id'],'output_size':'1024x1536','requirements':A2}).raise_for_status()
    new = {name: prepare(p,name) for name in ('A','B')}
    service=p.app.state.generation_service._direct_service()
    with pytest.raises(C01Error) as stale:service.preflight(UUID(old['A']['id']),MODEL)
    assert stale.value.code=='DIRECT_SOURCE_CHANGED'
    # A 的改变不应使 B 的旧操作失效。
    assert image_bytes(service.preflight(UUID(old['B']['id']),MODEL)['request'])==[p.product_bytes,p.ref_bytes['B']]
    for name in ('A','B'):
        assert_context(p,new[name],name)
        assert new[name]['id'] != old[name]['id']
        assert new[name]['context']['operation_id'] != old[name]['context']['operation_id']
        s=new[name]
        result=p.app.state.generation_service._direct_service().run(UUID(s['id']),MODEL,UUID(s['context']['operation_id']),
            confirmed=True,max_cost_usd='1.00',transport=p.images)
        assert result.get('candidate')
    for key in ('reference_asset_id','slot_state','effective_requirements','image_inputs'):
        assert new['B']['context']['source_state'][key] == old['B']['context']['source_state'][key]
    assert p.images.requests[-1]['prompt'] == before_b['prompt']
    assert image_bytes(p.images.requests[-1]) == image_bytes(before_b) == [p.product_bytes,p.ref_bytes['B']]
    assert image_bytes(p.images.requests[-2]) == [p.product_bytes,replacement_bytes]
    assert A2 in p.images.requests[-2]['prompt'] and A1 not in p.images.requests[-2]['prompt']
    assert rows_for(p,old.values()) == frozen
    assert len(p.images.requests)==4
    assert_trace(p)
    record_property('evidence',json.dumps({'task':p.task,'slots':p.slots,'old_snapshots':{k:v['id'] for k,v in old.items()},
        'new_snapshots':{k:v['id'] for k,v in new.items()},'image_calls':4,'C01_to_C05_calls':0,
        'B_effective_fields_unchanged':True,'old_snapshots_unchanged':True}))


def revise(p,name,base,text):
    q=p.client.post(p.paths[name]+'/versions/'+base['image_version_id']+'/revision-quote',json={'revision_text':text})
    assert q.status_code==200,q.text
    quote=q.json(); operation=str(uuid4())
    response=p.client.post(p.paths[name]+'/revisions',json={
        'operation_id':operation,'snapshot_id':quote['snapshot_id'],'quote_token':quote['quote_token']})
    assert response.status_code==202,response.text
    body=finish(p.client,p.paths[name]);assert body['state']=='SUCCEEDED',body
    return body['candidates'][0],quote


def test_02_c07_selected_v1_inheritance_and_sibling_protection(pair,monkeypatch,record_property):
    p=pair
    a1,_=execute(p,'A')
    a2,_=revise(p,'A',a1,'A_HISTORICAL_MODIFICATION_CANARY')
    b1,_=execute(p,'B');pass_version(p,'B',b1)
    # B 的历史修改快照提供独立污染探针，但不新增 B 候选。
    b_quote=p.client.post(p.paths['B']+'/versions/'+b1['image_version_id']+'/revision-quote',
        json={'revision_text':'B_HISTORICAL_MODIFICATION_CANARY'}).json()
    old_a={v['image_version_id']:p.client.get(v['url']).content for v in (a1,a2)}
    assert old_a[a1['image_version_id']] != old_a[a2['image_version_id']]
    b_before=slot_state(p,'B')
    frozen_versions=[snapshot_for(p.app,v)[0] for v in (a1,a2,b1)]
    frozen_before=rows_for(p,frozen_versions)
    a_version_records={v['image_version_id']:p.app.state.generation_service.candidates.inspect(UUID(v['image_version_id'])) for v in (a1,a2)}
    a_runs=[snapshot_for(p.app,v)[1] for v in (a1,a2)]
    replacement,data=asset(p.client,p.task,'marketing_reference','purple')
    p.refs['A'],p.ref_bytes['A'],p.requirements['A']=replacement,data,A2
    save_slot(p.client,p.paths['A'],{'reference_asset_id':replacement['id'],'output_size':'1024x1536','requirements':A2}).raise_for_status()
    asset_reads,version_reads,review_reads,snapshot_reads=[],[],[],[]
    original_file,original_version=LocalImageFiles.read,CandidateStore.read_for_slot
    original_review,original_snapshot=ReviewStore.list,C01EvidenceStore.load_snapshot
    def read_file(self,asset):
        asset_reads.append(str(asset.id));return original_file(self,asset)
    def read_version(self,task,slot,version):
        version_reads.append((str(slot),str(version)));return original_version(self,task,slot,version)
    def read_reviews(self,task,slot):
        review_reads.append(str(slot));return original_review(self,task,slot)
    def read_snapshot(self,identity):
        snapshot_reads.append(str(identity));return original_snapshot(self,identity)
    with monkeypatch.context() as spy:
        spy.setattr(LocalImageFiles,'read',read_file);spy.setattr(CandidateStore,'read_for_slot',read_version)
        spy.setattr(ReviewStore,'list',read_reviews);spy.setattr(C01EvidenceStore,'load_snapshot',read_snapshot)
        a3,quote=revise(p,'A',a1,'A_CURRENT_MODIFICATION_ONLY')
    request=p.images.requests[-1]
    assert image_bytes(request)==[old_a[a1['image_version_id']],p.product_bytes,data]
    assert image_bytes(request)[0]!=old_a[a2['image_version_id']]
    assert a3['base_image_version_id']==a1['image_version_id'] and a3['image_version_id'] not in old_a
    prompt=request['prompt']
    for text in (G1,A2,'A_CURRENT_MODIFICATION_ONLY'):assert text in prompt
    for forbidden in (B1,A1,'A_HISTORICAL_MODIFICATION_CANARY','B_HISTORICAL_MODIFICATION_CANARY',
                      '黑色主机','12V/24V','marketing_relationship_locks','initial_AI_content_decisions'):
        assert forbidden not in prompt
    assert p.refs['B']['id'] not in asset_reads and b1['image_version_id'] not in [v for _,v in version_reads]
    assert all(slot==p.slots['A'] for slot,_ in version_reads) and set(review_reads)=={p.slots['A']}
    assert b_quote['snapshot_id'] not in snapshot_reads
    for version in (a1,a2):
        assert p.client.get(version['url']).content==old_a[version['image_version_id']]
        assert p.app.state.generation_service.candidates.inspect(UUID(version['image_version_id']))==a_version_records[version['image_version_id']]
    for run in a_runs:assert p.app.state.generation_service.runs.inspect(UUID(run['run_id']))==run
    assert rows_for(p,frozen_versions)==frozen_before and slot_state(p,'B')==b_before
    body=p.client.get(p.paths['A']+'/workspace').json()
    assert body['delivery'] is None and not a3['pass']
    a3_snapshot,a3_run=snapshot_for(p.app,a3)
    assert a3_snapshot['decision_snapshot_id'] is None
    assert a3_snapshot['context']['base_image_version_id']==a1['image_version_id']
    assert a3_snapshot['context']['effective_constraints']['effective_requirements']['overall']['version']==1
    assert a3_snapshot['context']['effective_constraints']['current_reference_asset_id']==replacement['id']
    assert a3_snapshot['context']['historical_revision_instructions']=='NOT_REPLAYED'
    assert_trace(p)
    record_property('evidence',json.dumps({'A_V1':a1['image_version_id'],'A_V2':a2['image_version_id'],
        'A_V3':a3['image_version_id'],'actual_base':a3['base_image_version_id'],'snapshot':a3_snapshot['id'],
        'run':a3_run['run_id'],'attempt':a3_run['attempt_id'],'B_pass_and_files_unchanged':True,
        'B_image_version_revision_snapshot_reads':0,'image_hashes':[sha256(x).hexdigest() for x in image_bytes(request)]}))


@pytest.mark.xfail(strict=True,reason='KNOWN GAP: TaskService.get_draft 会加载整个 Task 的 Slot 元数据，包含兄弟要求；未实现严格不读取。')
def test_02_strict_no_sibling_requirement_metadata_read(pair,monkeypatch,record_property):
    p=pair;a1,_=execute(p,'A')
    loaded_sibling=[];original=TaskService.get_draft
    def read_task(self,identity):
        task=original(self,identity)
        loaded_sibling.extend((str(s.id),s.requirements) for s in task.slots if str(s.id)==p.slots['B'])
        return task
    with monkeypatch.context() as spy:
        spy.setattr(TaskService,'get_draft',read_task)
        snapshot=p.app.state.generation_service._revision_service().prepare(UUID(p.task),UUID(p.slots['A']),
            UUID(a1['image_version_id']),'STRICT_READ_SCOPE_CANARY')
    assert B1 not in snapshot['prompt']['planned_model_input']['text']
    record_property('evidence',json.dumps({'B_requirement_metadata_loaded':loaded_sibling,
                                        'B_requirement_in_model_prompt':False}))
    assert loaded_sibling==[], '实际读取了 B 要求元数据；有效 Context/模型 Prompt 仍未使用它。'


def test_03_global_update_applies_to_both_without_rewriting_history_or_pass(pair,record_property):
    p=pair
    versions={name:execute(p,name)[0] for name in ('A','B')}
    for name in ('A','B'):pass_version(p,name,versions[name])
    before={name:slot_state(p,name) for name in ('A','B')}
    old={name:prepare(p,name) for name in ('A','B')};old_rows=rows_for(p,old.values())
    old_quotes={name:p.client.get(p.paths[name]+'/workspace').json()['readiness']['quote_token'] for name in ('A','B')}
    overall(p.client,p.task,G2,1)
    new={name:prepare(p,name) for name in ('A','B')}
    for name in ('A','B'):
        assert_context(p,new[name],name,G2,2)
        assert_context(p,old[name],name,G1,1)
        assert new[name]['id']!=old[name]['id']
        service=p.app.state.generation_service._direct_service()
        flight=service.preflight(UUID(new[name]['id']),MODEL)
        assert image_bytes(flight['request'])==[p.product_bytes,p.ref_bytes[name]]
        assert G2 in flight['request']['prompt'] and G1 not in flight['request']['prompt']
        with pytest.raises(C01Error) as error:service.preflight(UUID(old[name]['id']),MODEL)
        assert error.value.code=='DIRECT_SOURCE_CHANGED'
        stale=p.client.post(p.paths[name]+'/generations',json={'operation_id':str(uuid4()),'quote_token':old_quotes[name]})
        assert stale.status_code==409
        after=slot_state(p,name)
        # 本场景明确创建了 G1/G2 新 Snapshot；它们应追加，而非要求快照总数不变。
        # 审核、交付、候选、Run、原图及已有快照必须分别保持原样。
        for key in ('delivery','files','source_files'):assert after[key]==before[name][key]
        for table in ('image_versions','image_reviews','slot_deliveries','c06_dispatches','image_slots','runs'):
            assert after['rows'][table]==before[name]['rows'][table]
        assert set(before[name]['rows']['snapshots'])<=set(after['rows']['snapshots'])
    assert rows_for(p,old.values())==old_rows and len(p.images.requests)==2
    assert_trace(p)
    record_property('evidence',json.dumps({'new_global_version':2,'old_global_version':1,
        'old_snapshots':{k:v['id'] for k,v in old.items()},'new_snapshots':{k:v['id'] for k,v in new.items()},
        'both_old_preflights':'DIRECT_SOURCE_CHANGED','both_stale_quotes':409,
        'existing_passes_unchanged':True,'automatic_calls_after_update':0}))


@pytest.mark.parametrize('failure',['http','timeout','invalid_json'])
def test_04_execution_failure_and_pass_are_slot_scoped(pair,failure,record_property):
    p=pair
    a1,_=execute(p,'A');pass_version(p,'A',a1)
    b1,_=execute(p,'B');pass_version(p,'B',b1)
    b_before=slot_state(p,'B');a_before=slot_state(p,'A')
    p.images.fail=failure=='http';p.images.unknown=failure=='timeout';p.images.invalid_json=failure=='invalid_json'
    before_calls=len(p.images.requests)
    command=submit(p.client,p.paths['A']);body=finish(p.client,p.paths['A'])
    assert len(p.images.requests)==before_calls+1 and not p.understanding.requests
    assert body['state']==('OUTCOME_UNKNOWN' if failure=='timeout' else 'FAILED')
    assert [v['image_version_id'] for v in body['candidates']]==[a1['image_version_id']]
    assert body['delivery']==a_before['delivery'] and slot_state(p,'B')==b_before
    assert p.client.post(p.paths['A']+'/generations',json=command).status_code==202
    assert len(p.images.requests)==before_calls+1
    run=p.app.state.generation_service.runs.inspect(UUID(body['executions'][0]['run_id']))
    assert run['task_id']==p.task and run['slot_id']==p.slots['A']
    assert run['status']!='SUCCEEDED'
    expected={'http':'MODEL_HTTP_ERROR','timeout':'MODEL_OUTCOME_UNKNOWN','invalid_json':'MODEL_RESPONSE_INVALID_JSON'}
    assert run['error']['code']==expected[failure]
    p.images.fail=p.images.unknown=p.images.invalid_json=False
    if failure=='timeout':
        assert not body['readiness']['can_generate']
        other=p.client.post(p.paths['A']+'/generations',json={'operation_id':str(uuid4()),
            'quote_token':body['readiness']['quote_token']})
        assert other.status_code==409 and len(p.images.requests)==before_calls+1
    else:
        candidate,_=execute(p,'A')
        assert not candidate['pass']
        assert p.client.get(p.paths['A']+'/workspace').json()['delivery']==a_before['delivery']
        reject=p.client.post(p.paths['A']+'/versions/'+candidate['image_version_id']+'/reviews',json={
            'operation_id':str(uuid4()),'decision':'REJECT','reviewer':'TEST_ONLY human','feedback':'TEST_ONLY reject',
            'expected_current_review_id':a_before['delivery']['id']})
        assert reject.status_code==201
    assert p.client.get(p.paths['A']+'/workspace').json()['delivery']==a_before['delivery']
    assert slot_state(p,'B')==b_before
    assert p.client.get(a1['url']).content and p.client.get(b1['url']).content
    assert_trace(p)
    record_property('evidence',json.dumps({'failure':failure,'run_id':run['run_id'],'attempt_id':run['attempt_id'],
        'snapshot_id':run['snapshot_id'],'status':run['status'],'new_candidates_from_failure':0,
        'request_count_for_failed_operation':1,'C01_to_C05_calls':0,'A_and_B_pass_unchanged':True,
        'B_business_rows_and_files_unchanged':True}))


def test_slot_save_stale_page_is_rejected_without_overwrite(pair,record_property):
    p=pair
    def read_page():
        task=p.client.get('/api/tasks/'+p.task).json()
        return deepcopy(next(s for s in task['slots'] if s['id']==p.slots['A']))
    page_one,page_two=read_page(),read_page()
    def payload(page,text):
        return {'reference_asset_id':page['reference_asset_id'],'output_size':page['output_size'],'requirements':text,'expected_version':page['version']}
    fresh=p.client.put(p.paths['A'],json=payload(page_one,'NEWER_PAGE_REQUIREMENT'))
    assert fresh.status_code==200 and read_page()['requirements']=='NEWER_PAGE_REQUIREMENT'
    stale=p.client.put(p.paths['A'],json=payload(page_two,'STALE_PAGE_REQUIREMENT'))
    # 旧页面必须明确收到 409，不能用成功响应掩盖对新要求的覆盖。
    assert stale.status_code==409 and read_page()['requirements']=='NEWER_PAGE_REQUIREMENT'
    record_property('evidence',json.dumps({'status':'PASS','first_write':200,'stale_write':409,
                                        'newer_requirement_was_overwritten':False}))
