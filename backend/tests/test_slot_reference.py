"""Per-slot references and safe removal on real HTTP/SQLite, with explicit TEST_ONLY model tools."""
import base64
import json
import sqlite3
from threading import Event
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from aigc_workbench.persistence import SQLiteTaskStore
from test_quick_a_creation import config,application,new_task,asset,submit,finish
from quick_a_adapters import UnderstandingSubstitute,ImageSubstitute


def add(client,task,kind):
 r=client.post(f'/api/tasks/{task}/slots',json={'kind':kind,'operation_id':str(uuid4())});assert r.status_code==201
 return r.json()['created_slot_id']
def configure(client,task,slot,ref,size='1536x1536'):
 from test_quick_a_creation import save_slot
 return save_slot(client,f'/api/tasks/{task}/slots/{slot}',{'reference_asset_id':ref,'output_size':size,'requirements':'TEST_ONLY preserve this slot'})
def archive(client,task,slot,op=None):
 return client.post(f'/api/tasks/{task}/slots/{slot}/remove',json={'operation_id':str(op or uuid4())})
def review(client,path,v):
 r=client.post(path+'/versions/'+v['image_version_id']+'/reviews',json={'operation_id':str(uuid4()),'decision':'PASS','reviewer':'TEST_ONLY human','feedback':'TEST_ONLY','expected_current_review_id':None});assert r.status_code==201
 return client.get(path+'/workspace').json()['delivery']


def test_upload_result_identity_and_cross_task_binding(config):
 with TestClient(application(config,UnderstandingSubstitute(),ImageSubstitute()),base_url='http://localhost') as client:
  path,task,slot,*_=new_task(client)
  a,_=asset(client,task,'marketing_reference','green')
  from quick_a_adapters import source_png
  op=str(uuid4());url=f'/api/tasks/{task}/assets?role=marketing_reference&operation_id={op}&filename=same.png'
  body=source_png('yellow');first=client.post(url,content=body,headers={'Content-Type':'image/png'})
  assert first.status_code==201;identity=first.json()['uploaded_asset_id']
  asset(client,task,'marketing_reference','purple')
  assert client.post(url,content=body,headers={'Content-Type':'image/png'}).json()['uploaded_asset_id']==identity
  assert configure(client,task,slot,identity).status_code==200
  _,other,other_slot,*_=new_task(client)
  assert configure(client,other,other_slot,identity).status_code==422


def test_dynamic_slot_independence_reference_selection_archive_and_history(config):
 understanding,images=UnderstandingSubstitute(),ImageSubstitute();app=application(config,understanding,images)
 with TestClient(app,base_url='http://localhost') as client:
  _,task,_,_,_,_=new_task(client)
  first,second=add(client,task,'selling_point'),add(client,task,'selling_point')
  a,abytes=asset(client,task,'marketing_reference','green');b,bbytes=asset(client,task,'marketing_reference','purple')
  assert configure(client,task,first,a['id']).status_code==200;assert configure(client,task,second,b['id']).status_code==200
  path=f'/api/tasks/{task}/slots/{second}';submit(client,path);state=finish(client,path);assert state['state']=='SUCCEEDED'
  v=state['candidates'][0];download_before=client.get(v['url']).content;delivery=review(client,path,v)
  assert base64.b64decode(images.requests[-1]['images'][1]['image_url'].split(',')[1])==bbytes
  assert base64.b64decode(understanding.requests[1]['input'][0]['content'][1]['image_url'].split(',')[1])==bbytes
  snapshot_before=app.state.generation_service._service().service.evidence.load_snapshot(UUID(state['executions'][0]['progress']['steps'][-1]['snapshot_id']))
  replacement,_=asset(client,task,'marketing_reference','orange');configure(client,task,first,replacement['id'])
  op=uuid4();assert archive(client,task,first,op).status_code==200;assert archive(client,task,first,op).status_code==200
  assert archive(client,task,second,op).status_code==409
  current=client.get('/api/tasks/'+task).json();other=next(s for s in current['slots'] if s['id']==second)
  assert other['reference_asset_id']==b['id'] and not other['archived']
  assert client.get(path+'/workspace').json()['delivery']==delivery
  assert app.state.generation_service._service().service.evidence.load_snapshot(UUID(snapshot_before['id']))==snapshot_before
  assert archive(client,task,second).status_code==200
  historical=client.get(path+'/workspace').json();assert historical['delivery']==delivery and not historical['readiness']['can_generate']
  assert client.get(delivery['url']).content==download_before
  next_id=add(client,task,'selling_point');assert next_id not in {first,second}
 with TestClient(application(config,understanding,images),base_url='http://localhost') as client:
  task_body=client.get('/api/tasks/'+task).json();assert sum(s['archived'] for s in task_body['slots'])==2
  assert client.get(delivery['url']).content==download_before


