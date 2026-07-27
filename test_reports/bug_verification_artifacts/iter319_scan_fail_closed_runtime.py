import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path('/app/backend')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.loop_engine import LoopEngine, LoopState  # noqa: E402

class FakeCollection:
    def __init__(self):
        self.docs = []
    async def update_one(self, filter_doc, update_doc, upsert=False):
        doc = dict(update_doc.get('$set', {}))
        self.docs.append(doc)
        return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id='fake')
    async def find_one(self, *args, **kwargs):
        return None

class FakeDB:
    def __init__(self):
        self.loop_sessions = FakeCollection()
        self.loop_errors = FakeCollection()
        self.loop_events = FakeCollection()
        self.loop_run_log = FakeCollection()
        self.loop_audit_log = FakeCollection()
        self.loop_failures = FakeCollection()
        self.loop_locks = FakeCollection()

async def noop(*args, **kwargs):
    return None
async def boom(*args, **kwargs):
    raise RuntimeError('synthetic scanner crash')

async def main():
    db = FakeDB()
    engine = LoopEngine(db, 'loop_bugverify_iter319_scan', 'user_bugverify', 'proj_bugverify', 'scan crash probe')
    engine.context['submitted_files'] = [{'path': 'app.py', 'content': 'print("safe")\n'}]
    engine.context['errors_encountered'] = []
    with patch('services.loop_engine._run_diff_security_scan', boom), \
         patch('services.loop_safety.record_loop_failure', noop), \
         patch('services.loop_safety.release_loop_lock', noop):
        await engine._do_scan()
    emitted = []
    while not engine.queue.empty():
        emitted.append(await engine.queue.get())
    last = emitted[-1]
    result = {
        'engine_state': engine.state.value,
        'engine_phase': engine.phase,
        'scan_results': engine.context.get('scan_results'),
        'errors': engine.context.get('errors_encountered'),
        'last_event_state': last.get('state'),
        'last_event_phase': last.get('phase'),
        'last_event_kind': (last.get('data') or {}).get('kind'),
        'persisted_state': db.loop_sessions.docs[-1].get('state') if db.loop_sessions.docs else None,
    }
    print(result)
    assert engine.state == LoopState.FAILED
    assert engine.phase == 'scan'
    assert engine.context['scan_results']['fail_closed'] is True
    assert 'scan_exception' in engine.context['errors_encountered'][-1]['error']
    assert last['state'] == 'failed'
    assert last['phase'] == 'scan'
    assert last['data']['kind'] == 'scan_exception'
    assert db.loop_sessions.docs[-1]['state'] == 'failed'
    print('PASS: scan exception fails closed and does not proceed to ship')

if __name__ == '__main__':
    asyncio.run(main())
