"""Versioned operator requirements through real HTTP/storage and TEST_ONLY AI tools."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from aigc_workbench.app import create_app
from aigc_workbench.persistence import SQLiteTaskStore, SQLiteChanges
from aigc_workbench.c01_context import C01Error
from aigc_workbench.c06_generation import C06Service, MODEL
from quick_a_adapters import UnderstandingSubstitute, ImageSubstitute
from test_quick_a_creation import config, application, new_task, submit, finish


def save(client, task, text, version, operation=None):
    return client.put(f'/api/tasks/{task}/overall-requirement', json={
        'text':text, 'expected_version':version, 'operation_id':str(operation or uuid4())})


def test_initial_atomic_create_versions_clear_restore_and_replay(config):
    operation = str(uuid4()); command = {'title':'TEST_ONLY overall', 'operation_id':operation, 'overall_requirements':'GLOBAL_ONE'}
    with TestClient(create_app(config), base_url='http://localhost') as client:
        first=client.post('/api/tasks',json=command); assert first.status_code==201
        task=first.json(); identity=task['id']; r=task['overall_requirement']
        assert r['version']==1 and r['text']=='GLOBAL_ONE' and r['scope']=='TASK_ALL_SLOTS'
        op=uuid4(); assert save(client,identity,'GLOBAL_TWO',1,op).status_code==200
        assert save(client,identity,'GLOBAL_TWO',1,op).json()['overall_requirement']['version']==2
        assert save(client,identity,'changed payload',1,op).status_code==409
        assert save(client,identity,'stale edit',1).status_code==409
        assert client.post('/api/tasks',json=command).json()['overall_requirement']['text']=='GLOBAL_TWO'
        assert client.post('/api/tasks',json={**command,'overall_requirements':'different'}).status_code==409
        cleared=save(client,identity,'',2).json(); assert cleared['overall_requirement']['version']==3
        assert cleared['overall_requirement']['text']==''
        assert save(client,identity,'x'*2001,3).status_code==422
        assert save(client,str(uuid4()),'other task',0).status_code==404
    with TestClient(create_app(config),base_url='http://localhost') as client:
        assert client.get('/api/tasks/'+identity).json()==cleared
    with sqlite3.connect(config.database_path) as db:
        assert db.execute('select version,text from task_requirements order by version').fetchall()==[(1,'GLOBAL_ONE'),(2,'GLOBAL_TWO'),(3,'')]
        with pytest.raises(sqlite3.IntegrityError): db.execute("update task_requirements set text='overwrite'")
        with pytest.raises(sqlite3.IntegrityError): db.execute('delete from task_requirements')


def test_requirement_write_failure_rolls_back_task_creation(config,monkeypatch):
    def fail(*a,**k):raise sqlite3.OperationalError('TEST_ONLY disk failure')
    monkeypatch.setattr(SQLiteChanges,'add_requirement',fail)
    with TestClient(create_app(config),base_url='http://localhost') as client:
        r=client.post('/api/tasks',json={'title':'not saved','operation_id':str(uuid4()),'overall_requirements':'must save atomically'})
        assert r.status_code==503 and client.get('/api/tasks').json()==[]


def test_concurrent_updates_do_not_silently_overwrite(config):
    with TestClient(create_app(config),base_url='http://localhost') as client:
        task=client.post('/api/tasks',json={'title':'TEST_ONLY concurrency','operation_id':str(uuid4())}).json()
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda value:save(client,task['id'],value,0),['ONE','TWO']))
        assert sorted(r.status_code for r in results)==[200,409]
        assert client.get('/api/tasks/'+task['id']).json()['overall_requirement']['version']==1


def test_capability_scoping_reuse_pass_protection_stale_quotes_and_selected_revision(config):
    understanding,images=UnderstandingSubstitute(),ImageSubstitute();app=application(config,understanding,images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,*_=new_task(client)
        assert save(client,task,'GLOBAL_V1_CANARY',0).status_code==200
        submit(client,path);body=finish(client,path);assert body['state']=='SUCCEEDED',body
        v1=body['candidates'][0];v1bytes=client.get(v1['url']).content
        runs=app.state.generation_service.runs; evidence=app.state.generation_service._service().service.evidence
        old={}
        for step in body['executions'][0]['progress']['steps']:
            run=runs.inspect(UUID(step['run_id']));snapshot=evidence.load_snapshot(UUID(step['snapshot_id']))
            old[step['snapshot_id']]=(snapshot,run)
            request=run['actual_test_adapter_input']['request']; serialized=json.dumps(request)
            if step['capability'] in {'C01','C02'}:assert 'GLOBAL_V1_CANARY' not in serialized
            else:
                effective=snapshot['context']['effective_requirements']
                assert effective['overall']['version']==1 and effective['overall']['text']=='GLOBAL_V1_CANARY'
                assert effective['slot']['text']=='TEST_ONLY preserve the supplied badge'
                assert 'GLOBAL_V1_CANARY' in serialized
        original_c06=app.state.generation_service._service()
        original_snapshot=next(k for k,(s,_) in old.items() if s['capability']=='C06')
        review=client.post(path+'/versions/'+v1['image_version_id']+'/reviews',json={'operation_id':str(uuid4()),
          'decision':'PASS','reviewer':'TEST_ONLY explicit human','feedback':'TEST_ONLY, not business acceptance','expected_current_review_id':None})
        assert review.status_code==201
        delivery=client.get(path+'/workspace').json()['delivery'];count=(len(understanding.requests),len(images.requests))
        assert save(client,task,'GLOBAL_V2_CANARY',1).status_code==200
        now=client.get(path+'/workspace').json(); assert now['delivery']==delivery
        assert (len(understanding.requests),len(images.requests))==count
        with pytest.raises(C01Error):original_c06.preflight(UUID(original_snapshot),MODEL)
        for identity,(snapshot,run) in old.items():
            assert evidence.load_snapshot(UUID(identity))==snapshot and runs.inspect(UUID(run['run_id']))==run
        submit(client,path);body2=finish(client,path);assert body2['state']=='SUCCEEDED',body2
        assert len(understanding.requests)==4 and len(images.requests)==2
        assert [s['reused'] for s in body2['executions'][0]['progress']['steps']]==[True,True,False,False]
        assert 'GLOBAL_V2_CANARY' in json.dumps(understanding.requests[-1]) and 'GLOBAL_V1_CANARY' not in json.dumps(understanding.requests[-1])
        assert 'GLOBAL_V2_CANARY' in images.requests[-1]['prompt']
        quote_path=path+'/versions/'+v1['image_version_id']+'/revision-quote'
        q=client.post(quote_path,json={'revision_text':'CURRENT_ONLY_MOVE_TITLE'}).json()
        snap=evidence.load_snapshot(UUID(q['snapshot_id'])); constraints=snap['context']['effective_constraints']
        assert snap['context']['base_image_version_id']==v1['image_version_id']
        assert constraints['effective_requirements']['overall']['version']==2
        assert constraints['effective_requirements']['selected_base_review']['is_passed'] is True
        assert 'GLOBAL_V2_CANARY' in snap['prompt']['planned_model_input']['text'] and 'GLOBAL_V1_CANARY' not in snap['prompt']['planned_model_input']['text']
        assert snap['context']['historical_revision_instructions']=='NOT_REPLAYED'
        assert save(client,task,'GLOBAL_V3_CANARY',2).status_code==200
        assert client.post(path+'/revisions',json={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}).status_code==409
        q=client.post(quote_path,json={'revision_text':'CURRENT_ONLY_MOVE_TITLE'}).json()
        assert client.post(path+'/revisions',json={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}).status_code==202
        revised=finish(client,path);assert revised['state']=='SUCCEEDED'
        assert revised['candidates'][0]['base_image_version_id']==v1['image_version_id']
        assert revised['delivery']==delivery and client.get(v1['url']).content==v1bytes
        assert 'GLOBAL_V3_CANARY' in images.requests[-1]['prompt']
        assert len(images.requests)==3
    with TestClient(application(config,understanding,images),base_url='http://localhost') as client:
        restored=client.get(path+'/workspace').json();assert restored['delivery']==delivery
        assert len(restored['candidates'])==3 and client.get(v1['url']).content==v1bytes


def test_v9_additive_migration_and_transactional_rollback_preserve_existing_data(config,monkeypatch):
    import aigc_workbench.persistence as persistence
    with TestClient(create_app(config),base_url='http://localhost') as client:
        original=client.post('/api/tasks',json={'title':'old task','operation_id':str(uuid4())}).json()
    database=SQLiteTaskStore(config.database_path)
    with database.connection(write=True) as db:
        db.execute('drop table task_requirements')
        db.execute('alter table task_creations drop column initial_overall_requirement')
        db.execute('pragma user_version=8')
        before=[tuple(r) for r in db.execute('select * from task_drafts')]
    statements=persistence.V9_TABLES
    monkeypatch.setattr(persistence,'V9_TABLES',statements+('INVALID SQL',))
    with pytest.raises(sqlite3.Error):database.initialize()
    with database.connection() as db:
        assert db.execute('pragma user_version').fetchone()[0]==8
        assert 'initial_overall_requirement' not in [r[1] for r in db.execute('pragma table_info(task_creations)')]
    monkeypatch.setattr(persistence,'V9_TABLES',statements);database.initialize()
    with database.connection() as db:
        assert db.execute('pragma user_version').fetchone()[0]==10
        assert [tuple(r) for r in db.execute('select * from task_drafts')]==before
    with TestClient(create_app(config),base_url='http://localhost') as client:
        assert client.get('/api/tasks/'+original['id']).json()==original
