"""任务管理与尺寸图说明：临时 SQLite、真实文件和显式 Fake 模型。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.persistence import SQLiteTaskStore
from aigc_workbench.pilot_backup import create, restore, verify
from quick_a_adapters import ImageSubstitute
from test_quick_a_creation import config, new_task, save_slot, submit
from test_workspace_generation import finish


def rename(client, task, title, expected):
    return client.patch('/api/tasks/'+task, json={'title':title,'expected_title':expected})


def test_rename_persists_and_keeps_original_creation_replay(config):
    command={'title':'ORIGINAL','operation_id':str(uuid4()),'overall_requirements':'GLOBAL'}
    with TestClient(create_app(config),base_url='http://localhost') as client:
        task=client.post('/api/tasks',json=command).json()
        other=client.post('/api/tasks',json={'title':'OTHER','operation_id':str(uuid4())}).json()
        changed=rename(client,task['id'],'  NEW NAME  ','ORIGINAL')
        assert changed.status_code==200 and changed.json()['title']=='NEW NAME'
        assert changed.json()['id']==task['id'] and changed.json()['overall_requirement']==task['overall_requirement']
        assert client.get('/api/tasks/'+other['id']).json()==other
        assert client.post('/api/tasks',json=command).json()==changed.json()
        assert rename(client,task['id'],'NEW NAME','ORIGINAL').status_code==200
        assert rename(client,task['id'],'STALE OVERWRITE','ORIGINAL').status_code==409
        assert client.get('/api/tasks/'+task['id']).json()['title']=='NEW NAME'
        for title in ['  ','x'*121,42]:assert rename(client,task['id'],title,'NEW NAME').status_code==422
        assert rename(client,str(uuid4()),'X','Y').status_code==404
    with TestClient(create_app(config),base_url='http://localhost') as client:
        assert client.get('/api/tasks/'+task['id']).json()['title']=='NEW NAME'


def test_soft_delete_keeps_files_context_versions_pass_and_backup(config,tmp_path_factory):
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,*_=new_task(client,'1024x1024')
        submit(client,path);candidate=finish(client,path)['candidates'][0]
        client.post(path+'/versions/'+candidate['image_version_id']+'/reviews',json={
            'operation_id':str(uuid4()),'decision':'PASS','reviewer':'TEST_ONLY','feedback':'keep',
            'expected_current_review_id':None}).raise_for_status()
        other=client.post('/api/tasks',json={'title':'OTHER','operation_id':str(uuid4())}).json()
        service=app.state.generation_service;database=service.database
        tables=['assets','image_slots','task_creations','task_requirements','c01_snapshots','c01_model_runs',
                'image_versions','image_reviews','slot_deliveries','c06_dispatches']
        with database.connection() as db:
            previous={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in tables}
            creation=db.execute('SELECT operation_id,initial_title,initial_overall_requirement FROM task_creations WHERE task_id=?',(task,)).fetchone()
            creation_command={'operation_id':creation[0],'title':creation[1],'overall_requirements':creation[2]}
        asset=client.get('/api/tasks/'+task).json()['assets'][0]
        asset_bytes=client.get(asset['url']).content
        version=service.candidates.inspect(UUID(candidate['image_version_id']))
        version_bytes=Path(version['file']).read_bytes()
        before_files={str(p):p.read_bytes() for p in config.data_dir.rglob('*') if p.is_file() and p.suffix!='.sqlite3' and not p.name.endswith(('-journal','-wal','-shm'))}
        old_title=client.get('/api/tasks/'+task).json()['title']
        assert rename(client,task,'RENAMED',old_title).status_code==200
        assert client.delete('/api/tasks/'+task).status_code==204
        assert client.delete('/api/tasks/'+task).status_code==204
        assert client.get('/api/tasks').json()==[other]
        assert client.get('/api/tasks/'+task).status_code==404
        assert rename(client,task,'RESURRECT','RENAMED').status_code==404
        assert client.post('/api/tasks',json=creation_command).status_code==409
        assert client.post(path+'/generations',json={'operation_id':str(uuid4()),'quote_token':'a'*64}).status_code==404
        assert client.post('/api/tasks/'+task+'/slots',json={'kind':'scene','operation_id':str(uuid4())}).status_code==404
        assert client.get(asset['url']).content==asset_bytes
        assert service.candidates.inspect(UUID(candidate['image_version_id']))==version
        assert Path(version['file']).read_bytes()==version_bytes
        for filename,content in before_files.items():assert Path(filename).read_bytes()==content
        with database.connection() as db:
            for table,rows in previous.items():assert [tuple(r) for r in db.execute('SELECT * FROM '+table)]==rows,table
            assert db.execute('SELECT deleted_at FROM task_drafts WHERE id=?',(task,)).fetchone()[0]
            assert db.execute('SELECT count(*) FROM task_drafts').fetchone()[0]==2
        assert len(images.requests)==1 and not app.state.task_service.orphan_files()
    # 任务隐藏后，原素材/版本/审核仍能完成现有备份和隔离恢复检查。
    archive=tmp_path_factory.mktemp('task-archive')/'archive'
    create(config.data_dir.parent,config.data_dir,archive)
    assert verify(archive)['inventory']['versions_checked']==1
    restored=restore(archive,tmp_path_factory.mktemp('task-restored')/'runtime')
    with TestClient(create_app(replace(config,data_dir=Path(restored['data_dir']))),base_url='http://localhost') as client:
        assert client.get('/api/tasks').json()==[other]
        assert client.get('/api/tasks/'+task).status_code==404


def test_running_and_queued_tasks_cannot_be_deleted(config):
    entered,release=Event(),Event()
    class Paused(ImageSubstitute):
        def send(self,payload):
            entered.set();assert release.wait(10)
            return super().send(payload)
    images=Paused();app=create_app(replace(config,generation_max_workers=1),generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        first,task,*_=new_task(client,'1024x1024')
        second,other,*_=new_task(client,'1024x1024')
        submit(client,first)
        try:
            assert entered.wait(3)
            submit(client,second)
            assert client.get(first+'/workspace').json()['state']=='RUNNING'
            assert client.get(second+'/workspace').json()['state']=='QUEUED'
            for identity in [task,other]:
                body=client.get('/api/tasks/'+identity).json()
                assert not body['can_delete'] and body['delete_blocked_reason']
                assert client.delete('/api/tasks/'+identity).status_code==409
            assert rename(client,task,'ACTIVE RENAMED',client.get('/api/tasks/'+task).json()['title']).status_code==200
        finally:release.set()
        assert finish(client,first)['state']=='SUCCEEDED' and finish(client,second)['state']=='SUCCEEDED'
        assert client.get('/api/tasks/'+task).json()['can_delete']
        assert client.delete('/api/tasks/'+task).status_code==204
        assert client.get('/api/tasks/'+other).status_code==200


def test_unknown_task_cannot_be_deleted_or_implicitly_retried(config):
    images=ImageSubstitute(unknown=True)
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        path,task,*_=new_task(client,'1024x1024');submit(client,path)
        assert finish(client,path)['state']=='OUTCOME_UNKNOWN'
        assert client.delete('/api/tasks/'+task).status_code==409
        assert not client.get('/api/tasks/'+task).json()['can_delete']
        assert len(images.requests)==1


def test_delete_wins_race_before_dispatch_without_model_call(config,monkeypatch):
    entered,release=Event(),Event();images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,*_=new_task(client,'1024x1024');dispatcher=app.state.generation_service.dispatcher
        original=dispatcher.submit
        def delayed(*args,**kwargs):
            entered.set();assert release.wait(10)
            return original(*args,**kwargs)
        monkeypatch.setattr(dispatcher,'submit',delayed)
        quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(client.post,path+'/generations',json={'operation_id':str(uuid4()),'quote_token':quote})
            try:
                assert entered.wait(3)
                assert client.delete('/api/tasks/'+task).status_code==204
            finally:release.set()
            assert future.result().status_code==409
        with app.state.generation_service.database.connection() as db:
            for table in ['c06_dispatches','c01_model_runs','image_versions']:assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        assert not images.requests


def test_deleted_frozen_snapshot_cannot_start_legacy_model_run(config):
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        _,task,slot,*_=new_task(client,'1024x1024');service=app.state.generation_service._direct_service()
        snapshot=service.prepare(UUID(task),UUID(slot),uuid4());flight=service.preflight(UUID(snapshot['id']),MODEL)
        assert client.delete('/api/tasks/'+task).status_code==204
        with pytest.raises(C01Error) as error:
            service.store.start(snapshot,uuid4(),flight['request'],flight['cost'],'UNRESTRICTED_WORKSPACE','OFFLINE_TRANSPORT_TEST')
        assert error.value.code=='TASK_DELETED' and not images.requests


def test_schema_11_upgrade_preserves_rows_and_creation_title(config):
    with TestClient(create_app(config),base_url='http://localhost') as client:
        _,task,*_=new_task(client)
    database=SQLiteTaskStore(config.database_path)
    with database.connection(write=True) as db:
        db.execute('ALTER TABLE task_drafts DROP COLUMN deleted_at')
        db.execute('ALTER TABLE task_creations DROP COLUMN initial_title')
        db.execute('PRAGMA user_version=11')
        before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in ['task_drafts','task_creations','assets','image_slots']}
    database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==13 and not db.execute('PRAGMA foreign_key_check').fetchall()
        for table,rows in before.items():
            actual=[tuple(r) for r in db.execute('SELECT * FROM '+table)]
            if table in {'task_drafts','task_creations'}:actual=[r[:-1] for r in actual]
            assert actual==rows
        assert db.execute('SELECT initial_title FROM task_creations WHERE task_id=?',(task,)).fetchone()[0]==before['task_drafts'][0][1]


def test_all_four_slots_including_dimensions_are_ready(config):
    images=ImageSubstitute()
    with TestClient(create_app(config,generation_transport=images),base_url='http://localhost') as client:
        _,task,_,assets,_,values=new_task(client,'1024x1024')
        for kind in ['dimensions','comparison','selling_point']:
            slot=client.post('/api/tasks/'+task+'/slots',json={'kind':kind,'operation_id':str(uuid4())}).json()['created_slot_id']
            save_slot(client,f'/api/tasks/{task}/slots/{slot}',{**values,'reference_asset_id':assets[1]['id'],
                'requirements':'TEST_ONLY keep the supplied dimension annotations'}).raise_for_status()
        slots=client.get('/api/tasks/'+task).json()['slots']
        assert len(slots)==4 and all(s['inputs_ready'] for s in slots)
        dimensions=next(s for s in slots if s['kind']=='dimensions')
        assert dimensions['reference_asset_id'] and dimensions['generation_blocked_reason'] is None
        for slot in slots:
            state=client.get(f"/api/tasks/{task}/slots/{slot['id']}/workspace").json()
            assert state['readiness']['can_generate']
        assert not images.requests
