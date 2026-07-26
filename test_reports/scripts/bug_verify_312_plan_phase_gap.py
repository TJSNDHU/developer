#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, os, sys
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
ROOT=Path('/app'); BACKEND=ROOT/'backend'; sys.path.insert(0,str(BACKEND)); load_dotenv(BACKEND/'.env')

class FakeResponse:
    def raise_for_status(self): return None
    def json(self):
        return {'choices':[{'message':{'content':'{"title":"Test Plan","files_to_change":[],"bullets":["x"],"estimated_time":"~1m"}'}}], 'usage': {'prompt_tokens': 111, 'completion_tokens': 22}}
class FakeAsyncClient:
    def __init__(self,*a,**k): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*a): return False
    async def post(self,*a,**k): return FakeResponse()
class Coll:
    def __init__(self): self.docs=[]
    async def insert_one(self, doc): self.docs.append(dict(doc)); return type('R',(),{'inserted_id':'x'})()
    async def find_one(self,*a,**k): return None
    async def update_one(self,*a,**k): return type('R',(),{'matched_count':1,'modified_count':1})()
    def find(self,*a,**k):
        class Cur:
            async def __aiter__(self): return self
            async def __anext__(self): raise StopAsyncIteration
        return Cur()
class DB:
    def __init__(self):
        self.ora_chat_usage=Coll(); self.ora_chat_budget_alerts=Coll(); self.loop_plans=Coll(); self.loop_sessions=Coll(); self.loop_errors=Coll(); self.loop_run_log=Coll()

async def main():
    from services import llm
    from services.ora_chat import cost_tracker as ct
    from services.loop_engine import LoopEngine
    db=DB(); orig_client=llm.httpx.AsyncClient; orig_get_db=ct.get_db; orig_alert=ct._maybe_send_threshold_alert; orig_key=os.environ.get('OPENROUTER_API_KEY')
    try:
        os.environ['OPENROUTER_API_KEY']='test-key-no-network'; llm.httpx.AsyncClient=FakeAsyncClient; ct.get_db=lambda: db
        async def no_alert(): return None
        ct._maybe_send_threshold_alert=no_alert
        engine=LoopEngine(db=db, loop_id='loop_plan_gap_312', user_id='u_plan_gap', project_id=None, user_message='make a plan')
        events=[]
        async for ev in engine.start(): events.append(ev)
        out={'usage_rows': db.ora_chat_usage.docs, 'events_count': len(events), 'state': getattr(engine.state,'value',str(engine.state))}
        (ROOT/'test_reports'/'bug_verify_312_plan_phase_gap.json').write_text(json.dumps(out, indent=2, sort_keys=True), encoding='utf-8')
        print(json.dumps(out, indent=2, sort_keys=True))
    finally:
        llm.httpx.AsyncClient=orig_client; ct.get_db=orig_get_db; ct._maybe_send_threshold_alert=orig_alert
        if orig_key is None: os.environ.pop('OPENROUTER_API_KEY', None)
        else: os.environ['OPENROUTER_API_KEY']=orig_key
if __name__=='__main__': asyncio.run(main())
