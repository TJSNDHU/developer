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
        self.updates = []
    async def update_one(self, filter_doc, update_doc, upsert=False):
        self.updates.append({'filter': filter_doc, 'update': update_doc, 'upsert': upsert})
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
        self.cto_projects = FakeCollection()
        self.dev_users = FakeCollection()

async def noop(*args, **kwargs):
    return None

async def main():
    db = FakeDB()
    engine = LoopEngine(
        db=db,
        loop_id='loop_bugverify_iter318_synthetic',
        user_id='user_bugverify',
        project_id='proj_bugverify',
        user_message='add one comment line at the top of README.md',
        bin_ctx=SimpleNamespace(repo_owner='test-owner', repo_name='test-repo', branch='main', pat='test-token'),
    )
    repo_body = '# README\n' + ('existing full documentation line\n' * 200)
    bad_body = '# README\n\n[Rest of existing README content remains unchanged...]\n'
    engine.context['submitted_files'] = [{'path': 'README.md', 'content': bad_body}]
    engine.context['original_bytes_by_path'] = {'README.md': len(repo_body)}
    engine.context['errors_encountered'] = []

    with patch('services.loop_safety.release_loop_lock', noop):
        await engine._do_ship()

    emitted = []
    while not engine.queue.empty():
        emitted.append(await engine.queue.get())
    persisted = db.loop_sessions.docs[-1] if db.loop_sessions.docs else {}
    guard = engine.context.get('integrity_guard') or {}
    first = (guard.get('violations') or [{}])[0]
    result = {
        'engine_state': engine.state.value if hasattr(engine.state, 'value') else str(engine.state),
        'engine_phase': engine.phase,
        'ship_pending_present': 'ship_pending' in engine.context,
        'integrity_guard_present': bool(guard),
        'first_rule_fired': first.get('rule_fired'),
        'offending_path': first.get('offending_path'),
        'marker_text': first.get('marker_text'),
        'last_event': emitted[-1] if emitted else None,
        'persisted_state': persisted.get('state'),
        'persisted_context_kind': ((persisted.get('context') or {}).get('integrity_guard') or {}).get('violations', [{}])[0].get('rule_fired') if persisted else None,
    }
    print(result)
    assert engine.state == LoopState.FAILED
    assert engine.phase == 'ship'
    assert 'ship_pending' not in engine.context
    assert guard['violations'][0]['rule_fired'] == 'elision_marker'
    assert guard['violations'][0]['offending_path'] == 'README.md'
    assert emitted[-1]['state'] == 'failed'
    assert emitted[-1]['phase'] == 'ship'
    assert emitted[-1]['data']['kind'] == 'integrity_guard_rejected'
    assert db.loop_sessions.docs[-1]['state'] == 'failed'
    assert db.loop_sessions.docs[-1]['context']['integrity_guard']['violations'][0]['rule_fired'] == 'elision_marker'
    print('PASS: synthetic pre-ship guard blocked placeholder README content before ship_pending/manual ship gate')

if __name__ == '__main__':
    asyncio.run(main())
