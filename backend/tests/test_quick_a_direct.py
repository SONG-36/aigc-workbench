"""默认直生图的业务回归；真实 HTTP/SQLite/文件 + 显式 Fake 响应，禁止外网。"""
import base64
from dataclasses import replace
from hashlib import sha256
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error, fingerprint
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.quick_a_direct import VERSION
from quick_a_adapters import UnderstandingSubstitute, ImageSubstitute
from test_quick_a_creation import save_slot, config, new_task, asset, submit, image_bytes, application as legacy_app
from test_workspace_generation import finish


def app_for(config, images, understanding=None):
    return create_app(config, generation_transport=images, understanding_transport=understanding)


def snapshot_for(app, candidate):
    service = app.state.generation_service
    run = service.runs.inspect(UUID(candidate['run_id']))
    return service._direct_service().service.evidence.load_snapshot(UUID(run['snapshot_id'])), run


def overall(client, task, text, version):
    response = client.put(f'/api/tasks/{task}/overall-requirement',json={
        'text':text, 'expected_version':version, 'operation_id':str(uuid4())})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('size', ['1024x1536', '1536x1536'])
def test_default_one_image_call_and_frozen_trace_replay_restart(config, size):
    images, understanding = ImageSubstitute(), UnderstandingSubstitute()
    app = app_for(config, images, understanding)
    with TestClient(app,base_url='http://localhost') as client:
        path, task, slot, assets, originals, _ = new_task(client,size)
        # Readiness/read/refresh 不落快照，不进行任何模型调用。
        for _ in range(2): assert client.get(path+'/workspace').json()['readiness']['can_generate']
        with app.state.generation_service.database.connection() as db:
            assert db.execute('SELECT count(*) FROM c01_snapshots').fetchone()[0] == 0
        assert not images.requests and not understanding.requests
        command = submit(client,path); body = finish(client,path)
        assert body['state']=='SUCCEEDED' and len(body['candidates'])==1
        candidate = body['candidates'][0]; snapshot, run = snapshot_for(app,candidate)
        assert len(images.requests)==1 and not understanding.requests
        assert image_bytes(images.requests[0]) == list(originals)
        assert candidate['task_id']==task and candidate['slot_id']==slot
        assert snapshot['context']['version']==VERSION and snapshot['capability']=='C06'
        assert snapshot['context']['operation_id']==command['operation_id']
        assert snapshot['decision_snapshot_id'] is None and snapshot['upstream_run_ids']==[]
        assert app.state.generation_service.candidates.inspect(UUID(candidate['image_version_id']))['decision_snapshot_id'] is None
        assert snapshot['context_sha256']==fingerprint(snapshot['context'])
        assert snapshot['context']['effective_requirements']['overall']['version']==0
        slot_requirement=snapshot['context']['effective_requirements']['slot']
        assert slot_requirement['version']==1 and slot_requirement['version_policy']=='SLOT_VERSION_AND_CONTENT_SHA256'
        assert slot_requirement['content_sha256']==fingerprint(slot_requirement['text'])
        assert run['attempt_id'] and run['snapshot_id']==snapshot['id']
        assert run['actual_test_adapter_input']['request']==images.requests[0]
        assert run['request_sha256']==fingerprint(images.requests[0])
        assert not run['model_api_call_attempted'] and run['actual_model_input'] is None
        assert snapshot['source_provenance'][0]['sha256']==sha256(originals[0]).hexdigest()
        assert snapshot['source_provenance'][1]['sha256']==sha256(originals[1]).hexdigest()
        assert '黑色主机' not in images.requests[0]['prompt'] and '12V/24V' not in images.requests[0]['prompt']
        assert not candidate['pass'] and body['delivery'] is None
        with app.state.generation_service.database.connection() as db:
            assert db.execute('SELECT count(*) FROM c01_model_runs').fetchone()[0]==1
        assert client.post(path+'/generations',json=command).status_code==202
        assert len(images.requests)==1
    with TestClient(app_for(config,images),base_url='http://localhost') as client:
        restored=client.get(path+'/workspace').json()
        assert restored['candidates'][0]['image_version_id']==candidate['image_version_id']
        assert len(images.requests)==1


