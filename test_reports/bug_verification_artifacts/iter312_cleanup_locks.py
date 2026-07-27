import os, asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
async def main():
    client=AsyncIOMotorClient(os.environ['MONGO_URL'])
    db=client[os.environ.get('DB_NAME','aurem_dev')]
    res=await db.loop_locks.delete_many({'user_id':'test_admin_001','project_id':{'$in':['p_demo_a','_no_project']}})
    print({'deleted_locks': res.deleted_count})
asyncio.run(main())
