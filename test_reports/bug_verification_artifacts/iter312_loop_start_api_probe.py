import json
import os
import time
import requests

BASE = os.environ.get('AUREM_PREVIEW_URL', 'https://launch-pad-237.preview.emergentagent.com').rstrip('/')
EMAIL = os.environ.get('AUREM_TEST_EMAIL', 'test@aurem.dev')
PASSWORD = os.environ.get('AUREM_TEST_PASSWORD', 'AuremTest2026!')

s = requests.Session()
login = s.post(f'{BASE}/api/aurem-dev/auth/login', json={'email': EMAIL, 'password': PASSWORD}, timeout=20)
print('login_status', login.status_code)
login.raise_for_status()
token = login.json().get('token')
assert token, 'login returned no token'
headers = {'Authorization': f'Bearer {token}'}

plist = s.get(f'{BASE}/api/aurem-dev/cto/projects/list', headers=headers, timeout=20)
print('projects_status', plist.status_code)
plist.raise_for_status()
projects = plist.json().get('projects') or []
print('project_count', len(projects))
usable = [p for p in projects if p.get('project_id') and p.get('has_pat')]
if not usable:
    usable = [p for p in projects if p.get('project_id')]
assert usable, 'no project available to exercise /loop/start in preview'
project_id = usable[0]['project_id']
print('project_id_used', project_id)

payload = {'project_id': project_id, 'user_message': 'Bug verification async loop start smoke test. Do not change files.'}
t0 = time.monotonic()
r = s.post(f'{BASE}/api/aurem-dev/loop/start', json=payload, headers=headers, timeout=15)
elapsed = time.monotonic() - t0
print('start_status', r.status_code)
print('start_elapsed_s', round(elapsed, 3))
try:
    body = r.json()
except Exception:
    body = {'text': r.text[:500]}
print('start_body', json.dumps(body, indent=2, default=str))
r.raise_for_status()
assert body.get('async_start') is True, 'loop/start did not return async_start true'
assert body.get('state') == 'planning', 'loop/start did not return planning state'
assert body.get('phase') == 'plan', 'loop/start did not return phase plan'
assert elapsed < 10, 'loop/start response was not prompt enough for async behavior'
loop_id = body.get('loop_id')
assert loop_id, 'no loop_id returned'

c = s.post(f'{BASE}/api/aurem-dev/loop/{loop_id}/cancel', headers=headers, timeout=20)
print('cancel_status', c.status_code)
try:
    print('cancel_body', json.dumps(c.json(), indent=2, default=str))
except Exception:
    print('cancel_text', c.text[:500])
assert c.status_code in (200, 404), 'cancel failed unexpectedly'
print('PASS: /loop/start returns immediate async planning response and cleanup attempted for', loop_id)