def test_two_slots_never_share_reference_or_slot_text(config):
    images=ImageSubstitute()
    with TestClient(app_for(config,images),base_url='http://localhost') as client:
        path,task,slot,_,originals,values=new_task(client)
        values['requirements']='SLOT_ONE_ONLY';save_slot(client,path,values).raise_for_status()
        overall(client,task,'GLOBAL_SHARED',0)
        other_ref,other_bytes=asset(client,task,'marketing_reference','green')
        other=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
        other_path=f'/api/tasks/{task}/slots/{other}'
        save_slot(client,other_path,{**values,'reference_asset_id':other_ref['id'],'requirements':'SLOT_TWO_ONLY'}).raise_for_status()
        submit(client,path);assert finish(client,path)['state']=='SUCCEEDED'
        submit(client,other_path);assert finish(client,other_path)['state']=='SUCCEEDED'
        first,second=images.requests
        assert image_bytes(first)==list(originals) and image_bytes(second)==[originals[0],other_bytes]
        assert 'SLOT_ONE_ONLY' in first['prompt'] and 'SLOT_TWO_ONLY' not in first['prompt']
        assert 'SLOT_TWO_ONLY' in second['prompt'] and 'SLOT_ONE_ONLY' not in second['prompt']
        assert 'GLOBAL_SHARED' in first['prompt'] and 'GLOBAL_SHARED' in second['prompt']


