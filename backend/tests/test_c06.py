"""Offline C06 boundaries; synthetic images/provider records exist only in temporary test stores."""

import base64
from hashlib import sha256
from io import BytesIO
import json
import sqlite3
from uuid import UUID, uuid4

from PIL import Image
import pytest

from test_creation_decision import source, prepare, decision_output
from aigc_workbench.c01_context import C01Error, fingerprint, now
from aigc_workbench.c01_model import HTTPResult
from aigc_workbench.c06_candidates import CandidateStore
from aigc_workbench.c06_generation import C06Service, MODEL, PARAMETERS, estimate_image_cost


def png(size=(1024, 1536)):
    stream = BytesIO()
    Image.new('RGB', size, 'green').save(stream, 'PNG')
    return stream.getvalue()


class SyntheticWire:
    # Counterfactual provider receipt for testing candidate persistence only. Never used in real runtime.
    label = 'REAL_PROVIDER_TRANSPORT'
    def __init__(self, status=200, error=None, size=(1024,1536)):
        self.calls, self.status, self.error, self.size = 0, status, error, size
    def send(self, payload):
        self.calls += 1
        if self.error:
            raise self.error
        self.request = json.loads(payload)
        body = {'data':[{'b64_json':base64.b64encode(png(self.size)).decode()}],
                'usage':{'input_tokens':300,'input_tokens_details':{'text_tokens':100,'image_tokens':200},
                         'output_tokens':1373,'total_tokens':1673}}
        if self.status != 200:
            body = {'error':{'message':'SYNTHETIC_TEST_ONLY','code':'model_access_denied'}}
        return HTTPResult(self.status,json.dumps(body).encode(),'synthetic-request-only')


@pytest.fixture
def generation(source, tmp_path, monkeypatch):
    snapshot = prepare(source)
    base = source['real']
    record,_ = base.store.start(snapshot,uuid4(),{'model':'synthetic_upstream'}, {}, '0.05','REAL_PROVIDER_TRANSPORT')
    result = decision_output()
    record.update(status='SUCCEEDED',attempt_status='SUCCEEDED',finished_at=now(),
                  model_api_call_attempted=True,structured_output={'content':result})
    base.store.save(record)
    directory=tmp_path/'handoff'; directory.mkdir()
    text='SYNTHETIC_TEST_ONLY: preserve both input image identities and all accessories.'
    handoff={'task_id':str(source['task']),'slot_id':str(source['slot']),'output_size':'1024x1536',
             'C06_executed':False,'status':'PROPOSED / REVIEW PENDING / NOT OPERATOR APPROVED',
             'images_in_order':snapshot['context']['image_inputs'],
             'image_creation_decision':{'C06_creation_input':text}}
    files={'05_C06_EXECUTION_HANDOFF.md':text,'05_C06_EXECUTION_HANDOFF.json':json.dumps(handoff),
           '04_QUALITY_AMENDMENTS.md':'SYNTHETIC_QA_ONLY','04_QUALITY_AMENDMENTS.json':'{}',
           'structured-result.json':json.dumps(result)}
    for name,body in files.items(): (directory/name).write_text(body)
    (directory/'delivery-manifest-final.json').write_text(json.dumps({
        name:{'sha256':sha256(body.encode()).hexdigest()} for name,body in files.items()}))
    service=C06Service(base.settings,base.service)
    monkeypatch.setenv('AIGC_ENABLE_C06_REAL','1')
    c06=service.prepare(UUID(snapshot['id']),directory,MODEL)
    return service,c06,directory,source


def run(generation,wire,operation=None,budget='1.00'):
    service,snapshot,_,_=generation
    return service.run(UUID(snapshot['id']),MODEL,operation or uuid4(),confirmed=True,
                       max_cost_usd=budget,transport=wire)


def test_request_uses_both_exact_images_and_full_instruction(generation):
    service,snapshot,directory,original=generation
    preflight=service.preflight(UUID(snapshot['id']),MODEL)
    request=preflight['request']
    for key,value in PARAMETERS.items(): assert request[key]==value
    assert set(request)==set(PARAMETERS)|{'prompt','images'}
    assert request['prompt']==(directory/'05_C06_EXECUTION_HANDOFF.md').read_text()
    for part,blob in zip(request['images'],original['blobs']):
        assert base64.b64decode(part['image_url'].split(',',1)[1])==blob
    assert len(request['images'])==2 and preflight['network_requests']==0
    assert preflight['cost']['official_output_token_estimate']==1372
    assert float(preflight['cost']['planning_envelope_usd'])<1


