"""Regression probe: verify integrity guard is applied before self-heal,
but current _do_verify does NOT re-apply it after self-heal/re-verify.
A skipped .md linter can therefore become ok=True again if the healer
returns no replacement and verify_files still reports skip/ok.
"""
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
    async def insert_one(self, doc): self.docs.append(dict(doc)); return SimpleNamespace()
    async def find_one(self, *args, **kwargs): return None

class FakeDB:
    def __init__(self):
        self.loop_sessions=FakeCollection(); self.loop_errors=FakeCollection(); self.loop_events=FakeCollection(); self.loop_run_log=FakeCollection(); self.loop_audit_log=FakeCollection()

async def fake_verify_files(file_objs):
    # Mirrors markdown linter-skip behavior: ok=True without reading content.
    return {'ok': True, 'results': [{'path': f['path'], 'ok': True, 'linter': 'skip', 'stderr': ''} for f in file_objs], 'errors': []}

class FakeHealer:
    async def heal(self, *args, **kwargs):
        # Simulate healer failing/no-op; bad file content remains in file_objs.
        return {'status': 'escalate', 'output': None}
class FakeParliament:
    def __init__(self, *args, **kwargs): self.healer = FakeHealer()

async def main():
    db=FakeDB(); engine=LoopEngine(db,'loop_bugverify_iter318_verify_regression','user_bugverify','proj_bugverify','add one comment to README')
    bad='# README\n[Rest of existing README content remains unchanged...]\n'
    engine.context['submitted_files']=[{'path':'README.md','content':bad}]
    engine.context['original_bytes_by_path']={'README.md': 5000}
    engine.context['errors_encountered']=[]
    engine.context['self_heals_performed']=[]
    with patch('services.loop_verify.verify_files', fake_verify_files), patch('core.parliament.Parliament', FakeParliament):
        await engine._do_verify()
    report=engine.context['verification_results']
    evidence={
        'final_report_ok': report.get('ok'),
        'final_row': report.get('results', [{}])[0],
        'submitted_file_still_contains_elision': '[Rest of existing README content remains unchanged' in engine.context['submitted_files'][0]['content'],
        'self_heals_performed': engine.context.get('self_heals_performed'),
        'final_state': engine.state.value if hasattr(engine.state,'value') else str(engine.state),
        'final_phase': engine.phase,
    }
    print(evidence)
    if report.get('ok') is True and evidence['submitted_file_still_contains_elision']:
        print('NOT_FIXED_EVIDENCE: _do_verify can end ok=True for .md placeholder content after no-op self-heal because integrity guard is not re-run on subset reverify.')
    else:
        raise AssertionError('Expected to reproduce verify skip guard regression, but did not')

if __name__=='__main__': asyncio.run(main())
