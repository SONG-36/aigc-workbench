"""Quick-B 本地 HTTP/SQLite/文件验收：只用可辨识图片及 Fake 模型，禁止外网。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error, fingerprint
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.persistence import SQLiteTaskStore
from aigc_workbench.quickb import VERSION, QuickBService
from quick_a_adapters import ImageSubstitute
from test_quick_a_creation import config, asset, save_slot, submit, image_bytes
from test_quick_a_direct import overall, snapshot_for
from test_quick_a_context_isolation import NoUnderstanding, DistinctImages
from test_quick_a_parallel import ConcurrentImages
from test_workspace_generation import finish


def new_b(client, size='1024x1024', kind='main', text='SLOT_0_ONLY'):
    task=client.post('/api/tasks',json={'title':'TEST_ONLY Quick-B','mode':'quick_b',
        'operation_id':str(uuid4())});assert task.status_code==201,task.text
    task=task.json()['id']
    product,p=asset(client,task,'product_image','#c63243')
    reference,r=asset(client,task,'marketing_reference','#1263b4')
    slot=client.post(f'/api/tasks/{task}/slots',json={'kind':kind,'operation_id':str(uuid4())}).json()['created_slot_id']
    path=f'/api/tasks/{task}/slots/{slot}'
    values={'reference_asset_id':reference['id'],'output_size':size,'requirements':text}
    save_slot(client,path,values).raise_for_status()
    return path,task,slot,(product,reference),(p,r),values


def pass_version(client,path,version):
    client.post(path+'/versions/'+version['image_version_id']+'/reviews',json={
        'operation_id':str(uuid4()),'decision':'PASS','reviewer':'TEST_ONLY','feedback':'keep',
        'expected_current_review_id':None}).raise_for_status()
    return client.get(path+'/workspace').json()['delivery']


def test_create_mode_replay_and_missing_sources_do_not_call_models(config):
    images,understanding=ImageSubstitute(),NoUnderstanding()
    with TestClient(create_app(config,generation_transport=images,understanding_transport=understanding),base_url='http://localhost') as client:
        command={'title':'Quick-B create','mode':'quick_b','operation_id':str(uuid4())}
        created=client.post('/api/tasks',json=command);assert created.status_code==201
        task=created.json()['id'];assert created.json()['mode']=='quick_b'
        assert client.post('/api/tasks',json=command).json()['id']==task
        assert client.post('/api/tasks',json={**command,'mode':'quick_a'}).status_code==409
        assert client.post('/api/tasks',json={**command,'mode':'listing','operation_id':str(uuid4())}).status_code==422
        slot=client.post(f'/api/tasks/{task}/slots',json={'kind':'main','operation_id':str(uuid4())}).json()['created_slot_id']
        path=f'/api/tasks/{task}/slots/{slot}'
        assert not client.get(path+'/workspace').json()['readiness']['can_generate']
        asset(client,task,'product_image','red')
        assert not client.get(path+'/workspace').json()['readiness']['can_generate']
        assert client.post(path+'/generations',json={'operation_id':str(uuid4()),'quote_token':'0'*64}).status_code==409
        other=client.post('/api/tasks',json={'title':'other','operation_id':str(uuid4())}).json()['id']
        foreign,_=asset(client,other,'marketing_reference','blue')
        assert save_slot(client,path,{'reference_asset_id':foreign['id'],'output_size':'1024x1024','requirements':''}).status_code==422
        assert not images.requests and not understanding.requests


@pytest.mark.parametrize('kind,size,text,global_text', [('main','1024x1024','',''),
    ('dimensions','1024x1536','KEEP 30 cm badge','GLOBAL_BRAND'),('scene','1536x1536','SLOT_ONLY','')])
def test_one_c06_exact_sources_prompt_trace_and_restart(config,kind,size,text,global_text):
    images,understanding=ImageSubstitute(),NoUnderstanding();app=create_app(config,generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,assets,originals,values=new_b(client,size,kind,text)
        if global_text:overall(client,task,global_text,0)
        assert client.get('/api/tasks/'+task).json()['mode']=='quick_b'
        for _ in range(2):assert client.get(path+'/workspace').json()['readiness']['can_generate']
        with app.state.generation_service.database.connection() as db:assert db.execute('SELECT count(*) FROM c01_snapshots').fetchone()[0]==0
        operation=submit(client,path);body=finish(client,path);assert body['state']=='SUCCEEDED',body
        version=body['candidates'][0];snap,run=snapshot_for(app,version)
        assert len(images.requests)==1 and not understanding.requests
        assert image_bytes(images.requests[0])==list(originals)
        prompt=images.requests[0]['prompt']
        for term in ['Quick-B','唯一视觉依据','完整替换','局部特写、配件和场景','不混合两种商品的结构',
            '运营默认确认适用','不扩大为整图审批门禁','不自行新增未提供','独立有效 Context']:
            assert term in prompt
        assert 'Quick-A' not in prompt and '黑色主机' not in prompt
        assert text in prompt and global_text in prompt
        assert snap['context']['version']==VERSION and snap['context']['source_state']['mode']=='quick_b'
        assert snap['context']['operation_id']==operation['operation_id'] and snap['decision_snapshot_id'] is None
        assert snap['context_sha256']==fingerprint(snap['context']) and snap['upstream_run_ids']==[]
        assert run['task_id']==task and run['slot_id']==slot and run['capability']=='C06'
        assert run['request_sha256']==fingerprint(images.requests[0]) and run['actual_test_adapter_input']['request']==images.requests[0]
        assert run['attempt_id'] and run['automatic_retries']==0 and not run['model_api_call_attempted']
        assert [r['sha256'] for r in snap['source_provenance']]==[sha256(v).hexdigest() for v in originals]
        assert not version['pass'] and body['delivery'] is None
        assert client.post(path+'/generations',json=operation).status_code==202 and len(images.requests)==1
        # 旧 C06 读取入口识别 Quick-B 冻结快照，不要求 C03 决定。
        assert app.state.generation_service._service().preflight(UUID(snap['id']),MODEL)['request']==images.requests[0]
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        assert client.get(path+'/workspace').json()['candidates'][0]['image_version_id']==version['image_version_id']
        assert len(images.requests)==1


def test_slot_and_requirement_changes_keep_history_and_invalidate_old_preflight(config):
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,values=new_b(client,text='ONLY_A')
        reference,b=asset(client,task,'marketing_reference','green')
        other=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
        bp=f'/api/tasks/{task}/slots/{other}'
        save_slot(client,bp,{**values,'reference_asset_id':reference['id'],'requirements':'ONLY_B'}).raise_for_status()
        overall(client,task,'G1_SHARED',0)
        submit(client,path);a1=finish(client,path)['candidates'][0];old_a,_=snapshot_for(app,a1)
        delivery=pass_version(client,path,a1)
        submit(client,bp);b1=finish(client,bp)['candidates'][0];old_b,_=snapshot_for(app,b1)
        assert image_bytes(images.requests[0])==list(originals) and image_bytes(images.requests[1])==[originals[0],b]
        assert 'ONLY_B' not in images.requests[0]['prompt'] and 'ONLY_A' not in images.requests[1]['prompt']
        service=app.state.generation_service._quick_b_service()
        frozen=service.prepare(UUID(task),UUID(slot),uuid4())
        old_quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        ref2,a2=asset(client,task,'marketing_reference','purple')
        save_slot(client,path,{**values,'reference_asset_id':ref2['id'],'requirements':'ONLY_A_UPDATED'}).raise_for_status()
        with pytest.raises(C01Error,match='已变化'):service.preflight(UUID(frozen['id']),MODEL)
        assert client.post(path+'/generations',json={'operation_id':str(uuid4()),'quote_token':old_quote}).status_code==409
        overall(client,task,'G2_SHARED',1)
        for sid in [slot,other]:
            snapshot=service.prepare(UUID(task),UUID(sid),uuid4())
            req=snapshot['context']['effective_requirements'];assert req['overall']['version']==2 and req['overall']['text']=='G2_SHARED'
            assert req['slot']['text']==('ONLY_A_UPDATED' if sid==slot else 'ONLY_B')
            assert service.preflight(UUID(snapshot['id']),MODEL)['network_requests']==0
        submit(client,path);finish(client,path);assert image_bytes(images.requests[-1])==[originals[0],a2]
        assert client.get(bp+'/workspace').json()['candidates'][0]['image_version_id']==b1['image_version_id']
        assert snapshot_for(app,a1)[0]==old_a and snapshot_for(app,b1)[0]==old_b
        assert old_a['context']['effective_requirements']['overall']['text']=='G1_SHARED'
        assert client.get(path+'/workspace').json()['delivery']==delivery


@pytest.mark.parametrize('count,workers,failures',[(4,3,False),(13,3,True),(5,2,False)])
def test_dynamic_parallel_isolated_versions_failures_and_dedup(config,count,workers,failures,record_property):
    images=ConcurrentImages(min(count,workers),failures);understanding=NoUnderstanding()
    app=create_app(replace(config,generation_max_workers=workers),generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        first,task,first_id,_,originals,values=new_b(client,kind='dimensions')
        paths=[first];refs=[originals[1]]
        for n in range(1,count):
            ref,raw=asset(client,task,'marketing_reference',f'#{n:02x}4466')
            sid=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
            path=f'/api/tasks/{task}/slots/{sid}'
            save_slot(client,path,{**values,'reference_asset_id':ref['id'],'requirements':f'SLOT_{n}_ONLY'}).raise_for_status()
            paths.append(path);refs.append(raw)
        overall(client,task,'SHARED_B',0)
        submit(client,first);old=finish(client,first)['candidates'][0];delivery=pass_version(client,first,old)
        commands=[{'operation_id':str(uuid4()),'quote_token':client.get(p+'/workspace').json()['readiness']['quote_token']} for p in paths]
        images.batch=True;images.gate.clear()
        try:
            with ThreadPoolExecutor(count) as pool:responses=list(pool.map(lambda pair:client.post(pair[0]+'/generations',json=pair[1]),zip(paths,commands)))
            assert all(r.status_code==202 for r in responses)
            assert images.saturated.wait(4) and images.peak==min(count,workers)
            states=[client.get(p+'/workspace').json()['state'] for p in paths]
            assert states.count('RUNNING')==min(count,workers) and states.count('QUEUED')==max(0,count-workers)
            assert client.post(first+'/generations',json=commands[0]).status_code==202
            assert client.post(first+'/generations',json={**commands[0],'operation_id':str(uuid4())}).status_code==409
            assert client.delete('/api/tasks/'+task).status_code==409
            assert client.post('/api/tasks',json={'title':'SQLite writable during model wait','mode':'quick_b','operation_id':str(uuid4())}).status_code==201
        finally:images.gate.set()
        bodies=[finish(client,p) for p in paths];bad={4,7} if failures else set();runs=[];versions=[]
        assert len(images.batch_requests)==count and not understanding.requests
        for n,(p,result) in enumerate(zip(paths,bodies)):
            execution=next(e for e in result['executions'] if e['operation_id']==commands[n]['operation_id'])
            run=app.state.generation_service.runs.inspect(UUID(execution['run_id']));runs.append(run)
            snapshot=app.state.generation_service._quick_b_service().service.evidence.load_snapshot(UUID(run['snapshot_id']))
            assert run['task_id']==task and run['slot_id']==p.split('/')[-1] and run['capability']=='C06'
            assert snapshot['context']['version']==VERSION and snapshot['context']['effective_requirements']['slot']['text']==f'SLOT_{n}_ONLY'
            assert image_bytes(run['planned_model_input'])==[originals[0],refs[n]]
            assert 'Quick-B' in run['planned_model_input']['prompt'] and 'SHARED_B' in run['planned_model_input']['prompt']
            for k in range(count):assert (f'SLOT_{k}_ONLY' in run['planned_model_input']['prompt'])==(n==k)
            if n not in bad:
                assert result['state']=='SUCCEEDED';candidate=next(v for v in result['candidates'] if v['run_id']==run['run_id'])
                assert not candidate['pass'];versions.append(candidate['image_version_id'])
            else:assert not result['candidates']
        assert len(set(versions))==count-len(bad)
        for field in ['operation_id','snapshot_id','run_id','attempt_id']:assert len({r[field] for r in runs})==count
        assert client.get(first+'/workspace').json()['delivery']==delivery
        if failures:
            assert bodies[4]['state']=='FAILED' and bodies[7]['state']=='OUTCOME_UNKNOWN'
            assert client.post(paths[7]+'/generations',json=commands[7]).status_code==202
            assert client.post(paths[7]+'/generations',json={**commands[7],'operation_id':str(uuid4())}).status_code==409
            assert len(images.batch_requests)==count and client.delete('/api/tasks/'+task).status_code==409
        record_property('quick_b_parallel',json.dumps({'N':count,'M':workers,'peak':images.peak,'new_versions':len(versions),'C01_to_C05_calls':0}))


def test_c07_older_base_current_reference_original_product_and_pass_protection(config):
    images,understanding=DistinctImages(),NoUnderstanding();app=create_app(config,generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,values=new_b(client)
        overall(client,task,'G1_OLD',0)
        submit(client,path);v1=finish(client,path)['candidates'][0];v1_bytes=client.get(v1['url']).content
        old_snapshot,old_run=snapshot_for(app,v1);delivery=pass_version(client,path,v1)
        submit(client,path);v2=finish(client,path)['candidates'][0];v2_bytes=client.get(v2['url']).content
        assert v1_bytes!=v2_bytes
        asset(client,task,'product_image','yellow')  # 修改仍用基础版本关联的商品，不能悄悄换成新商品。
        ref,ref_bytes=asset(client,task,'marketing_reference','purple')
        save_slot(client,path,{**values,'reference_asset_id':ref['id'],'requirements':'CURRENT_B_ONLY'}).raise_for_status()
        overall(client,task,'G2_CURRENT',1)
        quote=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'CHANGE_BADGE_LAYOUT'}).json()
        command={'operation_id':str(uuid4()),'snapshot_id':quote['snapshot_id'],'quote_token':quote['quote_token']}
        assert client.post(path+'/revisions',json=command).status_code==202
        result=finish(client,path);v3=result['candidates'][0]
        assert result['state']=='SUCCEEDED' and v3['base_image_version_id']==v1['image_version_id'] and not v3['pass']
        snapshot,run=snapshot_for(app,v3)
        assert snapshot['context']['business_source']['mode']=='quick_b' and snapshot['decision_snapshot_id'] is None
        assert image_bytes(images.requests[-1])==[v1_bytes,originals[0],ref_bytes]
        for term in ['完整替换','不混合两种商品的结构','运营默认确认适用','G2_CURRENT','CURRENT_B_ONLY','CHANGE_BADGE_LAYOUT']:
            assert term in images.requests[-1]['prompt']
        assert 'G1_OLD' not in images.requests[-1]['prompt'] and 'initial_AI_content_decisions' not in images.requests[-1]['prompt']
        assert result['delivery']==delivery and client.get(v1['url']).content==v1_bytes and client.get(v2['url']).content==v2_bytes
        assert snapshot_for(app,v1)==(old_snapshot,old_run)
        assert client.post(path+'/revisions',json=command).status_code==202 and len(images.requests)==3 and not understanding.requests
        reject=client.post(path+'/versions/'+v3['image_version_id']+'/reviews',json={'operation_id':str(uuid4()),'decision':'REJECT',
            'reviewer':'TEST_ONLY','feedback':'reject','expected_current_review_id':delivery['id']});assert reject.status_code==201
        assert client.get(path+'/workspace').json()['delivery']==delivery
        # 再次修改继承 V3，但不重放上轮修改意见，仍保留 Quick-B 主体边界。
        q=client.post(path+'/versions/'+v3['image_version_id']+'/revision-quote',json={'revision_text':'SECOND_REVISION'}).json()
        frozen=app.state.generation_service._revision_service().service.evidence.load_snapshot(UUID(q['snapshot_id']))
        assert 'CHANGE_BADGE_LAYOUT' not in frozen['prompt']['planned_model_input']['text']
        assert '完整替换' in frozen['prompt']['planned_model_input']['text']
        save_slot(client,path,{**values,'reference_asset_id':ref['id'],'requirements':'CHANGED_AFTER_QUOTE'}).raise_for_status()
        assert client.post(path+'/revisions',json={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}).status_code==409
        assert len(images.requests)==3
        task_body=client.get('/api/tasks/'+task).json()
        assert client.patch('/api/tasks/'+task,json={'title':'Quick-B renamed','expected_title':task_body['title']}).json()['title']=='Quick-B renamed'
        assert client.delete('/api/tasks/'+task).status_code==204
        assert task not in {t['id'] for t in client.get('/api/tasks').json()}
        assert client.get(v1['url']).status_code==404  # 任务操作关闭，但原文件与版本行保留。
        with app.state.generation_service.database.connection() as db:
            assert db.execute('SELECT count(*) FROM image_versions WHERE task_id=?',(task,)).fetchone()[0]==3
        assert app.state.generation_service.candidates.read_for_slot(UUID(task),UUID(slot),UUID(v1['image_version_id']))[1]==v1_bytes


def test_quick_b_slot_optimistic_update_and_cross_mode_services(config):
    from aigc_workbench.quick_a_direct import read_inputs as read_a
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,_,values=new_b(client)
        page=client.get('/api/tasks/'+task).json()['slots'][0]
        with ThreadPoolExecutor(2) as pool:
            responses=list(pool.map(lambda text:client.put(path,json={**values,'requirements':text,'expected_version':page['version']}),['PAGE_B_ONE','PAGE_B_TWO']))
        assert sorted(r.status_code for r in responses)==[200,409]
        current=client.get('/api/tasks/'+task).json()['slots'][0]
        assert current['version']==page['version']+1 and current['requirements'] in {'PAGE_B_ONE','PAGE_B_TWO'}
        service=app.state.generation_service._quick_b_service()
        with pytest.raises(C01Error,match='Quick-A'):read_a(service.service,UUID(task),UUID(slot))
        snap=service.prepare(UUID(task),UUID(slot),uuid4())
        with pytest.raises(C01Error,match='另一个操作'):service.run(UUID(snap['id']),MODEL,uuid4(),confirmed=True,max_cost_usd='1.00',transport=images)
        assert not images.requests


def test_v12_migration_preserves_all_quick_a_history_and_rolls_back(config,monkeypatch):
    from aigc_workbench.persistence import V1_TASKS
    from test_quick_a_creation import new_task
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,_,_,_,_=new_task(client,'1024x1024')
        submit(client,path);version=finish(client,path)['candidates'][0]
        delivery=pass_version(client,path,version);png=client.get(version['url']).content
    # 只在临时库还原基线 V12 的 Task CHECK，其余表与真实保存的 QA Evidence 保持原样。
    with sqlite3.connect(config.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        definition=V1_TASKS.replace('CREATE TABLE task_drafts (','CREATE TABLE tasks_v12 (').replace('created_at TEXT NOT NULL','created_at TEXT NOT NULL, deleted_at TEXT')
        db.execute(definition);fields='id,title,mode,status,created_at,deleted_at'
        db.execute(f'INSERT INTO tasks_v12 ({fields}) SELECT {fields} FROM task_drafts')
        db.execute('DROP TABLE task_drafts');db.execute('ALTER TABLE tasks_v12 RENAME TO task_drafts');db.execute('PRAGMA user_version=12')
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before={t:sorted(db.execute('SELECT * FROM '+t).fetchall(),key=str) for t in tables}
    original=SQLiteTaskStore._upgrade_v13
    def fail_after_rebuild(self,db):original(self,db);raise RuntimeError('TEST_ONLY_MIGRATION_FAILURE')
    monkeypatch.setattr(SQLiteTaskStore,'_upgrade_v13',fail_after_rebuild)
    with pytest.raises(RuntimeError,match='TEST_ONLY_MIGRATION_FAILURE'):SQLiteTaskStore(config.database_path).initialize()
    with sqlite3.connect(config.database_path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==12
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_drafts_next'").fetchone()
        assert all(sorted(db.execute('SELECT * FROM '+t).fetchall(),key=str)==rows for t,rows in before.items())
    monkeypatch.setattr(SQLiteTaskStore,'_upgrade_v13',original);SQLiteTaskStore(config.database_path).initialize()
    with sqlite3.connect(config.database_path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==14 and not db.execute('PRAGMA foreign_key_check').fetchall()
        assert all(sorted(db.execute('SELECT * FROM '+t).fetchall(),key=str)==rows for t,rows in before.items())
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        state=client.get(path+'/workspace').json();assert state['delivery']==delivery and state['candidates'][0]['image_version_id']==version['image_version_id']
        assert client.get(version['url']).content==png
        assert client.post('/api/tasks',json={'title':'Quick-B after upgrade','mode':'quick_b','operation_id':str(uuid4())}).status_code==201
        assert len(images.requests)==1


def test_c07_never_reads_sibling_reference_candidate_or_pass(config):
    images=DistinctImages();app=create_app(config,generation_transport=images,understanding_transport=NoUnderstanding())
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,values=new_b(client,text='CURRENT_A_ONLY')
        ref,ref_bytes=asset(client,task,'marketing_reference','green')
        sid=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
        bp=f'/api/tasks/{task}/slots/{sid}'
        save_slot(client,bp,{**values,'reference_asset_id':ref['id'],'requirements':'SIBLING_B_SECRET'}).raise_for_status()
        submit(client,bp);b1=finish(client,bp)['candidates'][0]
        b_delivery=pass_version(client,bp,b1);b_snapshot,b_run=snapshot_for(app,b1);b_png=client.get(b1['url']).content
        submit(client,path);a1=finish(client,path)['candidates'][0];a_png=client.get(a1['url']).content
        assert client.post(path+'/versions/'+b1['image_version_id']+'/revision-quote',json={'revision_text':'CROSS_SLOT'}).status_code==404
        quote=client.post(path+'/versions/'+a1['image_version_id']+'/revision-quote',json={'revision_text':'ONLY_A_REVISION'}).json()
        command={'operation_id':str(uuid4()),'snapshot_id':quote['snapshot_id'],'quote_token':quote['quote_token']}
        assert client.post(path+'/revisions',json=command).status_code==202
        result=finish(client,path);assert result['state']=='SUCCEEDED' and not result['candidates'][0]['pass']
        request=images.requests[-1];assert image_bytes(request)==[a_png,originals[0],originals[1]]
        assert 'SIBLING_B_SECRET' not in request['prompt'] and ref['id'] not in request['prompt'] and b1['image_version_id'] not in request['prompt']
        assert client.get(bp+'/workspace').json()['delivery']==b_delivery
        assert client.get(b1['url']).content==b_png and snapshot_for(app,b1)==(b_snapshot,b_run)
        assert len(client.get(bp+'/workspace').json()['candidates'])==1
