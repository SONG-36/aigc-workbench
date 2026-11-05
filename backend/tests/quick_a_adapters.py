"""Explicit TEST_ONLY substitutes shared by isolated integration and browser tests."""
import base64
from io import BytesIO
import json
import time

from PIL import Image, ImageDraw

from aigc_workbench.c01_model import HTTPResult


def shape(schema):
    if schema['type'] == 'string': return schema.get('enum', ['TEST_ONLY'])[0]
    if schema['type'] == 'array': return []
    return {key: shape(value) for key, value in schema['properties'].items()}


def source_png(color='white', size=(64, 64)):
    image = Image.new('RGB', size, color)
    ImageDraw.Draw(image).text((8, 8), 'TEST_ONLY', fill='black')
    stream = BytesIO(); image.save(stream, 'PNG')
    return stream.getvalue()


class UnderstandingSubstitute:
    label = 'OFFLINE_TRANSPORT_TEST'
    def __init__(self, *, fail=None, unknown=None, pause=None, delay=0):
        self.requests, self.fail, self.unknown, self.pause, self.delay = [], fail, unknown, pause, delay

    def send(self, payload):
        request = json.loads(payload)
        name = request['text']['format']['name']
        self.requests.append(request)
        if self.delay: time.sleep(self.delay)
        if self.pause and name == self.pause[0]:
            self.pause[1].set()
            assert self.pause[2].wait(10)
        if name == self.unknown: raise TimeoutError('TEST_ONLY outcome unknown')
        if name == self.fail: return HTTPResult(429, b'{"error":{"code":"TEST_ONLY_FAILURE"}}', 'test-only')
        schema = request['text']['format']['schema']
        output = shape(schema)
        if name == 'c01_visual_observation':
            output['components'] = ['TEST_ONLY_PRODUCT_COMPONENT']
        elif name == 'c02_reference_observation':
            output['marketing_claims'] = [{'id': 'ref-claim-X', 'original_text': 'TEST_ONLY 25 units',
                'meaning_in_reference': 'TEST_ONLY marketing claim, not certified product fact',
                'related_text_ids': ['text-X'], 'related_product_ids': ['object-X']}]
            output['visible_text'] = [{'id': 'text-X', 'text': 'TEST_ONLY 25 units', 'location': 'TEST_ONLY top'}]
        else:
            decision = output['02_IMAGE_CREATION_DECISION']
            item_schema = schema['properties']['02_IMAGE_CREATION_DECISION']['properties']['C04_MARKETING_CONTENT']['properties']['claim_decisions']['items']
            for identity in item_schema['properties']['claim_id'].get('enum', []):
                item = shape(item_schema); item['claim_id'] = identity
                decision['C04_MARKETING_CONTENT']['claim_decisions'].append(item)
            decision['C06_creation_input'] = 'TEST_ONLY: use actual product Image 1 and reference Image 2; preserve all visible accessories and relationships.'
        response = {'model': request['model'], 'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(output)}]}], 'usage': {'input_tokens': 10, 'output_tokens': 20}}
        return HTTPResult(200, json.dumps(response).encode(), 'test-only-understanding')


class ImageSubstitute:
    label = 'OFFLINE_TRANSPORT_TEST'
    def __init__(self, *, fail=False, unknown=False, delay=0):
        self.requests, self.fail, self.unknown, self.delay = [], fail, unknown, delay

    def send(self, payload):
        request = json.loads(payload); self.requests.append(request)
        if self.delay: time.sleep(self.delay)
        if self.unknown: raise TimeoutError('TEST_ONLY image outcome unknown')
        if self.fail: return HTTPResult(403, b'{"error":{"code":"TEST_ONLY_IMAGE_FAILURE"}}', 'test-only-image')
        blob = source_png('#d9e7ef', tuple(map(int, request['size'].split('x'))))
        return HTTPResult(200, json.dumps({'model': request['model'], 'data': [
            {'b64_json': base64.b64encode(blob).decode()}], 'usage': {'output_tokens': 10}}).encode(), 'test-only-image')
