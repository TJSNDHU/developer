import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path('/app/backend')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.loop_engine import LoopEngine  # noqa: E402

class FakeCollection:
    def __init__(self): self.docs=[]
    async def update_one(self, filter_doc, update_doc, upsert=False):
        self.docs.append(dict(update_doc.get('$set', {})))
        return SimpleNamespace()
    async def insert_one(self, doc):
        self.docs.append(dict(doc)); return SimpleNamespace()
    async def find_one(self, *args, **kwargs): return None

class FakeDB:
    def __init__(self):
        self.loop_sessions=FakeCollection(); self.loop_errors=FakeCollection(); self.loop_events=FakeCollection(); self.loop_run_log=FakeCollection(); self.loop_audit_log=FakeCollection()

async def fake_verify_files(file_objs):
    return {'ok': True, 'results': [{'path': 'README.md', 'ok': True, 'linter': 'skip', 'stderr': ''}], 'errors': []}
async def fake_self_heal(*args, **kwargs):
    return []
async def main():
    db=FakeDB(); engine=LoopEngine(db,'loop_bugverify_iter318_verify','user_bugverify','proj_bugverify','add one comment to README')
    bad='# README\n[Rest of existing README content remains unchanged...]\n'
    engine.context['submitted_files']=[{'path':'README.md','content':bad}]
    engine.context['original_bytes_by_path']={'README.md': 5000}
    engine.context['errors_encountered']=[]
    with patch('services.loop_verify.verify_files', fake_verify_files), patch('services.loop_verify.self_heal', fake_self_heal):
        await engine._do_verify()
    report=engine.context['verification_results']
    print(report)
    assert report['ok'] is False
    row=report['results'][0]
    assert row['ok'] is False
    assert row['integrity_guard']['rule_fired']=='elision_marker'
    assert 'integrity_guard' in row['stderr']
    print('PASS: .md linter skip row downgraded by integrity guard')
if __name__=='__main__': asyncio.run(main())
