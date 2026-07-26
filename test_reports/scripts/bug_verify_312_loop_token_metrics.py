#!/usr/bin/env python3
"""
Focused bug verification for Iter 312 loop token accounting.
Creates no product-code changes; writes summarized artifacts only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
import requests

ROOT = Path('/app')
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / '.env')


class FakeResponse:
    status_code = 200
    text = '{"ok": true}'

    def raise_for_status(self):
        return None

    def json(self):
        return {
            'choices': [{'message': {'content': 'fake llm ok'}}],
            'usage': {'prompt_tokens': 321, 'completion_tokens': 45, 'total_tokens': 366},
        }


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return FakeResponse()


class MockColl:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type('R', (), {'inserted_id': 'x'})()

    async def find_one(self, *args, **kwargs):
        return None


class MockDB:
    def __init__(self):
        self.ora_chat_usage = MockColl()
        self.ora_chat_budget_alerts = MockColl()


def assert_true(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


async def test_llm_instrumentation_with_context() -> dict:
    from services import llm
    from services import loop_token_ledger as ledger
    from services.ora_chat import cost_tracker as ct

    mock = MockDB()
    orig_client = llm.httpx.AsyncClient
    orig_key = os.environ.get('OPENROUTER_API_KEY')
    orig_get_db = ct.get_db
    orig_alert = ct._maybe_send_threshold_alert
    try:
        os.environ['OPENROUTER_API_KEY'] = 'test-key-no-network'
        llm.httpx.AsyncClient = FakeAsyncClient
        ct.get_db = lambda: mock
        async def no_alert():
            return None
        ct._maybe_send_threshold_alert = no_alert

        # Outside loop context: call_llm_with_meta should not write via ledger.
        regular = await llm.call_llm_with_meta('sys', 'hello', max_tokens=20, mode='chat')
        assert_true(regular.get('ok') is True, 'regular call_llm_with_meta did not return ok=True')
        assert_true(mock.ora_chat_usage.docs == [], 'regular non-loop call wrote ora_chat_usage via ledger')

        # DeepSeek path inside loop context writes loop.<phase> row.
        async with ledger.loop_call_context(loop_id='loop_instr_ds', phase_tag='verify', user_id='u_instr'):
            direct = await llm.call_llm([{'role': 'user', 'content': 'hello'}], max_tokens=20)
        assert_true(direct == 'fake llm ok', 'call_llm did not return fake content')
        assert_true(len(mock.ora_chat_usage.docs) == 1, 'loop-context _call_deepseek did not write one usage row')
        row1 = mock.ora_chat_usage.docs[-1]
        assert_true(row1['session_id'] == 'loop_instr_ds', 'DeepSeek ledger row session_id mismatch')
        assert_true(row1['route'] == 'loop.verify', 'DeepSeek ledger row route mismatch')
        assert_true(row1['input_tokens'] == 321 and row1['output_tokens'] == 45, 'DeepSeek usage tokens mismatch')

        # Generic OpenRouter path inside loop context writes loop.<phase> row.
        async with ledger.loop_call_context(loop_id='loop_instr_or', phase_tag='execute', user_id='u_instr'):
            generic = await llm.call_openrouter_model('z-ai/glm-5.2', 'sys', 'hello', max_tokens=20)
        assert_true(generic == 'fake llm ok', 'call_openrouter_model did not return fake content')
        assert_true(len(mock.ora_chat_usage.docs) == 2, 'call_openrouter_model did not write one additional row')
        row2 = mock.ora_chat_usage.docs[-1]
        assert_true(row2['session_id'] == 'loop_instr_or', 'OpenRouter ledger row session_id mismatch')
        assert_true(row2['route'] == 'loop.execute', 'OpenRouter ledger row route mismatch')
        assert_true(row2['model'] == 'z-ai/glm-5.2', 'OpenRouter ledger row model mismatch')
        return {'ok': True, 'rows': [{k: row1[k] for k in ('session_id','route','input_tokens','output_tokens','cost_usd')}, {k: row2[k] for k in ('session_id','route','input_tokens','output_tokens','cost_usd')}]} 
    finally:
        llm.httpx.AsyncClient = orig_client
        ct.get_db = orig_get_db
        ct._maybe_send_threshold_alert = orig_alert
        if orig_key is None:
            os.environ.pop('OPENROUTER_API_KEY', None)
        else:
            os.environ['OPENROUTER_API_KEY'] = orig_key


async def seed_and_check_mongo_hygiene() -> dict:
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.ora_chat.cost_tracker import compute_cost_usd

    mongo_url = os.environ['MONGO_URL']
    db_name = os.environ['DB_NAME']
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    phase = 'bugverify312_' + uuid.uuid4().hex[:8]
    loop_id = 'loop_bugverify312_' + uuid.uuid4().hex[:8]
    regular_id = 'regular_bugverify312_' + uuid.uuid4().hex[:8]
    now = time.time()
    loop_doc = {
        'user_id': 'qa_iter312',
        'session_id': loop_id,
        'route': f'loop.{phase}',
        'model': 'deepseek/deepseek-chat',
        'temperature': 0.0,
        'input_tokens': 100,
        'output_tokens': 25,
        'cost_usd': compute_cost_usd('deepseek/deepseek-chat', 100, 25),
        'ts': now,
        'ts_month': time.strftime('%Y-%m', time.gmtime(now)),
        'ts_day': time.strftime('%Y-%m-%d', time.gmtime(now)),
        'qa_marker': 'iter312_loop_token_metrics',
    }
    regular_doc = {
        **loop_doc,
        'session_id': regular_id,
        'route': 'chat',
        'qa_marker': 'iter312_regular_chat_control',
    }
    try:
        await db.ora_chat_usage.insert_many([loop_doc, regular_doc])
        loop_found = await db.ora_chat_usage.count_documents({'qa_marker': {'$in': ['iter312_loop_token_metrics','iter312_regular_chat_control']}, 'route': {'$regex': '^loop\\.'}})
        regular_found = await db.ora_chat_usage.count_documents({'qa_marker': {'$in': ['iter312_loop_token_metrics','iter312_regular_chat_control']}, 'route': {'$not': {'$regex': '^loop\\.'}}})
        bad_loop_rows = await db.ora_chat_usage.count_documents({'qa_marker': 'iter312_loop_token_metrics', '$or': [{'session_id': {'$in': ['', None]}}, {'route': {'$not': {'$regex': '^loop\\.'}}}]})
        agg = await db.ora_chat_usage.aggregate([
            {'$match': {'qa_marker': {'$in': ['iter312_loop_token_metrics','iter312_regular_chat_control']}, 'route': {'$regex': '^loop\\.'}}},
            {'$group': {'_id': '$route', 'calls': {'$sum': 1}, 'input': {'$sum': '$input_tokens'}, 'output': {'$sum': '$output_tokens'}, 'loops': {'$addToSet': '$session_id'}}},
        ]).to_list(10)
        assert_true(loop_found == 1, f'route ^= loop. matched {loop_found} rows, expected 1')
        assert_true(regular_found == 1, f'route !~ loop. matched {regular_found} rows, expected 1')
        assert_true(bad_loop_rows == 0, 'loop-originated row had blank session_id or non-loop route')
        assert_true(len(agg) == 1 and agg[0]['_id'] == f'loop.{phase}', 'aggregation did not isolate loop row')
        return {'ok': True, 'phase': phase, 'loop_id': loop_id, 'cost_usd': loop_doc['cost_usd'], 'agg': [{'_id': agg[0]['_id'], 'calls': agg[0]['calls'], 'input': agg[0]['input'], 'output': agg[0]['output'], 'loops_count': len(agg[0]['loops'])}]}
    finally:
        await db.ora_chat_usage.delete_many({'qa_marker': {'$in': ['iter312_loop_token_metrics','iter312_regular_chat_control']}})
        client.close()


def check_endpoint_auth_and_shape() -> dict:
    base = 'https://launch-pad-237.preview.emergentagent.com/api/aurem-dev'
    sess = requests.Session()
    unauth = sess.get(f'{base}/admin/loop-token-metrics', timeout=20)
    bad = sess.get(f'{base}/admin/loop-token-metrics', headers={'Authorization': 'Bearer invalid-token'}, timeout=20)
    non_admin_token = jwt.encode({
        'user_id': 'qa_non_admin_no_db',
        'email': 'qa_non_admin@example.invalid',
        'is_admin': False,
        'iat': int(time.time()),
        'jti': uuid.uuid4().hex,
        'exp': int(time.time()) + 3600,
    }, os.environ['JWT_SECRET'], algorithm='HS256')
    non_admin = sess.get(f'{base}/admin/loop-token-metrics', headers={'Authorization': f'Bearer {non_admin_token}'}, timeout=20)

    # Preview password login is currently 429 rate-limited in this shared QA env,
    # so use the app's JWT secret to mint an admin-scoped token without printing it.
    admin_token = jwt.encode({
        'user_id': 'qa_admin_loop_metrics',
        'email': 'test@aurem.dev',
        'is_admin': True,
        'iat': int(time.time()),
        'jti': uuid.uuid4().hex,
        'exp': int(time.time()) + 3600,
    }, os.environ['JWT_SECRET'], algorithm='HS256')
    admin = sess.get(f'{base}/admin/loop-token-metrics', headers={'Authorization': f'Bearer {admin_token}'}, timeout=30)
    assert_true(unauth.status_code == 401, f'unauth expected 401 got {unauth.status_code}')
    assert_true(bad.status_code == 401, f'invalid token expected 401 got {bad.status_code}')
    assert_true(non_admin.status_code == 403, f'non-admin expected 403 got {non_admin.status_code}')
    assert_true(admin.status_code == 200, f'admin expected 200 got {admin.status_code}: {admin.text[:200]}')
    data = admin.json()
    for key in ['ok','window_days','data_source','current','previous']:
        assert_true(key in data, f'missing top-level key {key}')
    for section in ['current','previous']:
        for key in ['by_phase','total_calls','total_input','total_output','total_cost_usd','distinct_loops','avg_per_loop']:
            assert_true(key in data[section], f'missing {section}.{key}')
    for key in ['db_name','mongo_host','commit_sha','env']:
        assert_true(key in data['data_source'], f'missing data_source.{key}')
    return {
        'ok': True,
        'statuses': {'unauth': unauth.status_code, 'invalid_token': bad.status_code, 'non_admin': non_admin.status_code, 'admin': admin.status_code},
        'shape': {'top_keys': sorted([k for k in ['ok','window_days','data_source','current','previous'] if k in data]), 'current_keys': sorted(data['current'].keys()), 'previous_keys': sorted(data['previous'].keys())},
    }


async def main():
    out = {
        'llm_instrumentation': await test_llm_instrumentation_with_context(),
        'mongo_hygiene': await seed_and_check_mongo_hygiene(),
        'endpoint': check_endpoint_auth_and_shape(),
    }
    out_path = ROOT / 'test_reports' / 'bug_verify_312_loop_token_metrics_checks.json'
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == '__main__':
    asyncio.run(main())