def test_fixed_dimensions_inputs_are_ready_without_extra_gate(config):
 understanding,images=UnderstandingSubstitute(),ImageSubstitute()
 with TestClient(application(config,understanding,images),base_url='http://localhost') as client:
  _,task,main,assets,_,_=new_task(client)
  dimensions=add(client,task,'dimensions');assert configure(client,task,dimensions,assets[1]['id']).status_code==200
  duplicate=client.post(f'/api/tasks/{task}/slots',json={'kind':'dimensions','operation_id':str(uuid4())});assert duplicate.status_code==409
  assert archive(client,task,main).status_code==409 and archive(client,task,dimensions).status_code==409
  w=client.get(f'/api/tasks/{task}/slots/{dimensions}/workspace').json();assert w['readiness']['can_generate']
  assert not understanding.requests and not images.requests


def test_cannot_remove_slot_during_active_execution(config):
 entered,release=Event(),Event()
 understanding=UnderstandingSubstitute(pause=('c02_reference_observation',entered,release))
 with TestClient(application(config,understanding,ImageSubstitute()),base_url='http://localhost') as client:
  path,task,slot,*_=new_task(client,kind='selling_point')
  submit(client,path)
  try:
   assert entered.wait(3)
   assert archive(client,task,slot).status_code==409
   assert not next(s for s in client.get('/api/tasks/'+task).json()['slots'] if s['id']==slot)['archived']
  finally:release.set()
  assert finish(client,path)['state']=='SUCCEEDED'


def test_v10_migration_rollback_and_foreign_key_preservation(config,monkeypatch):
 understanding,images=UnderstandingSubstitute(),ImageSubstitute();app=application(config,understanding,images)
 with TestClient(app,base_url='http://localhost') as client:
  path,task,*_=new_task(client);submit(client,path);state=finish(client,path);assert state['state']=='SUCCEEDED'
 database=SQLiteTaskStore(config.database_path)
 with database.connection(write=True) as db:
  db.execute('drop table slot_removals');db.execute('alter table image_slots drop column archived');db.execute('pragma user_version=9')
  names=['task_drafts','assets','image_slots','c01_snapshots','c01_model_runs','image_versions']
  old={n:([r[1] for r in db.execute('pragma table_info('+n+')')],[tuple(r) for r in db.execute('select * from '+n)]) for n in names}
 original=SQLiteTaskStore._upgrade_v10
 def fail(self,db):original(self,db);raise sqlite3.OperationalError('TEST_ONLY migration failure')
 monkeypatch.setattr(SQLiteTaskStore,'_upgrade_v10',fail)
 with pytest.raises(sqlite3.Error):database.initialize()
 with database.connection() as db:
  assert db.execute('pragma user_version').fetchone()[0]==9
  assert 'archived' not in [r[1] for r in db.execute('pragma table_info(image_slots)')]
 monkeypatch.setattr(SQLiteTaskStore,'_upgrade_v10',original);database.initialize()
 with database.connection() as db:
  assert db.execute('pragma user_version').fetchone()[0]==14 and not db.execute('pragma foreign_key_check').fetchall()
  for n,(cols,rows) in old.items():assert [tuple(r) for r in db.execute('select '+','.join(cols)+' from '+n)]==rows
  assert db.execute('pragma foreign_keys').fetchone()[0]==1