def test_candidate_is_durable_immutable_and_replay_has_no_second_post(generation):
    wire=SyntheticWire(); operation=uuid4()
    result=run(generation,wire,operation)
    assert result['status']=='SUCCEEDED' and result['response_http_status']==200
    assert result['endpoint']=='https://api.openai.com/v1/images/edits'
    assert result['usage_based_cost_estimate']['usd']=='0.04329'
    candidate=result['candidate']; service,_,_,_=generation
    store=CandidateStore(service.store.database,service.settings.data_dir)
    assert store.inspect(UUID(candidate['image_version_id']))==candidate
    assert candidate['status']=='CANDIDATE_REVIEW_PENDING' and candidate['pass'] is False
    assert candidate['image']['width']==1024 and candidate['image']['height']==1536
    assert run(generation,wire,operation)==result and wire.calls==1
    with pytest.raises(sqlite3.IntegrityError):
        with service.store.database.connection(write=True) as db:
            db.execute('UPDATE image_versions SET status=status')


@pytest.mark.parametrize('status',[403,429,500])
def test_http_errors_are_retained_without_retry(generation,status):
    wire=SyntheticWire(status=status); operation=uuid4()
    result=run(generation,wire,operation)
    assert result['status']==('OUTCOME_UNKNOWN' if status>=500 else 'FAILED')
    assert result['response_http_status']==status and result['actual_model_response']['error']
    assert 'candidate' not in result
    assert run(generation,wire,operation)==result and wire.calls==1


def test_timeout_unknown_no_replay(generation):
    wire=SyntheticWire(error=TimeoutError('synthetic')); operation=uuid4()
    result=run(generation,wire,operation)
    assert result['status']=='OUTCOME_UNKNOWN' and result['actual_model_response'] is None
    assert run(generation,wire,operation)==result and wire.calls==1


def test_wrong_size_retains_response_without_candidate(generation):
    result=run(generation,SyntheticWire(size=(1024,1024)))
    assert result['status']=='FAILED' and result['error']['code']=='C06_OUTPUT_FORMAT_INVALID'
    assert result['raw_response_text'] and 'candidate' not in result


@pytest.mark.parametrize('case',['disabled','budget_low','budget_high','handoff_changed','slot_changed','missing_key'])
def test_preconditions_fail_before_dispatch(generation,monkeypatch,case):
    wire=SyntheticWire(); service,snapshot,directory,original=generation
    budget='1.00'
    if case=='disabled': monkeypatch.delenv('AIGC_ENABLE_C06_REAL')
    if case=='budget_low': budget='0.01'
    if case=='budget_high': budget='1.01'
    if case=='handoff_changed': (directory/'04_QUALITY_AMENDMENTS.md').write_text('changed')
    if case=='missing_key': monkeypatch.delenv('OPENAI_API_KEY')
    if case=='slot_changed':
        service.service.tasks.configure_slot(original['task'],original['slot'],original['assets'][1].id,
                                            '1024x1536','DIFFERENT_REQUIREMENT')
    with pytest.raises(C01Error): run(generation,wire,budget=budget)
    assert wire.calls==0


def test_candidate_save_failure_preserves_provider_result_and_recovers_without_call(generation,monkeypatch):
    wire=SyntheticWire(); operation=uuid4(); saved=CandidateStore.save
    def fail(*args): raise OSError('synthetic local save failure')
    monkeypatch.setattr(CandidateStore,'save',fail)
    with pytest.raises(OSError): run(generation,wire,operation)
    monkeypatch.setattr(CandidateStore,'save',saved)
    result=run(generation,wire,operation)
    assert wire.calls==1 and result['candidate']['pass'] is False


def test_v4_upgrade_rolls_back_on_error_and_preserves_existing_records(generation,monkeypatch):
    import aigc_workbench.persistence as persistence
    service,_,_,_=generation
    database=service.store.database
    with database.connection(write=True) as db:
        db.execute('DROP TABLE task_requirements')
        db.execute('ALTER TABLE task_creations DROP COLUMN initial_overall_requirement')
        db.execute('DROP TABLE slot_deliveries')
        db.execute('DROP TABLE image_reviews')
        db.execute('DROP TABLE image_versions')
        db.execute('DROP TABLE c06_dispatches')
        db.execute('PRAGMA user_version=4')
        before={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in
                ['task_drafts','assets','image_slots','c01_snapshots','c01_model_runs']}
    original=persistence.V5_TABLES
    monkeypatch.setattr(persistence,'V5_TABLES',original+['INVALID SQL'])
    with pytest.raises(sqlite3.Error): database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==4
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='image_versions'").fetchone()[0]==0
    monkeypatch.setattr(persistence,'V5_TABLES',original)
    database.initialize()
    with database.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==12
        for name,rows in before.items(): assert [tuple(row) for row in db.execute('SELECT * FROM '+name)]==rows
