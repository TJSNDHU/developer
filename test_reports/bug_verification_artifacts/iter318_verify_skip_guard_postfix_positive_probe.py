"""Positive regression probe for Iter 318 hardening.

Exercises LoopEngine._do_verify end-to-end with mocked verify_files and
Parliament healer. The initial .md linter skip row is downgraded by the
integrity guard, healer escalates/no-ops, subset reverify returns another
skip/ok:true row, and the post-merge re-sweep must keep final ok false.
"""
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
        self.docs.append(dict(update_doc.get('$set', {})))
        return SimpleNamespace()

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace()

    async def find_one(self, *args, **kwargs):
        return None


class FakeDB:
    def __init__(self):
        self.loop_sessions = FakeCollection()
        self.loop_errors = FakeCollection()
        self.loop_events = FakeCollection()
        self.loop_run_log = FakeCollection()
        self.loop_audit_log = FakeCollection()


async def fake_verify_files(file_objs):
    # Mirrors markdown linter-skip behavior: ok=True without reading content.
    return {
        'ok': True,
        'results': [
            {'path': f['path'], 'ok': True, 'linter': 'skip', 'stderr': ''}
            for f in file_objs
        ],
        'errors': [],
    }


class FakeHealer:
    async def heal(self, *args, **kwargs):
        # No replacement, so the bad placeholder content remains in file_objs.
        return {'status': 'escalate', 'output': None}


class FakeParliament:
    def __init__(self, *args, **kwargs):
        self.healer = FakeHealer()


async def main():
    db = FakeDB()
    engine = LoopEngine(
        db,
        'loop_bugverify_iter318_verify_postfix',
        'user_bugverify',
        'proj_bugverify',
        'add one comment to README',
    )
    bad = '# README\n[Rest of existing README content remains unchanged...]\n'
    engine.context['submitted_files'] = [{'path': 'README.md', 'content': bad}]
    engine.context['original_bytes_by_path'] = {'README.md': 5000}
    engine.context['errors_encountered'] = []
    engine.context['self_heals_performed'] = []

    with patch('services.loop_verify.verify_files', fake_verify_files), \
         patch('core.parliament.Parliament', FakeParliament):
        await engine._do_verify()

    report = engine.context['verification_results']
    row = report['results'][0]
    evidence = {
        'final_report_ok': report.get('ok'),
        'final_row_ok': row.get('ok'),
        'final_linter': row.get('linter'),
        'integrity_rule': (row.get('integrity_guard') or {}).get('rule_fired'),
        'submitted_file_still_contains_elision': '[Rest of existing README content remains unchanged' in engine.context['submitted_files'][0]['content'],
        'self_heals_performed_count': len(engine.context.get('self_heals_performed') or []),
        'final_state': engine.state.value if hasattr(engine.state, 'value') else str(engine.state),
        'final_phase': engine.phase,
        'errors': report.get('errors'),
    }
    print(evidence)
    assert evidence['submitted_file_still_contains_elision'] is True
    assert report['ok'] is False
    assert row['ok'] is False
    assert row['linter'] == 'skip'
    assert row['integrity_guard']['rule_fired'] == 'elision_marker'
    assert engine.state == LoopState.PAUSED_FOR_USER
    assert engine.phase == 'verify'
    assert report['errors'].count('README.md: integrity_guard:elision_marker') == 1
    print('PASS: post-heal subset reverify merge did not restore .md skip row to ok:true')


if __name__ == '__main__':
    asyncio.run(main())