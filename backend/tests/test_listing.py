"""Listing 专项：临时数据库及图片，Fake C06，零外网和付费调用。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
from uuid import UUID,uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error,fingerprint
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.persistence import SQLiteTaskStore
from aigc_workbench.listing import VERSION
from aigc_workbench.pilot_backup import create as backup,restore
from quick_a_adapters import ImageSubstitute
from test_quick_a_creation import config,asset,save_slot,submit
from test_quick_a_context_isolation import NoUnderstanding,DistinctImages
from test_quick_a_direct import snapshot_for,overall
from test_quick_a_parallel import ConcurrentImages
from test_quick_b import pass_version
from test_workspace_generation import finish
from test_quick_a_creation import image_bytes


def new_listing(client,reference=False,text='LISTING_FACT 30 cm',kind='main'):
    command={'title':'TEST_ONLY Listing','mode':'listing','operation_id':str(uuid4()),'listing_text':text,'supplemental_text':'SUPPLEMENT_FACT'}
    r=client.post('/api/tasks',json=command);assert r.status_code==201,r.text
    task=r.json()['id'];product,p=asset(client,task,'product_image','red')
    ref,raw=asset(client,task,'marketing_reference','blue') if reference else (None,None)
    sid=client.post(f'/api/tasks/{task}/slots',json={'kind':kind,'operation_id':str(uuid4())}).json()['created_slot_id']
    path=f'/api/tasks/{task}/slots/{sid}'
    values={'reference_asset_id':ref['id'] if ref else None,'output_size':'1024x1024','requirements':'SLOT_0_ONLY'}
    save_slot(client,path,values).raise_for_status()
    return path,task,sid,product,(p,raw),values,command


def materials(client,task,text,version,supplement='SUPPLEMENT_CURRENT',operation=None):
    return client.put('/api/tasks/'+task+'/listing-materials',json={'listing_text':text,'supplemental_text':supplement,
        'expected_version':version,'operation_id':operation or str(uuid4())})


@pytest.mark.parametrize('listing_text',['','  ','x'*50001])
def test_required_listing_validation_never_saves_invalid_task(config,listing_text):
    with TestClient(create_app(config),base_url='http://localhost') as client:
        r=client.post('/api/tasks',json={'title':'Listing','mode':'listing','listing_text':listing_text,'operation_id':str(uuid4())})
        assert r.status_code==422 and client.get('/api/tasks').json()==[]


def test_material_versions_replay_conflicts_and_restart(config):
    app=create_app(config)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,_,_,command=new_listing(client,text='G1 原始标题\n五点和参数，无需 JSON')
        original=client.get('/api/tasks/'+task).json()['listing_materials'];assert original['version']==1
        op=str(uuid4());assert materials(client,task,'G2 updated',1,operation=op).status_code==200
        assert materials(client,task,'G2 updated',1,operation=op).json()['listing_materials']['version']==2
        assert materials(client,task,'different',1,operation=op).status_code==409
        assert materials(client,task,'STALE',1).status_code==409
        assert client.post('/api/tasks',json=command).json()['listing_materials']['version']==2
        assert client.post('/api/tasks',json={**command,'listing_text':'changed create'}).status_code==409
        # 同一有效版本两个页面同时提交，只有一方成功。
        with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda x:materials(client,task,x,2),['PAGE_1','PAGE_2']))
        assert sorted(r.status_code for r in responses)==[200,409]
        latest=client.get('/api/tasks/'+task).json()['listing_materials'];assert latest['version']==3
        with app.state.task_service.store.connection() as db:
            assert db.execute('SELECT listing_text FROM task_listing_materials WHERE task_id=? AND version=1',(task,)).fetchone()[0]==original['listing_text']
        with app.state.task_service.store.connection(write=True) as db:
            with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE task_listing_materials SET listing_text=? WHERE task_id=?',('BAD',task))
    with TestClient(create_app(config),base_url='http://localhost') as client:
        assert client.get('/api/tasks/'+task).json()['listing_materials']==latest
        other=client.post('/api/tasks',json={'title':'Quick-A','operation_id':str(uuid4())}).json()['id']
        assert materials(client,other,'not allowed',0).status_code==422


@pytest.mark.parametrize('has_reference,kind',[(False,'main'),(True,'main'),(False,'dimensions')])
def test_listing_one_c06_sources_authority_snapshot_and_restart(config,has_reference,kind):
    images,understanding=ImageSubstitute(),NoUnderstanding();app=create_app(config,generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,_,_=new_listing(client,has_reference,kind=kind)
        overall(client,task,'GLOBAL_REQUIRED',0)
        for _ in range(2):assert client.get(path+'/workspace').json()['readiness']['can_generate']
        assert not images.requests
        operation=submit(client,path);state=finish(client,path);assert state['state']=='SUCCEEDED',state
        version=state['candidates'][0];snapshot,run=snapshot_for(app,version)
        assert len(images.requests)==1 and not understanding.requests
        assert image_bytes(images.requests[0])==([originals[0],originals[1]] if has_reference else [originals[0]])
        for text in ['LISTING_FACT','SUPPLEMENT_FACT','GLOBAL_REQUIRED','SLOT_0_ONLY','不得创造资料未提供','没有当前商品资料支持时不得继承','不能静默遗漏']:
            assert text in images.requests[0]['prompt']
        assert ('Image 2 是当前图位' in images.requests[0]['prompt'])==has_reference
        if kind=='dimensions':assert '已标注目标商品真实物理尺寸的专用白底素材' in images.requests[0]['prompt']
        assert snapshot['context']['version']==VERSION and snapshot['context']['source_state']['mode']=='listing'
        assert snapshot['context']['source_state']['listing_materials']['version']==1
        assert snapshot['context']['operation_id']==operation['operation_id'] and snapshot['context_sha256']==fingerprint(snapshot['context'])
        assert snapshot['decision_snapshot_id'] is None and snapshot['upstream_run_ids']==[]
        assert run['capability']=='C06' and run['task_id']==task and run['slot_id']==slot
        assert run['attempt_id'] and run['request_sha256']==fingerprint(images.requests[0])
        assert run['actual_test_adapter_input']['request']==images.requests[0] and not run['model_api_call_attempted']
        assert len(snapshot['source_provenance'])==(2 if has_reference else 1)
        assert snapshot['source_provenance'][0]['sha256']==sha256(originals[0]).hexdigest()
        assert version['reference_asset_id']==snapshot['reference_asset_id']
        assert not version['pass'] and state['delivery'] is None
        assert client.post(path+'/generations',json=operation).status_code==202 and len(images.requests)==1
        assert app.state.generation_service._service().preflight(UUID(snapshot['id']),MODEL)['request']==images.requests[0]
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        assert client.get(path+'/workspace').json()['candidates'][0]['image_version_id']==version['image_version_id']
        assert client.get(version['url']).content and len(images.requests)==1


def test_material_changes_shared_per_slot_context_isolated_and_old_snapshots_kept(config):
    images=DistinctImages();app=create_app(config,generation_transport=images,understanding_transport=NoUnderstanding())
    with TestClient(app,base_url='http://localhost') as client:
        ap,task,a,_,originals,values,_=new_listing(client,True,text='M1_SHARED')
        ref2,ref_bytes=asset(client,task,'marketing_reference','green')
        b=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id'];bp=f'/api/tasks/{task}/slots/{b}'
        save_slot(client,bp,{**values,'reference_asset_id':ref2['id'],'requirements':'SLOT_1_ONLY'}).raise_for_status()
        overall(client,task,'GLOBAL_ONE',0)
        for p in [ap,bp]:submit(client,p);assert finish(client,p)['state']=='SUCCEEDED'
        av=client.get(ap+'/workspace').json()['candidates'][0];bv=client.get(bp+'/workspace').json()['candidates'][0]
        old_a,_=snapshot_for(app,av);old_b,_=snapshot_for(app,bv);delivery=pass_version(client,bp,bv)
        assert image_bytes(images.requests[0])==list(originals) and image_bytes(images.requests[1])==[originals[0],ref_bytes]
        service=app.state.generation_service._listing_service()
        frozen=[service.prepare(UUID(task),UUID(s),uuid4()) for s in [a,b]]
        quotes=[client.get(p+'/workspace').json()['readiness']['quote_token'] for p in [ap,bp]]
        assert materials(client,task,'M2_SHARED_CURRENT',1).status_code==200
        for p,snapshot,quote in zip([ap,bp],frozen,quotes):
            with pytest.raises(C01Error,match='已变化'):service.preflight(UUID(snapshot['id']),MODEL)
            assert client.post(p+'/generations',json={'operation_id':str(uuid4()),'quote_token':quote}).status_code==409
        # A 移除参考、修改要求不会让 B 的参考和要求失效或串用。
        save_slot(client,ap,{**values,'reference_asset_id':None,'requirements':'ONLY_A_CURRENT'}).raise_for_status()
        for p,sid,own,other in [(ap,a,'ONLY_A_CURRENT','SLOT_1_ONLY'),(bp,b,'SLOT_1_ONLY','ONLY_A_CURRENT')]:
            submit(client,p);state=finish(client,p);new=state['candidates'][0];snapshot,_=snapshot_for(app,new)
            assert snapshot['context']['source_state']['listing_materials']['version']==2
            assert 'M2_SHARED_CURRENT' in images.requests[-1]['prompt'] and own in images.requests[-1]['prompt'] and other not in images.requests[-1]['prompt']
            assert len(image_bytes(images.requests[-1]))==(1 if sid==a else 2)
        assert snapshot_for(app,av)[0]==old_a and snapshot_for(app,bv)[0]==old_b
        assert old_a['context']['source_state']['listing_materials']['listing_text']=='M1_SHARED'
        assert client.get(bp+'/workspace').json()['delivery']==delivery


@pytest.mark.parametrize('base_ref,current_ref',[(False,False),(False,True),(True,False),(True,True)])
def test_c07_selected_old_version_latest_materials_optional_reference_and_pass(config,base_ref,current_ref):
    images=DistinctImages();app=create_app(config,generation_transport=images,understanding_transport=NoUnderstanding())
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,values,_=new_listing(client,base_ref,text='M1_OLD_ONLY')
        submit(client,path);v1=finish(client,path)['candidates'][0];v1png=client.get(v1['url']).content
        old_snapshot,old_run=snapshot_for(app,v1);delivery=pass_version(client,path,v1)
        submit(client,path);v2=finish(client,path)['candidates'][0];v2png=client.get(v2['url']).content;assert v1png!=v2png
        asset(client,task,'product_image','yellow')
        reference,ref_png=asset(client,task,'marketing_reference','purple') if current_ref else (None,None)
        save_slot(client,path,{**values,'reference_asset_id':reference['id'] if reference else None,'requirements':'CURRENT_SLOT_ONLY'}).raise_for_status()
        assert materials(client,task,'M2_CURRENT_ONLY',1).status_code==200
        overall(client,task,'GLOBAL_CURRENT',0)
        quote=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'CHANGE_BADGE_ONLY'});assert quote.status_code==200,quote.text
        q=quote.json();command={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}
        assert client.post(path+'/revisions',json=command).status_code==202
        state=finish(client,path);assert state['state']=='SUCCEEDED',state
        v3=state['candidates'][0];snap,run=snapshot_for(app,v3)
        assert v3['base_image_version_id']==v1['image_version_id'] and not v3['pass']
        assert image_bytes(images.requests[-1])==[v1png,originals[0]]+([ref_png] if current_ref else [])
        assert snap['context']['version']=='c07-listing-selected-base-v1' and snap['context']['business_source']['mode']=='listing'
        for word in ['M2_CURRENT_ONLY','CURRENT_SLOT_ONLY','GLOBAL_CURRENT','CHANGE_BADGE_ONLY','没有当前商品资料支持时不得继承']:
            assert word in images.requests[-1]['prompt']
        assert 'M1_OLD_ONLY' not in images.requests[-1]['prompt'] and 'initial_AI_content_decisions' not in images.requests[-1]['prompt']
        assert state['delivery']==delivery and snapshot_for(app,v1)==(old_snapshot,old_run)
        assert client.get(v1['url']).content==v1png and client.get(v2['url']).content==v2png
        assert client.post(path+'/revisions',json=command).status_code==202 and len(images.requests)==3
        client.post(path+'/versions/'+v3['image_version_id']+'/reviews',json={'operation_id':str(uuid4()),'decision':'REJECT','reviewer':'TEST_ONLY',
            'feedback':'reject','expected_current_review_id':delivery['id']}).raise_for_status()
        assert client.get(path+'/workspace').json()['delivery']==delivery
        q2=client.post(path+'/versions/'+v3['image_version_id']+'/revision-quote',json={'revision_text':'SECOND_EDIT'}).json()
        frozen=app.state.generation_service._revision_service().service.evidence.load_snapshot(UUID(q2['snapshot_id']))
        assert 'CHANGE_BADGE_ONLY' not in frozen['prompt']['planned_model_input']['text']
        assert materials(client,task,'M3_NEWER',2).status_code==200
        assert client.post(path+'/revisions',json={'operation_id':str(uuid4()),'snapshot_id':q2['snapshot_id'],'quote_token':q2['quote_token']}).status_code==409
        assert len(images.requests)==3


@pytest.mark.parametrize('count,workers,failures',[(4,3,False),(13,3,True),(5,2,False)])
def test_dynamic_listing_batch_bounded_parallel_failure_isolation(config,count,workers,failures,record_property):
    images=ConcurrentImages(min(count,workers),failures);understanding=NoUnderstanding()
    app=create_app(replace(config,generation_max_workers=workers),generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        first,task,slot,_,originals,values,_=new_listing(client,False,text='SHARED_PRODUCT_MATERIALS')
        paths=[first];refs=[None]
        for i in range(1,count):
            ref,png=asset(client,task,'marketing_reference',f'#{i:02x}5577') if i%2 else (None,None)
            sid=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
            p=f'/api/tasks/{task}/slots/{sid}'
            save_slot(client,p,{**values,'reference_asset_id':ref['id'] if ref else None,'requirements':f'SLOT_{i}_ONLY'}).raise_for_status()
            paths.append(p);refs.append(png)
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
            assert client.post('/api/tasks',json={'title':'SQLite writer during wait','mode':'listing','listing_text':'PRODUCT FACTS','operation_id':str(uuid4())}).status_code==201
        finally:images.gate.set()
        results=[finish(client,p) for p in paths];bad={4,7} if failures else set();runs=[];versions=[]
        assert len(images.batch_requests)==count and not understanding.requests
        for i,(p,result) in enumerate(zip(paths,results)):
            execution=next(e for e in result['executions'] if e['operation_id']==commands[i]['operation_id'])
            run=app.state.generation_service.runs.inspect(UUID(execution['run_id']));runs.append(run)
            snapshot=app.state.generation_service._listing_service().service.evidence.load_snapshot(UUID(run['snapshot_id']))
            assert run['task_id']==task and run['slot_id']==p.split('/')[-1] and run['capability']=='C06'
            assert snapshot['context']['version']==VERSION and snapshot['context']['source_state']['listing_materials']['version']==1
            assert image_bytes(run['planned_model_input'])==[originals[0]]+([refs[i]] if refs[i] else [])
            prompt=run['planned_model_input']['prompt'];assert 'SHARED_PRODUCT_MATERIALS' in prompt
            for n in range(count):assert (f'SLOT_{n}_ONLY' in prompt)==(n==i)
            if i not in bad:
                assert result['state']=='SUCCEEDED';candidate=next(v for v in result['candidates'] if v['run_id']==run['run_id'])
                assert not candidate['pass'];versions.append(candidate['image_version_id'])
            else:assert not result['candidates']
        assert len(set(versions))==count-len(bad)
        for field in ['run_id','attempt_id','operation_id','snapshot_id']:assert len({r[field] for r in runs})==count
        assert client.get(first+'/workspace').json()['delivery']==delivery
        if failures:
            assert results[4]['state']=='FAILED' and results[7]['state']=='OUTCOME_UNKNOWN'
            assert client.post(paths[7]+'/generations',json=commands[7]).status_code==202
            assert client.post(paths[7]+'/generations',json={**commands[7],'operation_id':str(uuid4())}).status_code==409
            assert len(images.batch_requests)==count
        record_property('listing_parallel',json.dumps({'N':count,'M':workers,'peak':images.peak,'new_versions':len(versions),'C01_to_C05_calls':0}))


def test_listing_materials_versions_and_no_reference_candidates_survive_backup(config,tmp_path):
    settings=replace(config,data_dir=tmp_path/'runtime'/'f01');images=ImageSubstitute()
    with TestClient(create_app(settings,generation_transport=images),base_url='http://localhost') as client:
        path,task,slot,_,_,_,_=new_listing(client)
        submit(client,path);v1=finish(client,path)['candidates'][0];delivery=pass_version(client,path,v1)
        assert materials(client,task,'UPDATED_LONG_TEXT\n'+'parameter '*1000,1).status_code==200
        saved=client.get('/api/tasks/'+task).json()['listing_materials']
    archive=tmp_path/'backup';manifest=backup(settings.data_dir.parent,settings.data_dir,archive)
    assert manifest['inventory']['schema']==14 and manifest['inventory']['versions_checked']==1
    restored=restore(archive,tmp_path/'restored');new_settings=replace(settings,data_dir=tmp_path/'restored'/'f01')
    with TestClient(create_app(new_settings,generation_transport=images),base_url='http://localhost') as client:
        assert client.get('/api/tasks/'+task).json()['listing_materials']==saved
        assert client.get(path+'/workspace').json()['delivery']==delivery and client.get(v1['url']).status_code==200
        assert len(images.requests)==1


def test_v13_migration_failure_rolls_back_and_preserves_quick_a_b_history(config,monkeypatch):
    from aigc_workbench.persistence import V1_TASKS
    from test_quick_a_creation import new_task
    from test_quick_b import new_b
    images=ImageSubstitute();saved=[]
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        for factory in [new_task,new_b]:
            path,task,*_=factory(client,'1024x1024')
            submit(client,path);v=finish(client,path)['candidates'][0];d=pass_version(client,path,v)
            saved.append((path,task,v,d,client.get(v['url']).content))
    with sqlite3.connect(config.database_path) as db:
        db.execute('BEGIN IMMEDIATE');db.execute('DROP TABLE task_listing_materials')
        db.execute('ALTER TABLE task_creations DROP COLUMN initial_listing_text');db.execute('ALTER TABLE task_creations DROP COLUMN initial_supplemental_text')
        definition=V1_TASKS.replace('CREATE TABLE task_drafts (','CREATE TABLE tasks_v13 (').replace("mode = 'quick_a'","mode IN ('quick_a','quick_b')").replace('created_at TEXT NOT NULL','created_at TEXT NOT NULL, deleted_at TEXT')
        db.execute(definition);fields='id,title,mode,status,created_at,deleted_at'
        db.execute(f'INSERT INTO tasks_v13 ({fields}) SELECT {fields} FROM task_drafts');db.execute('DROP TABLE task_drafts');db.execute('ALTER TABLE tasks_v13 RENAME TO task_drafts');db.execute('PRAGMA user_version=13')
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before={t:([r[1] for r in db.execute('PRAGMA table_info('+t+')')],sorted(db.execute('SELECT * FROM '+t).fetchall(),key=str)) for t in tables}
    original=SQLiteTaskStore._upgrade_v14
    def fail(self,db):original(self,db);raise RuntimeError('TEST_ONLY_MIGRATION_FAILURE')
    monkeypatch.setattr(SQLiteTaskStore,'_upgrade_v14',fail)
    with pytest.raises(RuntimeError,match='TEST_ONLY_MIGRATION_FAILURE'):SQLiteTaskStore(config.database_path).initialize()
    with sqlite3.connect(config.database_path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==13
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('task_listing_materials','task_drafts_next')").fetchone()
        for table,(cols,rows) in before.items():
            assert [r[1] for r in db.execute('PRAGMA table_info('+table+')')]==cols
            assert sorted(db.execute('SELECT * FROM '+table).fetchall(),key=str)==rows
    monkeypatch.setattr(SQLiteTaskStore,'_upgrade_v14',original);SQLiteTaskStore(config.database_path).initialize()
    with sqlite3.connect(config.database_path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==14 and not db.execute('PRAGMA foreign_key_check').fetchall()
        for table,(cols,rows) in before.items():assert sorted(db.execute('SELECT '+','.join(cols)+' FROM '+table).fetchall(),key=str)==rows
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        for path,task,v,d,png in saved:
            assert client.get(path+'/workspace').json()['delivery']==d and client.get(v['url']).content==png
        new_listing(client);assert len(images.requests)==2


def test_listing_c07_and_task_management_preserve_sibling_delivery_and_material_history(config):
    images=DistinctImages();app=create_app(config,generation_transport=images,understanding_transport=NoUnderstanding())
    with TestClient(app,base_url='http://localhost') as client:
        ap,task,a,_,originals,values,command=new_listing(client,False)
        ref,rpng=asset(client,task,'marketing_reference','green')
        b=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id'];bp=f'/api/tasks/{task}/slots/{b}'
        save_slot(client,bp,{**values,'reference_asset_id':ref['id'],'requirements':'SIBLING_B_ONLY'}).raise_for_status()
        submit(client,bp);bv=finish(client,bp)['candidates'][0];bd=pass_version(client,bp,bv);bs,br=snapshot_for(app,bv);bpng=client.get(bv['url']).content
        submit(client,ap);av=finish(client,ap)['candidates'][0];apng=client.get(av['url']).content
        assert client.post(ap+'/versions/'+bv['image_version_id']+'/revision-quote',json={'revision_text':'CROSS_SLOT'}).status_code==404
        q=client.post(ap+'/versions/'+av['image_version_id']+'/revision-quote',json={'revision_text':'ONLY_A_EDIT'}).json()
        assert client.post(ap+'/revisions',json={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}).status_code==202
        result=finish(client,ap);assert result['state']=='SUCCEEDED' and not result['candidates'][0]['pass']
        request=images.requests[-1];assert image_bytes(request)==[apng,originals[0]]
        assert 'SIBLING_B_ONLY' not in request['prompt'] and bv['image_version_id'] not in request['prompt'] and ref['id'] not in request['prompt']
        assert client.get(bp+'/workspace').json()['delivery']==bd and client.get(bv['url']).content==bpng and snapshot_for(app,bv)==(bs,br)
        page=client.get('/api/tasks/'+task).json()['slots'][0]
        assert client.put(ap,json={**values,'requirements':'NEW_SLOT','expected_version':page['version']}).status_code==200
        assert client.put(ap,json={**values,'requirements':'OLD_PAGE','expected_version':page['version']}).status_code==409
        task_body=client.get('/api/tasks/'+task).json()
        assert client.patch('/api/tasks/'+task,json={'title':'Listing renamed','expected_title':task_body['title']}).status_code==200
        assert client.delete('/api/tasks/'+task).status_code==204
        assert task not in {t['id'] for t in client.get('/api/tasks').json()}
        assert client.post('/api/tasks',json=command).status_code==409
        with app.state.task_service.store.connection() as db:
            assert db.execute('SELECT count(*) FROM task_listing_materials WHERE task_id=?',(task,)).fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM image_versions WHERE task_id=?',(task,)).fetchone()[0]==3
        assert app.state.generation_service.candidates.read_for_slot(UUID(task),UUID(b),UUID(bv['image_version_id']))[1]==bpng
