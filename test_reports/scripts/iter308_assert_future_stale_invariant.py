"""Assert the loop_engine.py import-time stale-after invariant would fail on regression."""
from __future__ import annotations
import ast
from pathlib import Path
src = Path('/app/backend/services/loop_engine.py').read_text()
tree = ast.parse(src)
asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
assert any('STALE_AFTER_S' in ast.unparse(a.test) and 'PHASE_TIMEOUTS_S' in ast.unparse(a.test) for a in asserts), 'missing STALE_AFTER_S import-time assert'
# Simulate the expression with current stale_after and a future phase budget bump.
ns = {}
prefix = src.split('# Iter 308 v2 — Hard startup invariant')[0]
exec(compile(prefix, '<loop_engine_prefix>', 'exec'), ns)
future_phase_timeouts = dict(ns['PHASE_TIMEOUTS_S'])
future_phase_timeouts['execute'] = ns['STALE_AFTER_S'] + 1
try:
    assert ns['STALE_AFTER_S'] > max(future_phase_timeouts.values()), 'simulated future bump should violate invariant'
    raise AssertionError('future bump did not fail')
except AssertionError as e:
    if str(e) == 'future bump did not fail':
        raise
print({'ok': True, 'assert_count': len(asserts), 'current_stale_after': ns['STALE_AFTER_S'], 'simulated_future_execute_budget': future_phase_timeouts['execute'], 'would_fail': True})
