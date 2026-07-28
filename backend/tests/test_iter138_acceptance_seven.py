"""Iter 138 — 7-test acceptance suite as requested by founder.

Tests:
  1. execute_bash in TOOL_SPECS
  2. execute_bash blocks dangerous commands
  3. execute_bash runs safe commands
  4. run/cat trigger EXECUTE layer
  5. ANTI-HALLUCINATION in core layer
  6. tool count is 23
  7. upstream cooldown timer present
"""
import sys
sys.path.insert(0, '/app/backend')

# Test 1: execute_bash tool exists
from services.local_tools import TOOL_SPECS, invoke_local_tool
tool_names = [t['name'] for t in TOOL_SPECS]
assert 'execute_bash' in tool_names, 'execute_bash missing from TOOL_SPECS'
print('PASS Test 1: execute_bash in TOOL_SPECS')

# Test 2: execute_bash blocks dangerous commands
import asyncio
# Iter 331 — invoke_local_tool now takes a ctx dict; execute_bash is
# founder-gated (post-iter138 security hardening), so tests pass
# ctx={'is_founder': True} to exercise the tool itself.
result = asyncio.run(invoke_local_tool('execute_bash', {'command': 'rm -rf /'}, {'is_founder': True}))
assert result['ok'] is False, 'Should block rm command'
print('PASS Test 2: dangerous command blocked  ->  error=' + repr(result.get('error', ''))[:80])

# Test 2b: execute_bash is founder-gated — non-founder ctx must be refused
result = asyncio.run(invoke_local_tool('execute_bash', {'command': 'echo hi'}, {}))
assert result['ok'] is False, 'non-founder must be refused'
assert 'founder' in (result.get('error') or ''), 'refusal should mention founder gating'
print('PASS Test 2b: non-founder refused')

# Test 3: execute_bash runs safe commands (founder ctx)
result = asyncio.run(invoke_local_tool('execute_bash', {'command': 'echo hello'}, {'is_founder': True}))
assert result['ok'] is True, 'echo should work'
assert 'hello' in result['stdout'], 'stdout should contain hello'
print('PASS Test 3: safe command works  ->  stdout=' + repr(result['stdout'].strip()))

# Test 4: execute trigger includes run/terminal
from services.orchestrator import _wants_execute
assert _wants_execute('run this terminal command', False, []), 'run should trigger execute'
assert _wants_execute('cat /app/file.py', False, []), 'cat should trigger execute'
assert _wants_execute('execute the script', False, []), 'execute should trigger execute'
assert _wants_execute('find /app/backend', False, []), 'find /path should trigger execute'
print('PASS Test 4: run/cat/find/execute triggers EXECUTE layer')

# Test 5: ANTI-HALLUCINATION in core layer
from services.orchestrator import _SECTION_LAYER
ah_layer = _SECTION_LAYER.get('ANTI-HALLUCINATION CONTRACT — STRICTEST RULE')
assert ah_layer == 'core', f'Expected core, got {ah_layer!r}'
print('PASS Test 5: ANTI-HALLUCINATION in core layer')

# Test 6: tool documentation template still documents the toolbelt
# (Iter 331 — the hardcoded "23 total" count died long ago; TOOL_SPECS
# is now 38 and the template no longer embeds a count. Assert the
# structural invariants instead of a frozen number.)
from services.orchestrator import _TOOL_HELP_TEMPLATE
assert 'LOCAL FILESYSTEM' in _TOOL_HELP_TEMPLATE
assert 'execute_bash' in _TOOL_HELP_TEMPLATE
assert len(TOOL_SPECS) >= 23, f'toolbelt shrank below iter138 baseline: {len(TOOL_SPECS)}'
print(f'PASS Test 6: execute_bash documented, toolbelt size {len(TOOL_SPECS)} >= 23')

# Test 7: upstream giving up has timeout (5-min cooldown, not permanent)
from services.tools_bridge import (
    _upstream_giving_up_until, _UPSTREAM_COOLDOWN_S,
    _upstream_blocked, _open_upstream_cooldown,
)
assert _UPSTREAM_COOLDOWN_S == 300.0, f'Expected 300s default, got {_UPSTREAM_COOLDOWN_S}'
# Functional check — open the cooldown then immediately reset via simulation
import services.tools_bridge as _tb
_tb._upstream_giving_up = False
_tb._upstream_giving_up_until = 0.0
assert _upstream_blocked() is False, 'baseline must be unblocked'
_open_upstream_cooldown()
assert _upstream_blocked() is True, 'must block after cooldown opened'
# fast-forward by simulating expiry
_tb._upstream_giving_up_until = 0.0
assert _upstream_blocked() is False, 'must auto-reopen after cooldown expires'
print('PASS Test 7: upstream cooldown (300s) + auto-reopen works')

print()
print('ALL 7 TESTS PASSED — ship it')
