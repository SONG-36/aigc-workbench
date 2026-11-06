"""Terra request/parse/cost compatibility; no network or real model calls."""
from decimal import Decimal
import json

import pytest

from aigc_workbench.c01_context import C01Error
from aigc_workbench.c01_model import (DEFAULT_UNDERSTANDING_MODEL as TERRA, MODEL, multimodal_request,
    parse_response, estimate_cost, usage_cost)
from aigc_workbench.c01_prompt import OUTPUT_CONTRACT, OUTPUT_FIELDS


def request(model=TERRA, tokens=8192):
    return multimodal_request('unchanged four-layer text', b'real-source-placeholder', 'image/jpeg',
        OUTPUT_CONTRACT, 'c01_visual_observation', model, tokens)


def test_terra_reasoning_preserves_images_text_schema_and_legacy_request():
    body = request()
    assert body['model'] == TERRA and body['reasoning'] == {'effort': 'medium'}
    assert 'temperature' not in body and 'top_p' not in body
    assert body['max_output_tokens'] == 8192 and body['store'] is False and body['stream'] is False
    assert body['input'][0]['content'][0]['text'] == 'unchanged four-layer text'
    assert body['input'][0]['content'][1]['image_url'].startswith('data:image/jpeg;base64,')
    assert body['text']['format']['schema'] == OUTPUT_CONTRACT and body['text']['format']['strict']
    assert request(MODEL, 4096)['temperature'] == 0
    with pytest.raises(C01Error): request(MODEL, 8192)
    with pytest.raises(C01Error): request(TERRA, 16385)
    assert request(TERRA, 16384)['max_output_tokens'] == 16384


def response(status='completed'):
    return {'model': TERRA, 'status': status, 'output': [
        {'type': 'reasoning', 'summary': []}, {'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps({key: ['observation'] for key in OUTPUT_FIELDS})}]}]}


def test_only_final_text_parsed_not_reasoning_and_incomplete_rejected():
    assert set(parse_response(response(), TERRA)) == set(OUTPUT_FIELDS)
    with pytest.raises(C01Error, match='未完整完成'): parse_response(response('incomplete'), TERRA)
    with pytest.raises(C01Error, match='模型与'): parse_response(response(), MODEL)
    bad = response(); bad['output'][1]['content'][0] = {'type': 'refusal', 'refusal': 'declined'}
    with pytest.raises(C01Error, match='拒绝'): parse_response(bad, TERRA)


def test_terra_price_uses_own_rates_output_already_includes_reasoning():
    raw = {'model': TERRA, 'usage': {'input_tokens': 10000, 'output_tokens': 2000,
        'input_tokens_details': {'cached_tokens': 5000}, 'output_tokens_details': {'reasoning_tokens': 1000}}}
    cost = usage_cost(raw)
    assert Decimal(cost['usd']) == Decimal('.01525')  # Cache read discount, reasoning not double counted.
    assert cost['label'] == 'USAGE_BASED_ESTIMATE_NOT_PROVIDER_BILL'
    raw['usage']['input_tokens'] = 300000
    assert Decimal(usage_cost(raw)['usd']) == Decimal('.6055')
    assert usage_cost({'model': 'unknown', 'usage': raw['usage']}) is None
    snapshot = {'context': {'capability_specific_inputs': {'width': 1920, 'height': 1920}}}
    quote = estimate_cost(snapshot, request())
    assert quote['input_usd_per_million'] == '1' and quote['output_usd_per_million'] == '5'
    assert 'heuristic' in quote['method'] and quote['output_token_limit'] == 8192


def test_terra_cache_write_rates_are_total_not_surcharges():
    raw = {'model': TERRA, 'usage': {'input_tokens': 3592, 'output_tokens': 715,
        'input_tokens_details': {'cache_write_tokens': 3589, 'cached_tokens': 0}}}
    assert Decimal(usage_cost(raw)['usd']) == Decimal('.00806425')
    assert usage_cost(raw)['cache_write_ttl'] == 'DEFAULT_30M_NOT_RETURNED'
    raw['usage']['input_tokens_details']['cache_write_tokens_details'] = {'ttl_30m_tokens': 2000, 'ttl_12h_tokens': 1589}
    assert Decimal(usage_cost(raw)['usd']) == Decimal('.0100505')
    raw['usage']['input_tokens_details']['cached_tokens'] = 100
    assert usage_cost(raw) is None  # Inconsistent categories must not produce a fabricated amount.