def test_current_global_version_and_stale_quote_and_snapshot(config):
    images=ImageSubstitute();app=app_for(config,images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,*_=new_task(client)
        overall(client,task,'GLOBAL_V1_ONLY',0)
        submit(client,path);first=finish(client,path)['candidates'][0]
        before,run=snapshot_for(app,first)
        old_quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        overall(client,task,'GLOBAL_V2_ONLY',1)
        assert client.post(path+'/generations',json={'operation_id':str(uuid4()),'quote_token':old_quote}).status_code==409
        with pytest.raises(C01Error,match='已变化'):
            app.state.generation_service._direct_service().preflight(UUID(before['id']),MODEL)
        submit(client,path);second=finish(client,path)['candidates'][0]
        after,_=snapshot_for(app,second)
        assert before['id']!=after['id'] and after['context']['effective_requirements']['overall']['version']==2
        assert 'GLOBAL_V2_ONLY' in images.requests[-1]['prompt'] and 'GLOBAL_V1_ONLY' not in images.requests[-1]['prompt']
        assert app.state.generation_service._direct_service().service.evidence.load_snapshot(UUID(before['id']))==before
        assert app.state.generation_service.runs.inspect(UUID(run['run_id']))==run


@pytest.mark.parametrize('change',['product','reference','slot'])
def test_source_changes_before_dispatch_refuse_old_context(config, change):
    app=app_for(config,ImageSubstitute())
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,_,values=new_task(client)
        service=app.state.generation_service._direct_service()
        snapshot=service.prepare(UUID(task),UUID(slot),uuid4())
        if change=='product':asset(client,task,'product_image','yellow')
        elif change=='reference':
            ref,_=asset(client,task,'marketing_reference','purple')
            save_slot(client,path,{**values,'reference_asset_id':ref['id']}).raise_for_status()
        else:save_slot(client,path,{**values,'requirements':'CHANGED_SLOT_ONLY'}).raise_for_status()
        with pytest.raises(C01Error) as error:service.preflight(UUID(snapshot['id']),MODEL)
        assert error.value.code=='DIRECT_SOURCE_CHANGED'


@pytest.mark.parametrize('failure',['http','timeout','invalid_json'])
def test_failures_have_no_fake_version_and_no_automatic_retry(config,failure):
    class Broken(ImageSubstitute):
        def send(self,payload):
            if failure=='invalid_json':
                self.requests.append(json.loads(payload))
                from aigc_workbench.c01_model import HTTPResult
                return HTTPResult(200,b'not json','test-only')
            return super().send(payload)
    images=Broken(fail=failure=='http',unknown=failure=='timeout');app=app_for(config,images)
    with TestClient(app,base_url='http://localhost') as client:
        path,*_=new_task(client);command=submit(client,path);body=finish(client,path)
        assert body['state']==('OUTCOME_UNKNOWN' if failure=='timeout' else 'FAILED')
        assert body['candidates']==[] and body['delivery'] is None
        assert client.post(path+'/generations',json=command).status_code==202
        assert len(images.requests)==1
        run=app.state.generation_service.runs.inspect(UUID(body['executions'][0]['run_id']))
        assert run['automatic_retries']==0 and run['error']


def test_new_candidate_and_reject_never_replace_current_pass(config):
    images=ImageSubstitute()
    with TestClient(app_for(config,images),base_url='http://localhost') as client:
        path,*_=new_task(client);submit(client,path);v1=finish(client,path)['candidates'][0]
        review={'operation_id':str(uuid4()),'decision':'PASS','reviewer':'TEST_ONLY human',
                'feedback':'TEST_ONLY','expected_current_review_id':None}
        first=client.post(path+'/versions/'+v1['image_version_id']+'/reviews',json=review)
        assert first.status_code==201
        delivery=client.get(path+'/workspace').json()['delivery'];old_bytes=client.get(v1['url']).content
        submit(client,path);body=finish(client,path);v2=body['candidates'][0]
        assert v2['image_version_id']!=v1['image_version_id'] and not v2['pass'] and body['delivery']==delivery
        review.update(operation_id=str(uuid4()),decision='REJECT',expected_current_review_id=delivery['id'])
        assert client.post(path+'/versions/'+v2['image_version_id']+'/reviews',json=review).status_code==201
        assert client.get(path+'/workspace').json()['delivery']==delivery
        assert client.get(v1['url']).content==old_bytes


def test_direct_candidate_can_revise_selected_older_base_with_current_requirements(config):
    images=ImageSubstitute();app=app_for(config,images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,*_=new_task(client);submit(client,path);v1=finish(client,path)['candidates'][0]
        submit(client,path);assert finish(client,path)['candidates'][0]['image_version_id']!=v1['image_version_id']
        base=client.get(v1['url']).content
        overall(client,task,'CURRENT_GLOBAL_REVISION',0)
        quote=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'CURRENT_CHANGE_ONLY'})
        assert quote.status_code==200,quote.text
        q=quote.json()
        overall(client,task,'CURRENT_GLOBAL_V2',1)
        command={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}
        assert client.post(path+'/revisions',json=command).status_code==409
        q=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'CURRENT_CHANGE_ONLY'}).json()
        command.update(snapshot_id=q['snapshot_id'],quote_token=q['quote_token'])
        assert client.post(path+'/revisions',json=command).status_code==202
        body=finish(client,path);version=body['candidates'][0]
        assert body['state']=='SUCCEEDED' and version['base_image_version_id']==v1['image_version_id']
        assert len(images.requests)==3 and len(images.requests[-1]['images'])==3
        assert image_bytes(images.requests[-1])[0]==base
        prompt=images.requests[-1]['prompt']
        assert 'CURRENT_GLOBAL_V2' in prompt and 'CURRENT_CHANGE_ONLY' in prompt
        for forbidden in ['黑色主机','12V/24V','GLOBAL_REVISION','marketing_relationship_locks']:
            assert forbidden not in prompt
        assert client.get(v1['url']).content==base


def test_legacy_chain_history_and_pass_remain_readable_in_default_direct_app(config):
    images,understanding=ImageSubstitute(),UnderstandingSubstitute()
    with TestClient(legacy_app(config,understanding,images),base_url='http://localhost') as client:
        path,*_=new_task(client);submit(client,path);old=finish(client,path)['candidates'][0]
        old_png=client.get(old['url']).content
    previous_calls=len(understanding.requests)
    with TestClient(app_for(config,images,understanding),base_url='http://localhost') as client:
        assert client.get(old['url']).content==old_png
        submit(client,path);body=finish(client,path)
        assert len(body['candidates'])==2 and len(understanding.requests)==previous_calls==3
        assert body['candidates'][0]['image_version_id']!=old['image_version_id']
        assert client.get(old['url']).content==old_png
        quote=client.post(path+'/versions/'+old['image_version_id']+'/revision-quote',json={'revision_text':'LEGACY_BASE_NEW_EDIT'})
        assert quote.status_code==200
        evidence=client.app.state.generation_service._revision_service().service.evidence
        snap=evidence.load_snapshot(UUID(quote.json()['snapshot_id']))
        assert '黑色主机' not in snap['prompt']['planned_model_input']['text']


