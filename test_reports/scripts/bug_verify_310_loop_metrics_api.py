import asyncio
import os
import time
from pathlib import Path

import httpx
import jwt
from dotenv import dotenv_values

ENV = dotenv_values('/app/backend/.env')
secret = ENV.get('JWT_SECRET') or os.environ.get('JWT_SECRET')
admin_email = ENV.get('ADMIN_EMAIL') or 'admin@example.test'
base = 'http://localhost:8001/api/aurem-dev'

async def main():
    async with httpx.AsyncClient(timeout=10) as c:
        anon = await c.get(f'{base}/admin/loop-metrics')
        print('anon_status', anon.status_code)
        assert anon.status_code == 401, anon.text
        now = int(time.time())
        token = jwt.encode({
            'user_id': 'bug-verify-loop-metrics-admin',
            'email': admin_email,
            'is_admin': True,
            'iat': now,
            'jti': 'bugverify310loopmetrics',
            'exp': now + 3600,
        }, secret, algorithm='HS256')
        authed = await c.get(f'{base}/admin/loop-metrics', headers={'Authorization': f'Bearer {token}'})
        print('admin_status', authed.status_code)
        assert authed.status_code == 200, authed.text
        body = authed.json()
        print('keys', sorted(body.keys()))
        assert body.get('ok') is True
        assert body.get('window_days') == 7
        assert {'current','previous','delta_failed_ratio','note'} <= set(body)
        for bucket in ('current','previous'):
            assert {'counts','total','resolved','failed','completed','failed_ratio','since_utc','until_utc'} <= set(body[bucket])
        d = body.get('delta_failed_ratio')
        assert d is None or isinstance(d, (int, float))
        print('shape_ok')

asyncio.run(main())
