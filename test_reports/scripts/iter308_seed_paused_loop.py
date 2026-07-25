import asyncio, os, json, secrets
from datetime import datetime, timezone
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
 db=AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ.get('DB_NAME','aurem_dev')]
 loop_id='loop_ui_rescue_'+secrets.token_hex(4)
 ev={'loop_id':loop_id,'state':'paused_for_user','phase':'execute','step':0,'total_steps':5,'message':'Server restarted mid-execute; session paused — retry when ready.','data':{'sub_step':'rescued_stale','rescued':True,'resume_reason':'server_restart_mid_loop'},'timestamp':datetime.now(timezone.utc).isoformat(),'requires_user_action':True}
 await db.loop_sessions.insert_one({'loop_id':loop_id,'user_id':'test_admin_001','project_id':'p_demo_a','state':'paused_for_user','phase':'execute','context':{'errors_encountered':[]},'updated_at':datetime.now(timezone.utc),'last_event':ev})
 Path('/app/test_reports/scripts/iter308_ui_seed_loop_id.txt').write_text(loop_id)
 print(json.dumps({'ok':True,'loop_id':loop_id}))
asyncio.run(main())
