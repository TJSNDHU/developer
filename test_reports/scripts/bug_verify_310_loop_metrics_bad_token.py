import httpx
base='http://localhost:8001/api/aurem-dev'
r=httpx.get(base+'/admin/loop-metrics', headers={'Authorization':'Bearer definitely.invalid.token'}, timeout=10)
print('bad_token_status', r.status_code)
print('detail', r.text[:160])
assert r.status_code == 401
