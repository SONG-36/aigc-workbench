"""Offline Pilot contract, network boundary, immutable restore and existing one-shot Runtime."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from test_creation_decision import source
from test_c06 import generation, SyntheticWire
from test_c07_review import revision
from test_workspace_generation import route, finish
from aigc_workbench.app import create_app
from aigc_workbench.c01_context import C01Error
from aigc_workbench.c01_execution import C01Service, require_development
from aigc_workbench.c01_persistence import C01EvidenceStore
from aigc_workbench.c07_revision import C07Service
from aigc_workbench.c06_generation import C06Service, MODEL
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.pilot_backup import create, restore, verify, inventory
from aigc_workbench.pilot_server import settings_from_file
from aigc_workbench.source_paths import archived_path
from aigc_workbench.application import TaskService
from aigc_workbench.file_storage import LocalImageFiles
from aigc_workbench.persistence import SQLiteTaskStore


@pytest.fixture
def pilot(revision, tmp_path, tmp_path_factory):
    g, service, v1 = revision
    dist = tmp_path_factory.mktemp('public-frontend')
    (dist/'assets').mkdir();(dist/'assets/app.js').write_text('/* synthetic production build */')
    (dist/'index.html').write_text('<!doctype html><title>Synthetic Pilot</title>')
    settings = replace(g[0].settings, environment='pilot', enable_c01_testing=False, enable_workspace_generation=True,
        runtime_root=tmp_path, frontend_dist=dist, pilot_hosts=('192.168.112.37','127.0.0.1','localhost'),
        pilot_networks=('192.168.112.0/24','127.0.0.0/8','::1/128'))
    return settings, g, service, v1


def client_for(settings, wire=None, peer='192.168.112.3', host='http://192.168.112.37:8030'):
    return TestClient(create_app(settings,generation_transport=wire),base_url=host,client=(peer,45000))


def test_pilot_no_login_no_fee_approval_no_cap_but_cost_is_observed(pilot, monkeypatch):
    settings,g,service,v1=pilot;wire=SyntheticWire();path=route(g)
    import aigc_workbench.c06_generation as c06
    original=c06.estimate_image_cost
    def expensive(*args,**kwargs):
        return {**original(*args,**kwargs),'planning_envelope_usd':'999.00'}
    monkeypatch.setattr(c06,'estimate_image_cost',expensive)
    with client_for(settings,wire) as client:
        assert client.get('/').status_code==200
        ready=client.get(path+'/workspace').json()['readiness']
        assert ready['can_generate'] and ready['max_cost_usd'] is None and ready['estimate_usd']=='999.00'
        operation=str(uuid4()); body={'operation_id':operation,'quote_token':ready['quote_token']}
        assert client.post(path+'/generations',json=body).status_code==202
        result=finish(client,path)
        assert result['state']=='SUCCEEDED' and wire.calls==1
        assert result['executions'][0]['usage_cost_estimate']['usd']=='0.04329'
        assert client.post(path+'/generations',json=body).status_code==202 and wire.calls==1
        run=service.store.inspect(UUID(result['executions'][0]['run_id']))
        assert run['user_max_cost_usd'] is None and run['execution_policy']=='UNRESTRICTED_INTERNAL_PILOT'
        assert run['cost_preflight']['planning_envelope_usd']=='999.00'
        assert run['automatic_retries']==0


@pytest.mark.parametrize('path',['/docs','/redoc','/openapi.json','/.runtime/f01/workbench.sqlite3','/backend/.env.local',
    '/api/c01/snapshots','/api/model-runs','/api/evidence','/assets/../backend/.env.local','/workspace/not-a-uuid'])
def test_debug_evidence_and_arbitrary_server_files_not_public(pilot,path):
    settings,*_=pilot
    with client_for(settings,SyntheticWire()) as client:
        assert client.get(path).status_code==404


def test_network_host_origin_and_forwarded_headers_are_enforced_without_auth(pilot):
    settings,g,*_=pilot
    with client_for(settings,SyntheticWire(),peer='10.10.1.2') as client:
        assert client.get('/api/tasks').status_code==403
    with client_for(settings,SyntheticWire(),host='http://untrusted.example') as client:
        assert client.get('/api/tasks').status_code==403
    with client_for(settings,SyntheticWire()) as client:
        assert client.get('/api/tasks').status_code==200
        assert client.get('/api/tasks',headers={'X-Forwarded-For':'192.168.112.3'}).status_code==403
        assert client.post('/api/tasks',json={'title':'x','operation_id':str(uuid4())},headers={'Origin':'https://outside.example'}).status_code==403
        assert client.post('/api/tasks',json={'title':'unauthenticated internal task','operation_id':str(uuid4())},
            headers={'Origin':'http://192.168.112.37:8030'}).status_code==201
        assert b'planned_model_input' not in client.get(route(g)+'/workspace').content
        assert b'source_provenance' not in client.get(route(g)+'/workspace').content
        with pytest.raises(C01Error):require_development(settings)


def test_revision_without_fee_step_manual_label_and_current_delivery(pilot):
    settings,g,service,v1=pilot;path=route(g);wire=SyntheticWire()
    with client_for(settings,wire) as client:
        q=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'Only synthetic color'}).json()
        assert q['max_cost_usd'] is None
        operation=str(uuid4());body={k:q[k] for k in ('snapshot_id','quote_token')};body['operation_id']=operation
        assert client.post(path+'/revisions',json=body).status_code==202
        result=finish(client,path);v2=result['candidates'][0]
        assert v2['base_image_version_id']==v1['image_version_id'] and result['delivery'] is None
        assert client.post(path+'/revisions',json=body).status_code==202 and wire.calls==1
        passed=client.post(path+'/versions/'+v2['image_version_id']+'/reviews',json={
            'operation_id':str(uuid4()),'decision':'PASS','reviewer':'Unverified tester label',
            'feedback':'Synthetic only','expected_current_review_id':None})
        assert passed.status_code==201 and passed.json()['reviewer_identity']=='UNVERIFIED_OPERATOR_LABEL'
        assert client.get(path+'/workspace').json()['delivery']['image_version_id']==v2['image_version_id']


@pytest.mark.parametrize('networks',[(),('0.0.0.0/0',),('8.8.8.0/24',)])
def test_pilot_start_requires_explicit_internal_network_scope(pilot,networks):
    settings,*_=pilot
    with pytest.raises(ValueError):create_app(replace(settings,pilot_networks=networks))


def test_complete_backup_restore_and_portable_preflight_without_rewriting_snapshots(pilot,tmp_path_factory,monkeypatch):
    settings,g,service,v1=pilot;path=route(g);wire=SyntheticWire()
    with client_for(settings,wire) as client:
        q=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'Preserve this synthetic base'}).json()
        assert client.post(path+'/revisions',json={'operation_id':str(uuid4()),'snapshot_id':q['snapshot_id'],'quote_token':q['quote_token']}).status_code==202
        result=finish(client,path);v2=result['candidates'][0]
        review=client.post(path+'/versions/'+v2['image_version_id']+'/reviews',json={'operation_id':str(uuid4()),
            'decision':'PASS','reviewer':'Synthetic restore check','feedback':'TEST ONLY','expected_current_review_id':None}).json()
        # A running process cannot be backed up accidentally.
        with pytest.raises(ValueError):create(settings.runtime_root,settings.data_dir,tmp_path_factory.mktemp('blocked')/'archive')
    target=tmp_path_factory.mktemp('backup-target');archive=target/'backup';restored=target/'restored-runtime'
    before=inventory(settings.data_dir)
    manifest=create(settings.runtime_root,settings.data_dir,archive)
    assert manifest['inventory']==before
    restoration=restore(archive,restored)
    assert restoration['inventory']==before and verify(archive)['inventory']==before
    with pytest.raises(ValueError):restore(archive,restored)
    new_settings=replace(settings,data_dir=Path(restoration['data_dir']),runtime_root=restored,archived_runtime_root=settings.runtime_root)
    db=SQLiteTaskStore(new_settings.database_path)
    tasks=TaskService(db,LocalImageFiles(new_settings.data_dir))
    base=C01Service(new_settings,tasks,C01EvidenceStore(db,new_settings.data_dir))
    # Remove access to the former root to prove requests use the configured mapping, not old files.
    original_read_bytes=Path.read_bytes;original_read_text=Path.read_text
    def reject_old_bytes(p,*a,**k):
        assert not p.is_relative_to(settings.runtime_root),'attempted to read original runtime'
        return original_read_bytes(p,*a,**k)
    def reject_old_text(p,*a,**k):
        assert not p.is_relative_to(settings.runtime_root),'attempted to read original runtime'
        return original_read_text(p,*a,**k)
    monkeypatch.setattr(Path,'read_bytes',reject_old_bytes);monkeypatch.setattr(Path,'read_text',reject_old_text)
    C06Service(new_settings,base).preflight(UUID(g[1]['id']),MODEL)
    flight=C07Service(new_settings,base).preflight(UUID(q['snapshot_id']),MODEL)
    assert flight['snapshot']['context']['revision_text']=='Preserve this synthetic base'
    with client_for(new_settings,wire) as client:
        restored_view=client.get(path+'/workspace').json()
        assert restored_view['delivery']['id']==review['id']
        assert [c['image_version_id'] for c in restored_view['candidates']]==[v2['image_version_id'],v1['image_version_id']]
        assert client.get(restored_view['delivery']['url']).content==CandidateStore(db,new_settings.data_dir)._read_png(UUID(v2['image_version_id']))
        assert wire.calls==1


def test_backup_detects_tampering_and_never_overwrites(pilot,tmp_path_factory):
    settings,*_=pilot;archive=tmp_path_factory.mktemp('archive-parent')/'archive'
    create(settings.runtime_root,settings.data_dir,archive)
    with pytest.raises(ValueError):create(settings.runtime_root,settings.data_dir,archive)
    (archive/'runtime'/next(iter(json.loads((archive/'manifest.json').read_text())['files']))).write_bytes(b'tamper')
    with pytest.raises(ValueError):verify(archive)


def test_archive_mapping_cannot_escape_runtime_or_follow_symlink(pilot,tmp_path):
    settings,*_=pilot
    with pytest.raises(C01Error):archived_path(settings,'/etc/passwd')
    with pytest.raises(C01Error):archived_path(settings,settings.runtime_root/'..'/'outside')
    link=tmp_path/'unsafe';link.symlink_to('/etc/passwd')
    with pytest.raises(C01Error):archived_path(settings,link)


def test_private_pilot_config_requires_owner_only_permissions(pilot,tmp_path,monkeypatch):
    monkeypatch.setenv("AIGC_PRIVATE_KEY_FILE", "synthetic-prior-value")
    settings,*_=pilot;p=tmp_path/'pilot.json'
    p.write_text(json.dumps({'bind_host':'192.168.112.37','port':8030,'hosts':list(settings.pilot_hosts),
        'networks':list(settings.pilot_networks),'runtime_root':str(settings.runtime_root),
        'data_dir':str(settings.data_dir),'frontend_dist':str(settings.frontend_dist),'key_file':str(tmp_path/'private.env')}))
    p.chmod(0o644)
    with pytest.raises(ValueError):settings_from_file(p)
    p.chmod(0o600);configured,host,port=settings_from_file(p)
    assert configured.environment=='pilot' and configured.enable_c01_testing is False and host=='192.168.112.37' and port==8030


@pytest.mark.parametrize('state,expected',[('QUEUED','FAILED'),('RUNNING','OUTCOME_UNKNOWN')])
def test_pilot_restart_classifies_unfinished_dispatch_without_replay(pilot,state,expected):
    from aigc_workbench.c01_context import now
    settings,g,service,_=pilot;operation=str(uuid4());stamp=now();wire=SyntheticWire()
    with service.store.database.connection(write=True) as db:
        db.execute('INSERT INTO c06_dispatches VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',(
            operation,g[1]['task_id'],g[1]['slot_id'],g[1]['id'],'a'*64,'UNRESTRICTED_PILOT',state,'TEST_SUBSTITUTE',stamp,stamp))
    with client_for(settings,wire) as client:
        view=client.get(route(g)+'/workspace').json()
        assert view['state']==expected and wire.calls==0
        assert len(view['candidates'])==1
        assert not view['readiness']['can_generate'] if state=='RUNNING' else view['readiness']['can_generate']


def test_pilot_refuses_empty_database_instead_of_hiding_missing_restore(pilot,tmp_path_factory):
    settings,*_=pilot;root=tmp_path_factory.mktemp('missing-runtime')
    with pytest.raises(ValueError):create_app(replace(settings,runtime_root=root,data_dir=root/'f01'))
    assert not (root/'f01'/'workbench.sqlite3').exists()
