"""尺寸图复用直生图、版本及修改链路；只有 Fake 传输，禁止真实模型。"""
from copy import deepcopy
from uuid import UUID,uuid4

from fastapi.testclient import TestClient
import pytest

from aigc_workbench.app import create_app
from aigc_workbench.c06_generation import MODEL
from aigc_workbench.quick_a_direct import VERSION,PROMPT_VERSION,build_prompt
from quick_a_adapters import ImageSubstitute
from test_quick_a_creation import config,new_task,asset,save_slot,submit,image_bytes
from test_quick_a_direct import snapshot_for,overall
from test_quick_a_context_isolation import NoUnderstanding,DistinctImages
from test_workspace_generation import finish


@pytest.mark.parametrize('slot_text',['','DIMENSION_ONLY 保留参考已有尺寸标注'])
def test_dimensions_use_two_current_images_and_one_c06_without_extra_inputs(config,slot_text):
    images,understanding=ImageSubstitute(),NoUnderstanding()
    app=create_app(config,generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,values=new_task(client,'1024x1024',kind='dimensions')
        save_slot(client,path,{**values,'requirements':slot_text}).raise_for_status()
        other_ref,_=asset(client,task,'marketing_reference','green')
        other=client.post(f'/api/tasks/{task}/slots',json={'kind':'scene','operation_id':str(uuid4())}).json()['created_slot_id']
        save_slot(client,f'/api/tasks/{task}/slots/{other}',{**values,'reference_asset_id':other_ref['id'],'requirements':'SIBLING_ONLY'}).raise_for_status()
        overall(client,task,'GLOBAL_ALL_IMAGES',0)
        ready=client.get(path+'/workspace').json()['readiness'];assert ready['can_generate']
        command=submit(client,path);body=finish(client,path)
        assert body['state']=='SUCCEEDED' and len(images.requests)==1 and not understanding.requests
        version=body['candidates'][0];snapshot,run=snapshot_for(app,version)
        assert snapshot['slot_id']==slot and snapshot['context']['source_state']['slot_state']['kind']=='dimensions'
        assert snapshot['context']['operation_id']==command['operation_id'] and run['capability']=='C06'
        assert snapshot['prompt']['template_version']==PROMPT_VERSION and snapshot['decision_snapshot_id'] is None
        assert image_bytes(images.requests[0])==list(originals)
        prompt=images.requests[0]['prompt']
        for text in ['保留参考图已有的尺寸数值、单位、文字和标注关系','不自行编造新尺寸','GLOBAL_ALL_IMAGES']:
            assert text in prompt
        assert 'SIBLING_ONLY' not in prompt and str(other_ref['id']) not in prompt
        assert '白底' not in prompt and '核对流程' not in prompt
        assert snapshot['context']['effective_requirements']['slot']['text']==slot_text
        assert not version['pass'] and body['delivery'] is None


def test_dimensions_c07_selected_older_base_current_reference_and_existing_pass(config):
    images,understanding=DistinctImages(),NoUnderstanding()
    app=create_app(config,generation_transport=images,understanding_transport=understanding)
    with TestClient(app,base_url='http://localhost') as client:
        path,task,slot,_,originals,values=new_task(client,'1024x1024',kind='dimensions')
        overall(client,task,'GLOBAL_1',0)
        submit(client,path);v1=finish(client,path)['candidates'][0];v1_bytes=client.get(v1['url']).content
        old_snapshot,old_run=snapshot_for(app,v1)
        client.post(path+'/versions/'+v1['image_version_id']+'/reviews',json={
            'operation_id':str(uuid4()),'decision':'PASS','reviewer':'TEST_ONLY','feedback':'keep dimensions',
            'expected_current_review_id':None}).raise_for_status()
        delivery=client.get(path+'/workspace').json()['delivery']
        submit(client,path);v2=finish(client,path)['candidates'][0];v2_bytes=client.get(v2['url']).content
        assert v1_bytes!=v2_bytes
        reference,ref_bytes=asset(client,task,'marketing_reference','purple')
        save_slot(client,path,{**values,'reference_asset_id':reference['id'],'requirements':'CURRENT_DIMENSION_ONLY'}).raise_for_status()
        overall(client,task,'GLOBAL_2',1)
        quote=client.post(path+'/versions/'+v1['image_version_id']+'/revision-quote',json={'revision_text':'CHANGE_LAYOUT_ONLY'}).json()
        command={'operation_id':str(uuid4()),'snapshot_id':quote['snapshot_id'],'quote_token':quote['quote_token']}
        assert client.post(path+'/revisions',json=command).status_code==202
        body=finish(client,path);v3=body['candidates'][0]
        assert body['state']=='SUCCEEDED' and v3['base_image_version_id']==v1['image_version_id']
        assert image_bytes(images.requests[-1])==[v1_bytes,originals[0],ref_bytes]
        assert 'GLOBAL_2' in images.requests[-1]['prompt'] and 'CURRENT_DIMENSION_ONLY' in images.requests[-1]['prompt']
        assert body['delivery']==delivery and not v3['pass']
        assert client.get(v1['url']).content==v1_bytes and client.get(v2['url']).content==v2_bytes
        assert snapshot_for(app,v1)==(old_snapshot,old_run)
        assert len(images.requests)==3 and not understanding.requests
        assert client.post(path+'/revisions',json=command).status_code==202 and len(images.requests)==3


def test_original_direct_prompt_snapshot_still_preflights_and_runs(config):
    images=ImageSubstitute();app=create_app(config,generation_transport=images)
    with TestClient(app,base_url='http://localhost') as client:
        _,task,slot,_,originals,_=new_task(client,'1024x1024')
        service=app.state.generation_service._direct_service();operation=uuid4()
        fresh=service.prepare(UUID(task),UUID(slot),operation)
        legacy=deepcopy(fresh);legacy['id']=str(uuid4())
        legacy['prompt']['template_version']=VERSION
        legacy['prompt']['planned_model_input']['text']=build_prompt(legacy['context'],VERSION)
        assert '保留参考图已有的尺寸数值、单位、文字和标注关系' not in legacy['prompt']['planned_model_input']['text']
        service.service.evidence.save_snapshot(legacy,originals[0])
        flight=app.state.generation_service._service().preflight(UUID(legacy['id']),MODEL)
        assert flight['request']['prompt']==legacy['prompt']['planned_model_input']['text']
        result=service.run(UUID(legacy['id']),MODEL,operation,confirmed=True,max_cost_usd='UNRESTRICTED_WORKSPACE',transport=images)
        assert result['status']=='SUCCEEDED' and len(images.requests)==1
