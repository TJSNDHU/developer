#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import jwt
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path('/app')
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / '.env')


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


async def main():
    from services.ora_chat.cost_tracker import compute_cost_usd
    mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = mongo[os.environ['DB_NAME']]
    marker = 'iter312_endpoint_seed'
    phase = 'endpoint312_' + uuid.uuid4().hex[:6]
    now = time.time()
    seed = {
        'user_id': 'qa_iter312_endpoint',
        'session_id': 'loop_endpoint312_' + uuid.uuid4().hex[:6],
        'route': f'loop.{phase}',
        'model': 'deepseek/deepseek-chat',
        'temperature': 0.0,
        'input_tokens': 777,
        'output_tokens': 88,
        'cost_usd': compute_cost_usd('deepseek/deepseek-chat', 777, 88),
        'ts': now,
        'ts_month': time.strftime('%Y-%m', time.gmtime(now)),
        'ts_day': time.strftime('%Y-%m-%d', time.gmtime(now)),
        'qa_marker': marker,
    }
    base = 'https://launch-pad-237.preview.emergentagent.com/api/aurem-dev'
    sess = requests.Session()
    try:
        await db.ora_chat_usage.insert_one(seed)
        admin_token = jwt.encode({
            'user_id': 'qa_admin_loop_metrics',
            'email': 'test@aurem.dev',
            'is_admin': True,
            'iat': int(time.time()),
            'jti': uuid.uuid4().hex,
            'exp': int(time.time()) + 3600,
        }, os.environ['JWT_SECRET'], algorithm='HS256')
        resp = sess.get(f'{base}/admin/loop-token-metrics', headers={'Authorization': f'Bearer {admin_token}'}, timeout=30)
        assert_true(resp.status_code == 200, f'admin expected 200 got {resp.status_code}: {resp.text[:200]}')
        data = resp.json()
        by_phase = data['current']['by_phase']
        assert_true(phase in by_phase, f'seeded phase {phase} missing from endpoint aggregation')
        row = by_phase[phase]
        assert_true(row['calls'] >= 1, 'seeded phase calls not counted')
        assert_true(row['input_tokens'] >= 777 and row['output_tokens'] >= 88, 'seeded phase tokens not counted')
        out = {'ok': True, 'phase': phase, 'endpoint_phase_row': row, 'total_calls': data['current']['total_calls']}
        (ROOT / 'test_reports' / 'bug_verify_312_endpoint_seeded.json').write_text(json.dumps(out, indent=2, sort_keys=True), encoding='utf-8')
        print(json.dumps(out, indent=2, sort_keys=True))
    finally:
        await db.ora_chat_usage.delete_many({'qa_marker': marker})
        mongo.close()

if __name__ == '__main__':
    asyncio.run(main())

