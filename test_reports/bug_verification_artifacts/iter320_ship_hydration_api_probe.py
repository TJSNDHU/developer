import json
import os
import time
import requests
from pymongo import MongoClient

BASE = os.environ.get('AUREM_PREVIEW_URL', 'https://launch-pad-237.preview.emergentagent.com').rstrip('/')
EMAIL = os.environ.get('AUREM_TEST_EMAIL', 'test@aurem.dev')
PASSWORD = os.environ.get('AUREM_TEST_PASSWORD', 'AuremTest2026!')
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'aurem_dev')
PROJECT_ID = os.environ.get('AUREM_TEST_PROJECT_ID', 'p_norepotest')

s = requests.Session()
login = s.post(f'{BASE}/api/aurem-dev/auth/login', json={'email': EMAIL, 'password': PASSWORD}, timeout=20)
print('login_status', login.status_code)
login.raise_for_status()
data = login.json()
token = data['token']
user_id = data['user_id']
headers = {'Authorization': f'Bearer {token}'}

loop_id = f'loop_bugverify_shiphydrate_{int(time.time())}'
now = time.time()
client = MongoClient(MONGO_URL)
db = client[DB_NAME]
# Remove stale synthetic docs for this exact prefix only.
db.loop_sessions.delete_many({'loop_id': {'$regex': '^loop_bugverify_shiphydrate_'}})

doc = {
    'loop_id': loop_id,
    'user_id': user_id,
    'project_id': PROJECT_ID,
    'state': 'paused_for_user',
    'phase': 'ship',
    'context': {
        'original_request': 'synthetic paused ship hydrate verification',
        'plan': {'title': 'Synthetic plan', 'files_to_change': ['README.md'], 'bullets': ['edit readme']},
        'ship_pending': {
            'owner': 'aurem-labs',
            'repo': 'ora-testkit',
            'branch': 'main',
            'token': 'SHOULD_NOT_LEAK_TO_CLIENT',
            'files': {'README.md': '# README\nsynthetic ship hydration check\n'},
            'commit_message': 'feat(ora): synthetic ship hydrate check [loop-verified]',
        },
        'files_changed': [{'path': 'README.md'}],
    },
    'updated_at': now,
    'created_at': now,
}
db.loop_sessions.insert_one(doc)
try:
    r = s.get(f'{BASE}/api/aurem-dev/loop/active?project_id={PROJECT_ID}', headers=headers, timeout=20)
    print('active_status', r.status_code)
    body = r.json()
    print('active_body', json.dumps(body, indent=2, default=str))
    r.raise_for_status()
    active = body.get('active')
    assert active, 'no active loop returned'
    assert active['loop_id'] == loop_id, f'expected synthetic loop, got {active.get("loop_id")}'
    assert active['state'] == 'paused_for_user'
    assert active['phase'] == 'ship'
    sp = active.get('ship_pending')
    assert sp, 'ship_pending missing from /loop/active response'
    assert sp.get('commit_message'), 'commit message missing'
    assert 'README.md' in (sp.get('files') or {}), 'ship files missing'
    assert sp.get('token') is None, 'ship_pending token leaked to client'
    print('PASS: /loop/active rehydrates paused ship loop with ship_pending/commit/files and scrubbed token')
finally:
    db.loop_sessions.delete_one({'loop_id': loop_id})
