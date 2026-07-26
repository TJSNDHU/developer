#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path('/app')
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / '.env')

class FakeResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {
            'choices': [{'message': {'content': '{"title":"Test Plan","files_to_change":[],"bullets":["x"],"estimated_time":"~1m"}'}}],
            'usage': {'prompt_tokens': 111, 'completion_tokens': 22, 'total_tokens': 133},
        }

class FakeAsyncClient:
    def __init__(self, *a, **k):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def post(self, *a, **k):
        return FakeResponse()

class Coll:
    def __init__(self):
        self.docs = []
    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type('R', (), {'inserted_id': 'x'})()
    async def find_one(self, *a, **k):
        return None
    async def update_one(self, *a, **k):
        return type('R', (), {'matched_count': 1, 'modified_count': 1})()
    async def delete_one(self, *a, **k):
        return type('R', (), {'deleted_count': 0})()
    async def delete_many(self, *a, **k):
        return type('R', (), {'deleted_count': 0})()
    async def replace_one(self, *a, **k):
        return type('R', (), {'matched_count': 1, 'modified_count': 1})()
    async def count_documents(self, *a, **k):
        return 0
    def find(self, *a, **k):
        docs = list(self.docs)
        class Cur:
            def sort(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def __aiter__(self):
                self._it = iter(docs)
                return self
            async def __anext__(self):
                try: return next(self._it)
                except StopIteration: raise StopAsyncIteration
        return Cur()
    def aggregate(self, *a, **k):
        async def _g():
            if False:
                yield None
        return _g()

class DB:
    def __init__(self):
        self.ora_chat_usage = Coll()
        self.ora_chat_budget_alerts = Coll()
        self.loop_plans = Coll()
        self.loop_sessions = Coll()
        self.loop_events = Coll()
        self.loop_errors = Coll()
        self.loop_run_log = Coll()
        self.loop_failures = Coll()
        self.loop_locks = Coll()
        self.dev_users = Coll()
        self.cto_projects = Coll()
    def __getitem__(self, name):
        if not hasattr(self, name):
            setattr(self, name, Coll())
        return getattr(self, name)

async def main():
    from services import llm
    from services.ora_chat import cost_tracker as ct
    from services.loop_engine import LoopEngine
    from cto_services import db as db_mod

    db = DB()
    orig_client = llm.httpx.AsyncClient
    orig_get_db_ct = ct.get_db
    orig_alert = ct._maybe_send_threshold_alert
    orig_key = os.environ.get('OPENROUTER_API_KEY')
    orig_get_db_mod = db_mod.get_db
    try:
        os.environ['OPENROUTER_API_KEY'] = 'test-key-no-network'
        llm.httpx.AsyncClient = FakeAsyncClient
        ct.get_db = lambda: db
        db_mod.get_db = lambda: db
        async def no_alert(): return None
        ct._maybe_send_threshold_alert = no_alert

        engine = LoopEngine(db=db, loop_id='loop_plan_realpath_313', user_id='u_plan_gap', project_id=None, user_message='make a plan')
        events = []
        async for ev in engine.start():
            events.append(ev)
        rows = db.ora_chat_usage.docs
        out = {
            'usage_rows': rows,
            'events_count': len(events),
            'state': getattr(engine.state, 'value', str(engine.state)),
            'last_event_state': events[-1]['state'] if events else None,
            'assertions': {
                'one_row': len(rows) == 1,
                'route_loop_plan': rows[0].get('route') == 'loop.plan' if rows else False,
                'session_id_loop_id': rows[0].get('session_id') == 'loop_plan_realpath_313' if rows else False,
                'input_tokens': rows[0].get('input_tokens') == 111 if rows else False,
                'output_tokens': rows[0].get('output_tokens') == 22 if rows else False,
                'awaiting_confirmation': getattr(engine.state, 'value', str(engine.state)) == 'awaiting_confirmation',
            }
        }
        (ROOT / 'test_reports' / 'bug_verify_313_plan_phase_real_path.json').write_text(json.dumps(out, indent=2, sort_keys=True), encoding='utf-8')
        print(json.dumps(out, indent=2, sort_keys=True))
        assert all(out['assertions'].values()), out
    finally:
        llm.httpx.AsyncClient = orig_client
        ct.get_db = orig_get_db_ct
        ct._maybe_send_threshold_alert = orig_alert
        db_mod.get_db = orig_get_db_mod
        if orig_key is None:
            os.environ.pop('OPENROUTER_API_KEY', None)
        else:
            os.environ['OPENROUTER_API_KEY'] = orig_key

if __name__ == '__main__':
    asyncio.run(main())
