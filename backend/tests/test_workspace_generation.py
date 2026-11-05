"""Workspace integration over isolated synthetic stores/tools. No real inference or user-runtime writes."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import UUID,uuid4

from fastapi.testclient import TestClient
import pytest

from test_creation_decision import source
from test_c06 import generation,SyntheticWire,run
from aigc_workbench.app import create_app
from aigc_workbench.c01_context import now
from aigc_workbench.c01_model_store import C01ModelStore
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.persistence import SQLiteTaskStore
from aigc_workbench.domain import SlotKind


def settings(generation):
    return replace(generation[0].settings,enable_workspace_generation=True)


def route(generation):
    return f"/api/tasks/{generation[1]['task_id']}/slots/{generation[1]['slot_id']}"


def paid(client,path,operation=None,quote=None,**extra):
    ready=client.get(path+'/workspace').json()['readiness']
    return client.post(path+'/generations',json={
        'operation_id':str(operation or uuid4()),'quote_token':quote or ready['quote_token'],
        'confirm_paid':True,'max_cost_usd':'1.00',**extra})


def finish(client,path):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        body=client.get(path+'/workspace').json()
        if body['state']!='RUNNING': return body
        time.sleep(.01)
    raise AssertionError('isolated substitute did not finish')


def test_existing_candidate_scoped_png_and_restart_recovery_without_dispatch(generation):
    initial=run(generation,SyntheticWire())['candidate']; path=route(generation); wire=SyntheticWire()
    for _ in range(2):
        with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
            body=client.get(path+'/workspace').json(); candidate=body['candidates'][0]
            assert candidate['image_version_id']==initial['image_version_id']
            assert body['state']=='SUCCEEDED' and body['readiness']['can_generate']
            response=client.get(candidate['url'])
            assert response.status_code==200 and response.headers['content-type']=='image/png'
            assert response.content==Path(initial['file']).read_bytes()
            assert response.headers['etag']=='"'+initial['image']['sha256']+'"'
            assert 'base64' not in json.dumps(body) and initial['file'] not in json.dumps(body)
            assert wire.calls==0 and candidate['pass'] is False


def test_wrong_task_or_slot_cannot_open_candidate(generation):
    candidate=run(generation,SyntheticWire())['candidate']; service,_,_,original=generation
    other=service.service.tasks.create_slot(original['task'],uuid4(),SlotKind.SCENE)
    with TestClient(create_app(settings(generation)),base_url='http://localhost') as client:
        for task,slot in [(uuid4(),original['slot']),(original['task'],other.id)]:
            url=f"/api/tasks/{task}/slots/{slot}/versions/{candidate['image_version_id']}/content"
            assert client.get(url).status_code==404


def test_corrupt_candidate_is_never_returned_as_a_png(generation):
    candidate=run(generation,SyntheticWire())['candidate']
    Path(candidate['file']).write_bytes(b'not the immutable candidate')
    with TestClient(create_app(settings(generation)),base_url='http://localhost') as client:
        response=client.get(route(generation)+f"/versions/{candidate['image_version_id']}/content")
        assert response.status_code==503 and response.headers['content-type']=='application/json'


def test_one_explicit_submission_returns_promptly_and_poll_replay_never_recharges(generation):
    entered,release=Event(),Event()
    class SlowWire(SyntheticWire):
        def send(self,payload):
            entered.set(); assert release.wait(4)
            return super().send(payload)
    wire=SlowWire(); path=route(generation); operation=uuid4()
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        try:
            assert paid(client,path,operation,quote).status_code==202
            assert entered.wait(2)
            for _ in range(3): assert client.get(path+'/workspace').json()['state']=='RUNNING'
            assert paid(client,path,operation,quote).status_code==202
            assert paid(client,path,uuid4(),quote).status_code==409
            assert paid(client,path,operation,'a'*64).status_code==409
        finally: release.set()
        body=finish(client,path)
        assert body['state']=='SUCCEEDED' and len(body['candidates'])==1
        assert body['executions'][0]['execution_mode']=='TEST_SUBSTITUTE'
        assert paid(client,path,operation,quote).status_code==202
        assert wire.calls==1 and not body['candidates'][0]['pass']


@pytest.mark.parametrize('status',[403,429,500])
def test_server_errors_and_unknown_are_not_success_and_have_no_implicit_retry(generation,status):
    wire=SyntheticWire(status=status);path=route(generation);op=uuid4()
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        assert paid(client,path,op,quote).status_code==202
        body=finish(client,path)
        assert body['state']==('OUTCOME_UNKNOWN' if status>=500 else 'FAILED')
        assert body['executions'][0]['http_status']==status and body['executions'][0]['error']
        assert not body['candidates']
        for _ in range(2):
            client.get(path+'/workspace'); assert paid(client,path,op,quote).status_code==202
        assert wire.calls==1
        if status>=500: assert not body['readiness']['can_generate']


def test_missing_decision_changed_source_and_unconfirmed_payment_do_not_dispatch(generation):
    wire=SyntheticWire();path=route(generation);service,_,_,original=generation
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        assert paid(client,path,confirm_paid=False).status_code==422
        other=service.service.tasks.create_slot(original['task'],uuid4(),SlotKind.SCENE)
        missing=f"/api/tasks/{original['task']}/slots/{other.id}"
        state=client.get(missing+'/workspace').json()
        assert not state['readiness']['can_generate'] and '营销参考' in state['readiness']['reason']
        quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        service.service.tasks.configure_slot(original['task'],original['slot'],original['assets'][1].id,
                                             '1024x1536','changed current requirement')
        assert paid(client,path,quote=quote).status_code==409
        assert wire.calls==0


def test_disabled_and_second_process_can_read_but_not_dispatch(generation,monkeypatch):
    wire=SyntheticWire();path=route(generation)
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as first:
        assert first.get(path+'/workspace').json()['readiness']['can_generate']
        with TestClient(create_app(settings(generation)),base_url='http://localhost') as second:
            assert not second.get(path+'/workspace').json()['readiness']['can_generate']
        monkeypatch.delenv('AIGC_ENABLE_C06_REAL')
        assert not first.get(path+'/workspace').json()['readiness']['can_generate']
        assert paid(first,path,quote='a'*64).status_code==503
    assert wire.calls==0


@pytest.mark.parametrize('before',['QUEUED','RUNNING'])
def test_restart_never_replays_interrupted_receipts(generation,before):
    wire=SyntheticWire();service,snapshot,_,_=generation;database=service.store.database
    operation=uuid4();t=now()
    with database.connection(write=True) as db:
        db.execute('INSERT INTO c06_dispatches (operation_id,task_id,slot_id,snapshot_id,quote_token,max_cost_usd,state,execution_mode,created_at,updated_at,error_json) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',(
            str(operation),snapshot['task_id'],snapshot['slot_id'],snapshot['id'],'a'*64,'1.00',before,
            'TEST_SUBSTITUTE',t,t))
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        state=client.get(route(generation)+'/workspace').json()
        assert state['state']==('FAILED' if before=='QUEUED' else 'OUTCOME_UNKNOWN')
        assert wire.calls==0


def test_v5_migration_rolls_back_and_preserves_candidate_and_evidence(generation,monkeypatch):
    import aigc_workbench.persistence as persistence
    candidate=run(generation,SyntheticWire())['candidate'];database=generation[0].store.database
    with database.connection(write=True) as db:
        db.execute('DROP TABLE slot_deliveries')
        db.execute('DROP TABLE image_reviews')
        db.execute('DROP TABLE c06_dispatches');db.execute('PRAGMA user_version=5')
        before={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in
                ['task_drafts','assets','image_slots','c01_snapshots','c01_model_runs','image_versions']}
    original=persistence.V6_TABLES
    monkeypatch.setattr(persistence,'V6_TABLES',original+['INVALID SQL'])
    with pytest.raises(sqlite3.Error):database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==5
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='c06_dispatches'").fetchall()
    monkeypatch.setattr(persistence,'V6_TABLES',original);database.initialize()
    with database.connection() as db:
        for name,rows in before.items(): assert [tuple(row) for row in db.execute('SELECT * FROM '+name)]==rows
    assert CandidateStore(database,generation[0].settings.data_dir).inspect(UUID(candidate['image_version_id']))==candidate


def test_completed_cli_operation_cannot_be_reused_as_a_new_workspace_payment(generation):
    previous=run(generation,SyntheticWire());wire=SyntheticWire();path=route(generation)
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        assert paid(client,path,UUID(previous['operation_id'])).status_code==409
        assert wire.calls==0


@pytest.mark.parametrize('completed',[False,True])
def test_restart_reconciles_existing_runtime_without_modifying_or_replaying_it(generation,completed):
    service,snapshot,_,_=generation;wire=SyntheticWire();database=service.store.database
    if completed:
        previous=run(generation,SyntheticWire())
    else:
        preflight=service.preflight(UUID(snapshot['id']),'gpt-image-2.6-sunburst')
        previous,_=service.store.start(snapshot,uuid4(),preflight['request'],preflight['cost'],'1.00','REAL_PROVIDER_TRANSPORT')
    t=now()
    with database.connection(write=True) as db:
        db.execute('INSERT INTO c06_dispatches (operation_id,task_id,slot_id,snapshot_id,quote_token,max_cost_usd,state,execution_mode,created_at,updated_at,error_json) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',(
            previous['operation_id'],snapshot['task_id'],snapshot['slot_id'],snapshot['id'],'a'*64,'1.00','RUNNING',
            'TEST_SUBSTITUTE',t,t))
    original=service.store.inspect(UUID(previous['run_id']))
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        body=client.get(route(generation)+'/workspace').json()
        assert body['state']==('SUCCEEDED' if completed else 'OUTCOME_UNKNOWN')
        assert wire.calls==0
        assert service.store.inspect(UUID(previous['run_id']))==original


def test_two_tabs_compete_for_one_active_slot_and_only_one_tool_is_called(generation):
    entered,release=Event(),Event()
    class HeldWire(SyntheticWire):
        def send(self,payload):
            entered.set();assert release.wait(4)
            return super().send(payload)
    wire=HeldWire();path=route(generation)
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        quote=client.get(path+'/workspace').json()['readiness']['quote_token']
        try:
            with ThreadPoolExecutor(max_workers=2) as tabs:
                futures=[tabs.submit(paid,client,path,uuid4(),quote) for _ in range(2)]
                statuses=[future.result().status_code for future in futures]
            assert sorted(statuses)==[202,409]
            assert entered.wait(2)
        finally:release.set()
        assert finish(client,path)['state']=='SUCCEEDED' and wire.calls==1


def test_model_result_without_local_candidate_blocks_new_payment_until_local_save_recovers(generation,monkeypatch):
    wire=SyntheticWire();path=route(generation);save=CandidateStore.save
    def fail(*args):raise OSError('synthetic disk error')
    monkeypatch.setattr(CandidateStore,'save',fail)
    with TestClient(create_app(settings(generation),generation_transport=wire),base_url='http://localhost') as client:
        assert paid(client,path).status_code==202
        body=finish(client,path)
        assert body['state']=='FAILED' and body['executions'][0]['error']['code']=='CANDIDATE_NOT_SAVED'
        assert not body['readiness']['can_generate'] and wire.calls==1
        monkeypatch.setattr(CandidateStore,'save',save)
        operation=body['executions'][0]['operation_id']
        recovered=generation[0].run(UUID(generation[1]['id']),'gpt-image-2.6-sunburst',UUID(operation),
                                  confirmed=True,max_cost_usd='1.00',transport=wire)
        assert recovered['candidate'] and wire.calls==1
        assert client.get(path+'/workspace').json()['state']=='SUCCEEDED'


def test_runtime_terminal_error_is_displayed_before_dispatch_callback_finishes(generation):
    service,snapshot,_,_=generation
    prior=run(generation,SyntheticWire(status=403))
    with TestClient(create_app(settings(generation)),base_url='http://localhost') as client:
        t=now()
        with service.store.database.connection(write=True) as db:
            db.execute('INSERT INTO c06_dispatches (operation_id,task_id,slot_id,snapshot_id,quote_token,max_cost_usd,state,execution_mode,created_at,updated_at,error_json) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',(
                prior['operation_id'],snapshot['task_id'],snapshot['slot_id'],snapshot['id'],'a'*64,'1.00',
                'RUNNING','TEST_SUBSTITUTE',t,t))
        body=client.get(route(generation)+'/workspace').json()
        assert body['state']=='FAILED' and body['executions'][0]['http_status']==403
