#!/usr/bin/env python3
"""
Focused Iter 318 backend verification for Loop /start async + /active planning visibility.
Uses preview credentials from memory/test_credentials.md and base URL from frontend/.env.
Creates no product code changes; uses a synthetic project_id and cleans locks/sessions for it.
"""
import asyncio
import json
import re
import time
from pathlib import Path

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path('/app')
FRONTEND_ENV = ROOT / 'frontend' / '.env'
BACKEND_ENV = ROOT / 'backend' / '.env'
CREDS = ROOT / 'memory' / 'test_credentials.md'
OUT = ROOT / 'test_reports' / 'bug_verification_artifacts' / 'iter318_backend_probe.json'


def env_value(path, key):
    txt = path.read_text()
    m = re.search(rf'^{re.escape(key)}=(.*)$', txt, re.M)
    if not m:
        return None
    return m.group(1).strip().strip('"')


def cred_value(label):
    txt = CREDS.read_text()
    marker = '## Preview (dev)'
    if marker in txt:
        txt = txt.split(marker, 1)[1]
    m = re.search(rf'\*\*{re.escape(label)}\*\*:\s*(.+)$', txt, re.M)
    if not m:
        return None
    return m.group(1).strip()


async def cleanup_db(user_id, project_id, loop_id=None):
    mongo_url = env_value(BACKEND_ENV, 'MONGO_URL')
    db_name = env_value(BACKEND_ENV, 'DB_NAME')
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    await db.loop_locks.delete_many({'user_id': user_id, 'project_id': project_id})
    q_sess = {'user_id': user_id, 'project_id': project_id}
    if loop_id:
        q_sess = {'$or': [q_sess, {'loop_id': loop_id}]}
    await db.loop_sessions.delete_many(q_sess)
    client.close()


async def main():
    base = env_value(FRONTEND_ENV, 'REACT_APP_BACKEND_URL')
    api = f'{base}/api/aurem-dev'
    email = cred_value('Email')
    password = cred_value('Password')
    # Use no project for the primary backend probe so PAT health does not block
    # plan generation; the lock key becomes "_no_project" on the backend.
    project_id = None
    lock_project_id = '_no_project'
    results = {
        'base_url': base,
        'project_id': project_id,
        'checks': {},
        'observations': [],
    }
    async with httpx.AsyncClient(timeout=20) as cx:
        login = await cx.post(f'{api}/auth/login', json={'email': email, 'password': password})
        results['checks']['login_status'] = login.status_code
        login.raise_for_status()
        token = login.json()['token']
        user_id = login.json()['user_id']
        headers = {'Authorization': f'Bearer {token}'}

        await cleanup_db(user_id, lock_project_id)

        complex_msg = (
            'Implement a complex multi-step refactor touching frontend and backend: audit current README, '
            'design a migration plan, add validation, update tests, preserve behavior, and explain risks. '
            'This is intentionally detailed to exercise async loop start.'
        )
        t0 = time.perf_counter()
        r1 = await cx.post(f'{api}/loop/start', headers=headers, json={
            'project_id': project_id,
            'user_message': complex_msg,
        })
        dt = time.perf_counter() - t0
        body1 = r1.json() if r1.headers.get('content-type','').startswith('application/json') else {'text': r1.text[:500]}
        results['checks']['backend_1'] = {
            'status': r1.status_code,
            'elapsed_s': dt,
            'body': body1,
        }
        loop_id = body1.get('loop_id') if isinstance(body1, dict) else None

        r2 = await cx.post(f'{api}/loop/start', headers=headers, json={
            'project_id': project_id,
            'user_message': 'second concurrent start should be refused',
        })
        body2 = r2.json() if r2.headers.get('content-type','').startswith('application/json') else {'text': r2.text[:500]}
        results['checks']['backend_2'] = {'status': r2.status_code, 'body': body2}

        r3 = await cx.get(f'{api}/loop/active', headers=headers)
        body3 = r3.json() if r3.headers.get('content-type','').startswith('application/json') else {'text': r3.text[:500]}
        results['checks']['backend_3_immediate_active'] = {'status': r3.status_code, 'body': body3}

        terminal_or_plan = None
        poll_history = []
        for _ in range(36):
            rp = await cx.get(f'{api}/loop/active', headers=headers)
            bp = rp.json()
            active = bp.get('active')
            state = active.get('state') if active else None
            phase = active.get('phase') if active else None
            has_plan = bool(active and active.get('plan'))
            poll_history.append({'state': state, 'phase': phase, 'has_plan': has_plan})
            if active and ((state == 'awaiting_confirmation' and phase == 'plan' and has_plan) or state in ['failed','aborted','completed']):
                terminal_or_plan = poll_history[-1]
                break
            await asyncio.sleep(5)
        results['checks']['backend_4_poll'] = {
            'reached': terminal_or_plan,
            'history': poll_history,
        }
        await cleanup_db(user_id, lock_project_id, loop_id=loop_id)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    asyncio.run(main())