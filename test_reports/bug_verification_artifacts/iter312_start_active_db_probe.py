import os, time, json, asyncio, subprocess, requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
BASE=open('/app/frontend/.env').read().split('REACT_APP_BACKEND_URL=')[1].splitlines()[0].strip()
TOKEN=open('/app/test_reports/bug_verification_artifacts/iter312_token.txt').read().strip()
HEAD={'Authorization':f'Bearer {TOKEN}','Content-Type':'application/json'}
PROJECT='p_norepotest'
async def main():
    client=AsyncIOMotorClient(os.environ['MONGO_URL']); db=client[os.environ.get('DB_NAME','aurem_dev')]
    await db.loop_locks.delete_many({'user_id':'test_admin_001','project_id':PROJECT})
    active=requests.get(f'{BASE}/api/aurem-dev/loop/active?project_id={PROJECT}',headers={'Authorization':f'Bearer {TOKEN}'},timeout=10).json().get('active')
    if active and active.get('loop_id'):
        requests.post(f'{BASE}/api/aurem-dev/loop/{active["loop_id"]}/cancel',headers=HEAD,json={},timeout=10)
        await asyncio.sleep(1)
    body={'project_id':PROJECT,'user_message':'Iter 312 active planning visibility probe: create a detailed plan for a multi-system refactor with frontend, backend, tests, CI, observability, migration, rollback, security and docs.'}
    t=time.time(); r=requests.post(f'{BASE}/api/aurem-dev/loop/start',headers=HEAD,json=body,timeout=10); dt=time.time()-t
    print('START_STATUS', r.status_code, 'TIME', round(dt,3), 'BODY', json.dumps(r.json()))
    lid=(r.json() or {}).get('loop_id')
    ar=requests.get(f'{BASE}/api/aurem-dev/loop/active?project_id={PROJECT}',headers={'Authorization':f'Bearer {TOKEN}'},timeout=10)
    print('ACTIVE_IMMEDIATE', ar.status_code, json.dumps(ar.json()))
    doc=await db.loop_sessions.find_one({'loop_id':lid},{'_id':0,'loop_id':1,'state':1,'phase':1,'project_id':1,'updated_at':1,'last_event':1})
    def clean(o):
        import datetime
        if isinstance(o, dict): return {k: clean(v) for k,v in o.items()}
        if isinstance(o, list): return [clean(v) for v in o]
        if hasattr(o,'isoformat'): return o.isoformat()
        return o
    print('DB_SESSION_IMMEDIATE', json.dumps(clean(doc)))
    await asyncio.sleep(2)
    ar2=requests.get(f'{BASE}/api/aurem-dev/loop/active?project_id={PROJECT}',headers={'Authorization':f'Bearer {TOKEN}'},timeout=10)
    print('ACTIVE_AFTER_2S', ar2.status_code, json.dumps(ar2.json()))
    doc2=await db.loop_sessions.find_one({'loop_id':lid},{'_id':0,'loop_id':1,'state':1,'phase':1,'project_id':1,'updated_at':1,'last_event':1})
    print('DB_SESSION_AFTER_2S', json.dumps(clean(doc2)))
    if lid:
        cr=requests.post(f'{BASE}/api/aurem-dev/loop/{lid}/cancel',headers=HEAD,json={},timeout=10)
        print('CLEANUP_CANCEL', cr.status_code, json.dumps(cr.json()))
asyncio.run(main())