def test_debug_eight_steps_are_ordered_and_redact_private_values(config,monkeypatch,capsys):
    monkeypatch.setenv('AIGC_DEBUG_QUICK_A','1')
    secret='TEST_ONLY_SECRET_FOR_DIRECT_LOGS';monkeypatch.setenv('OPENAI_API_KEY',secret)
    images=ImageSubstitute()
    with TestClient(app_for(config,images),base_url='http://localhost') as client:
        path,task,slot,_,_,values=new_task(client)
        values['requirements']=secret+' Bearer HIDDEN_TOKEN data:image/png;base64,QUJDREVGRw=='
        save_slot(client,path,values).raise_for_status()
        command=submit(client,path);assert finish(client,path)['state']=='SUCCEEDED'
        assert secret in images.requests[0]['prompt']  # 日志脱敏不能改写真实输入。
    output=capsys.readouterr().err
    headers=[line for line in output.splitlines() if line.startswith('[')]
    assert [line.split(']')[0] for line in headers]==['['+str(i) for i in range(1,9)]
    assert all(f'Task={task}' in h and f'Slot={slot}' in h and f"Operation={command['operation_id']}" in h
               and 'elapsed_ms=' in h and 'status=' in h for h in headers)
    for hidden in (secret,'HIDDEN_TOKEN','QUJDREVGRw==',images.requests[0]['images'][0]['image_url']):
        assert hidden not in output


@pytest.mark.parametrize('failure',['before_request','transport','save'])
def test_debug_failure_names_stage_exception_dispatch_and_save(config,monkeypatch,capsys,failure):
    monkeypatch.setenv('AIGC_DEBUG_QUICK_A','1')
    images=ImageSubstitute(unknown=failure=='transport');app=app_for(config,images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,*_=new_task(client)
        if failure=='before_request':
            service=app.state.generation_service._direct_service()
            with pytest.raises(C01Error):service.prepare(UUID(task),uuid4(),uuid4())
        else:
            if failure=='save':
                def broken(*a,**k):raise OSError('TEST_ONLY disk unavailable')
                monkeypatch.setattr(CandidateStore,'save',broken)
            submit(client,path);body=finish(client,path)
            assert body['state'] in {'FAILED','OUTCOME_UNKNOWN'} and not body['candidates']
    output=capsys.readouterr().err
    assert 'status=FAILED' in output and 'exception_type' in output and 'message' in output
    assert '"model_call_attempted": false' in output and '"image_version_saved": false' in output
    assert ('TimeoutError' if failure=='transport' else 'OSError' if failure=='save' else 'C01Error') in output


def test_debug_distinguishes_attempted_unknown_from_pre_dispatch_failure(config,monkeypatch,capsys):
    # 反事实传输标签只用于验证“可能计费”日志；send 仍是本地 Fake，绝不访问供应商。
    class CounterfactualTimeout(ImageSubstitute):
        label='REAL_PROVIDER_TRANSPORT'
    monkeypatch.setenv('AIGC_DEBUG_QUICK_A','1')
    images=CounterfactualTimeout(unknown=True)
    with TestClient(app_for(config,images),base_url='http://localhost') as client:
        path,*_=new_task(client);submit(client,path);body=finish(client,path)
        assert body['state']=='OUTCOME_UNKNOWN' and not body['candidates'] and len(images.requests)==1
    output=capsys.readouterr().err
    assert '"model_call_attempted": true' in output and '"fee_may_have_been_incurred": true' in output
    assert '"image_version_saved": false' in output and 'TimeoutError' in output


@pytest.mark.parametrize('environment,flag',[('development','0'),('pilot','1'),('production','1')])
def test_debug_requires_local_explicit_opt_in(config,monkeypatch,capsys,environment,flag):
    from aigc_workbench.quick_a_direct import _TerminalLog
    monkeypatch.setenv('AIGC_DEBUG_QUICK_A',flag)
    log=_TerminalLog(replace(config,environment=environment),'task','slot','op')
    log.done('PRIVATE_CANARY');log.failure(ValueError('PRIVATE_CANARY'))
    assert capsys.readouterr().err==''
