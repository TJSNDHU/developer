import asyncio
import os
import time
import uuid
import json
from pathlib import Path

import httpx
import jwt
from motor.motor_asyncio import AsyncIOMotorClient


def load_env(path: str):
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


async def main():
    load_env('/app/backend/.env')
    mongo_url = os.environ['MONGO_URL']
    db_name = os.environ.get('DB_NAME', 'aurem_dev')
    jwt_secret = os.environ['JWT_SECRET']
    base_url = os.environ.get('TEST_BACKEND_URL', 'http://localhost:8001')
    if base_url.endswith('/api'):
        api_base = base_url
    else:
        api_base = base_url.rstrip('/') + '/api'
    url = api_base + '/aurem-dev/admin/loop-token-metrics'

    marker = 'qa313_' + uuid.uuid4().hex[:10]
    now = time.time()
    docs = [
        {'user_id': 'qa_admin', 'session_id': marker + '_loopA', 'route': f'loop.{marker}_plan', 'model': 'm', 'temperature': 0, 'input_tokens': 111, 'output_tokens': 22, 'cost_usd': 0.00111, 'ts': now - 60},
        {'user_id': 'qa_admin', 'session_id': marker + '_loopA', 'route': f'loop.{marker}_execute', 'model': 'm', 'temperature': 0, 'input_tokens': 333, 'output_tokens': 44, 'cost_usd': 0.00333, 'ts': now - 60},
        {'user_id': 'qa_admin', 'session_id': marker + '_chat', 'route': f'chat.{marker}', 'model': 'm', 'temperature': 0, 'input_tokens': 9999, 'output_tokens': 9999, 'cost_usd': 9.999, 'ts': now - 60},
    ]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    await db.ora_chat_usage.insert_many(docs)

    admin_payload = {
        'user_id': 'qa_admin_user', 'email': 'qa-admin@example.invalid',
        'is_admin': True, 'iat': int(now), 'exp': int(now) + 3600,
        'jti': uuid.uuid4().hex,
    }
    non_admin_payload = {
        'user_id': 'qa_normal_user', 'email': 'qa-user@example.invalid',
        'is_admin': False, 'iat': int(now), 'exp': int(now) + 3600,
        'jti': uuid.uuid4().hex,
    }
    admin_token = jwt.encode(admin_payload, jwt_secret, algorithm='HS256')
    non_admin_token = jwt.encode(non_admin_payload, jwt_secret, algorithm='HS256')

    result = {'marker': marker, 'url': url}
    try:
        async with httpx.AsyncClient(timeout=20.0) as h:
            no_auth = await h.get(url)
            non_admin = await h.get(url, headers={'Authorization': f'Bearer {non_admin_token}'})
            admin = await h.get(url, headers={'Authorization': f'Bearer {admin_token}'})
        result['status_codes'] = {
            'no_auth': no_auth.status_code,
            'non_admin': non_admin.status_code,
            'admin': admin.status_code,
        }
        if admin.status_code != 200:
            result['admin_body_prefix'] = admin.text[:300]
            raise AssertionError(f'admin endpoint returned {admin.status_code}')
        body = admin.json()
        by_phase = body.get('current', {}).get('by_phase', {})
        p1 = by_phase.get(f'{marker}_plan')
        p2 = by_phase.get(f'{marker}_execute')
        chat_present = any(marker in k for k in by_phase if k.startswith('chat'))
        result['shape_keys'] = sorted(body.keys())
        result['current_keys'] = sorted(body.get('current', {}).keys())
        result['seeded_phases'] = {'plan': p1, 'execute': p2, 'chat_present_in_loop_groups': chat_present}
        assert no_auth.status_code == 401, f'expected unauth 401, got {no_auth.status_code}'
        assert non_admin.status_code == 403, f'expected non-admin 403, got {non_admin.status_code}'
        assert body.get('ok') is True
        for key in ('current', 'previous', 'data_source', 'window_days', 'note'):
            assert key in body, f'missing top-level key {key}'
        assert p1 and p1['calls'] == 1 and p1['input_tokens'] == 111 and p1['output_tokens'] == 22
        assert p2 and p2['calls'] == 1 and p2['input_tokens'] == 333 and p2['output_tokens'] == 44
        assert not chat_present, 'non-loop chat route leaked into loop-token metrics'
        result['ok'] = True
    finally:
        await db.ora_chat_usage.delete_many({'$or': [{'route': {'$regex': marker}}, {'session_id': {'$regex': marker}}]})
        client.close()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    asyncio.run(main())
