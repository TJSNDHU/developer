import asyncio, os, json
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
 db=AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ.get('DB_NAME','aurem_dev')]
 u=await db.dev_users.find_one({'email':'test@aurem.dev'},{'_id':0,'user_id':1,'email':1})
 print('user',u)
 for coll in ['cto_projects','projects','repositories','repos']:
  if coll in await db.list_collection_names():
   docs=[]
   async for d in db[coll].find({'user_id':u['user_id']},{'_id':0}).limit(5): docs.append(d)
   print(coll, json.dumps(docs, default=str)[:2000])
asyncio.run(main())
