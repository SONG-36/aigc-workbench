"""动态 N 图真实 HTTP/SQLite 调度验收；仅 Fake 模型，不访问供应商。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event, Lock
import time
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.config import Settings
from aigc_workbench.c01_context import fingerprint
from aigc_workbench.persistence import SQLiteTaskStore
from quick_a_adapters import ImageSubstitute
from test_quick_a_creation import config, new_task, asset, save_slot, submit, image_bytes
from test_quick_a_context_isolation import NoUnderstanding
from test_workspace_generation import finish


class ConcurrentImages(ImageSubstitute):
    def __init__(self, limit, inject_failures):
        super().__init__()
        self.limit=limit;self.inject_failures=inject_failures;self.lock=Lock();self.active=0;self.peak=0;self.batch=False
        self.gate=Event();self.gate.set();self.saturated=Event();self.batch_requests=[]

    def send(self,payload):
        request=json.loads(payload)
        if not self.batch:return super().send(payload)
        with self.lock:
            self.active+=1;self.peak=max(self.peak,self.active);self.batch_requests.append(request)
            if self.active==self.limit:self.saturated.set()
        try:
            assert self.gate.wait(10),'test gate was not released'
            time.sleep(.03)
            if self.inject_failures and 'SLOT_4_ONLY' in request['prompt']:
                from aigc_workbench.c01_model import HTTPResult
                return HTTPResult(429,b'{"error":{"code":"TEST_ONLY_RATE_LIMIT"}}','test-only')
            if self.inject_failures and 'SLOT_7_ONLY' in request['prompt']:raise TimeoutError('TEST_ONLY unknown outcome')
            return super().send(payload)
        finally:
            with self.lock:self.active-=1


@pytest.mark.parametrize('count,workers,inject_failures,first_kind',[
    (1,3,False,'main'),(4,3,False,'dimensions'),(10,3,False,'main'),(10,3,True,'main'),
    (10,2,True,'main'),(13,3,False,'dimensions'),(17,2,False,'main')])
def test_dynamic_slots_one_submission_bounded_parallel_independent_results(config,count,workers,inject_failures,first_kind,monkeypatch,record_property):
    # 过期费率和极高估算仍不能拦截 Workspace 的主动操作。
    import aigc_workbench.c06_generation as c06
    original=c06.estimate_image_cost
    monkeypatch.setattr(c06,'PRICING_CHECKED','2000-01-01')
    monkeypatch.setattr(c06,'estimate_image_cost',lambda *a,**k:{**original(*a,**k),'planning_envelope_usd':'999.00'})
    settings=config if workers==3 else replace(config,generation_max_workers=workers)
    expected_active=min(count,workers)
    images,understanding=ConcurrentImages(expected_active,inject_failures),NoUnderstanding()
    app=create_app(settings,generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        first,task,first_id,_,originals,values=new_task(client,'1024x1024',kind=first_kind)
        slots=[first_id];paths=[first];refs=[originals[1]]
        save_slot(client,first,{**values,'requirements':'SLOT_0_ONLY'}).raise_for_status()
        for index in range(1,count):
            ref,blob=asset(client,task,'marketing_reference',f'#{index%256:02x}4466')
            slot=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
            path=f'/api/tasks/{task}/slots/{slot}'
            save_slot(client,path,{**values,'reference_asset_id':ref['id'],'requirements':f'SLOT_{index}_ONLY'}).raise_for_status()
            slots.append(slot);paths.append(path);refs.append(blob)
        client.put(f'/api/tasks/{task}/overall-requirement',json={'text':'GLOBAL_SHARED','expected_version':0,
                    'operation_id':str(uuid4())}).raise_for_status()
        submit(client,first);old=finish(client,first)['candidates'][0]
        review=client.post(first+'/versions/'+old['image_version_id']+'/reviews',json={
            'operation_id':str(uuid4()),'decision':'PASS','reviewer':'TEST_ONLY','feedback':'TEST_ONLY',
            'expected_current_review_id':None});assert review.status_code==201
        old_delivery=client.get(first+'/workspace').json()['delivery'];old_png=client.get(old['url']).content
        quotes=[client.get(path+'/workspace').json()['readiness']['quote_token'] for path in paths]
        operations=[str(uuid4()) for _ in slots]
        commands=[{'operation_id':operation,'quote_token':quote} for operation,quote in zip(operations,quotes)]
        images.batch=True;images.gate.clear()
        try:
            with ThreadPoolExecutor(count) as pool:
                responses=list(pool.map(lambda entry:client.post(entry[0]+'/generations',json=entry[1]),zip(paths,commands)))
            assert all(r.status_code==202 for r in responses),[r.text for r in responses]
            assert images.saturated.wait(4) and images.peak==expected_active
            assert app.state.generation_service.dispatcher.max_workers==workers
            states=[client.get(path+'/workspace').json()['state'] for path in paths]
            assert states.count('RUNNING')==expected_active and states.count('QUEUED')==count-expected_active
            # 远程等待被 Fake gate 阻塞时，SQLite 仍能完成独立业务写入。
            independent=client.post('/api/tasks',json={'title':'TEST_ONLY SQLite write during model wait','operation_id':str(uuid4())})
            assert independent.status_code==201
            with ThreadPoolExecutor(2) as pool:
                duplicates=list(pool.map(lambda _:client.post(first+'/generations',json=commands[0]),range(2)))
            assert all(r.status_code==202 for r in duplicates)
            assert client.post(first+'/generations',json={'operation_id':str(uuid4()),'quote_token':quotes[0]}).status_code==409
        finally:images.gate.set()
        results=[finish(client,path) for path in paths]
        assert len(images.batch_requests)==count and images.peak==expected_active and not understanding.requests
        errors={4,7} if inject_failures else set()
        successes=count-len(errors)
        assert sum(r['state']=='SUCCEEDED' for r in results)==successes
        if inject_failures:assert results[4]['state']=='FAILED' and results[7]['state']=='OUTCOME_UNKNOWN'
        candidates=[];runs=[]
        for index,(result,path) in enumerate(zip(results,paths)):
            execution=next(e for e in result['executions'] if e['operation_id']==operations[index])
            run=app.state.generation_service.runs.inspect(UUID(execution['run_id']));runs.append(run)
            snapshot=app.state.generation_service._direct_service().service.evidence.load_snapshot(UUID(run['snapshot_id']))
            assert run['task_id']==task and run['slot_id']==slots[index] and run['capability']=='C06'
            assert snapshot['context']['effective_requirements']['overall']['version']==1
            assert snapshot['context']['effective_requirements']['slot']['text']==f'SLOT_{index}_ONLY'
            assert image_bytes(run['planned_model_input'])==[originals[0],refs[index]]
            assert run['request_sha256']==fingerprint(run['actual_test_adapter_input']['request'])
            assert 'GLOBAL_SHARED' in run['planned_model_input']['prompt']
            for other in range(count):
                assert (f'SLOT_{other}_ONLY' in run['planned_model_input']['prompt'])==(other==index)
            assert run['user_max_cost_usd'] is None and run['cost_preflight']['planning_envelope_usd']=='999.00'
            assert not run['model_api_call_attempted'] and run['automatic_retries']==0
            if index not in errors:
                candidate=next(v for v in result['candidates'] if v['run_id']==run['run_id']);candidates.append(candidate)
                assert candidate['task_id']==task and candidate['slot_id']==slots[index]
                assert candidate['image_version_id']!=old['image_version_id'] and not candidate['pass']
            else:assert not result['candidates']
        for field in ('run_id','attempt_id','snapshot_id','operation_id'):
            assert len({r[field] for r in runs})==count
        assert len({v['image_version_id'] for v in candidates})==successes
        assert client.get(first+'/workspace').json()['delivery']==old_delivery and client.get(old['url']).content==old_png
        if inject_failures:
            assert client.post(paths[7]+'/generations',json=commands[7]).status_code==202
            assert len(images.batch_requests)==count
            assert client.post(paths[7]+'/generations',json={'operation_id':str(uuid4()),'quote_token':quotes[7]}).status_code==409
        record_property('parallel_result',json.dumps({'slots':count,'accepted':count,'configured_workers':workers,'first_kind':first_kind,
            'observed_peak':images.peak,'success':successes,'failed':int(inject_failures),'outcome_unknown':int(inject_failures),'new_versions':successes,
            'C01_to_C05_calls':0,'model_attempts':count,'sqlite_write_while_waiting':True,'old_pass_unchanged':True}))


def test_two_pages_concurrent_slot_save_returns_one_conflict(config):
    with TestClient(create_app(config,generation_transport=ImageSubstitute()),base_url='http://localhost') as client:
        path,task,slot,_,_,values=new_task(client)
        page=next(s for s in client.get('/api/tasks/'+task).json()['slots'] if s['id']==slot)
        def write(text):return client.put(path,json={**values,'expected_version':page['version'],'requirements':text})
        with ThreadPoolExecutor(2) as pool:responses=list(pool.map(write,['PAGE_ONE','PAGE_TWO']))
        assert sorted(r.status_code for r in responses)==[200,409]
        current=next(s for s in client.get('/api/tasks/'+task).json()['slots'] if s['id']==slot)
        assert current['version']==page['version']+1 and current['requirements'] in {'PAGE_ONE','PAGE_TWO'}
        assert '已变化' in next(r for r in responses if r.status_code==409).json()['error']['message']


def test_v11_additive_migration_keeps_old_slot_and_requirements(config):
    database=SQLiteTaskStore(config.database_path)
    with TestClient(create_app(config),base_url='http://localhost') as client:
        _,task,slot,*_=new_task(client)
    with database.connection(write=True) as db:
        db.execute('ALTER TABLE image_slots DROP COLUMN version');db.execute('PRAGMA user_version=10')
        before=tuple(db.execute('SELECT * FROM image_slots WHERE id=?',(slot,)).fetchone())
    database.initialize()
    with database.connection() as db:
        row=tuple(db.execute('SELECT * FROM image_slots WHERE id=?',(slot,)).fetchone())
        assert row[:-1]==before and row[-1]==0
        assert db.execute('PRAGMA user_version').fetchone()[0]==14
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('value',['0','17','invalid'])
def test_worker_configuration_rejects_invalid_limits(monkeypatch,value):
    monkeypatch.setenv('AIGC_GENERATION_MAX_WORKERS',value)
    with pytest.raises(ValueError):Settings.from_environment()


def test_worker_configuration_reads_environment(monkeypatch):
    monkeypatch.setenv('AIGC_GENERATION_MAX_WORKERS','2')
    assert Settings.from_environment().generation_max_workers==2


@pytest.mark.parametrize('endpoint',['generations','revisions'])
def test_workspace_ignores_legacy_fee_approval_and_replays_old_policy(config,monkeypatch,endpoint):
    import aigc_workbench.c06_generation as c06
    original=c06.estimate_image_cost
    monkeypatch.setattr(c06,'PRICING_CHECKED','2000-01-01')
    monkeypatch.setattr(c06,'estimate_image_cost',lambda *a,**k:{**original(*a,**k),'planning_envelope_usd':'999.00'})
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        path,*_=new_task(client)
        if endpoint=='revisions':
            submit(client,path);base=finish(client,path)['candidates'][0]
            quote=client.post(path+'/versions/'+base['image_version_id']+'/revision-quote',
                json={'revision_text':'TEST_ONLY new background'}).json()
            assert quote['max_cost_usd'] is None
        else:quote=client.get(path+'/workspace').json()['readiness']
        command={'operation_id':str(uuid4()),'quote_token':quote['quote_token'],
                 'confirm_paid':False,'max_cost_usd':'0.01'}
        if endpoint=='revisions':command['snapshot_id']=quote['snapshot_id']
        response=client.post(path+'/'+endpoint,json=command)
        assert response.status_code==202,response.text
        assert finish(client,path)['state']=='SUCCEEDED'
        calls=len(images.requests)
        receipt=app.state.generation_service.dispatcher.find(UUID(command['operation_id']))
        assert receipt['max_cost_usd']=='UNRESTRICTED_WORKSPACE'
        # 模拟升级前保留下来的费用策略及输入指纹；旧操作只读取原回执，绝不再次付费。
        with app.state.generation_service.database.connection(write=True) as db:
            db.execute('UPDATE c06_dispatches SET max_cost_usd=?,quote_token=? WHERE operation_id=?',
                       ('1.00','b'*64,command['operation_id']))
        command['quote_token']='b'*64
        assert client.post(path+'/'+endpoint,json=command).status_code==202
        assert len(images.requests)==calls
        assert client.post(path+'/'+endpoint,json={**command,'quote_token':'a'*64}).status_code==409
