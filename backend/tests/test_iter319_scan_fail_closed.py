"""
test_iter319_scan_fail_closed.py — Iter 319

Bug 3: `_do_scan` had two independent defects that ganged up on the
founder in loop_678eea28436c4e:

  1. `_scan_text` was never imported into `_run_security_scan` or
     `_run_diff_security_scan` — both call sites reference the name
     but the import block that once carried it was truncated, so
     every scan raised `NameError: name '_scan_text' is not defined`.
     Ruff had been flagging this as F821 for iterations; the previous
     agent dismissed it as "pre-existing, unrelated to Iter 315" —
     that was wrong.

  2. `_do_scan` caught the resulting `NameError` in a generic except
     block, logged it to `scan_results = {"error": ...}`, and
     RETURNED — treating the scan crash as non-fatal. The state
     machine then proceeded to Ship as if the scan had passed. This
     is the exact wrong default for a security scanner.

Fix:
  • Import `_scan_text` from `routers.security_scan` at both call sites.
  • Wrap `_do_scan` in a fail-closed try/except: any exception
    transitions the loop to `FAILED` (kind='scan_exception'),
    ship-gate does NOT open.
"""
from __future__ import annotations

import re
from pathlib import Path


_ENGINE_SRC = Path("/app/backend/services/loop_engine.py").read_text()


def test_scan_text_imported_into_run_security_scan():
    """`_run_security_scan` calls `_scan_text(...)` — the name MUST
    be in scope, otherwise every legacy full-repo scan crashes."""
    m = re.search(
        r"async def _run_security_scan\(.*?(?=\nasync def |\ndef )",
        _ENGINE_SRC, re.DOTALL,
    )
    assert m, "_run_security_scan not found in loop_engine.py"
    body = m.group(0)
    assert "_scan_text(" in body, (
        "sanity: _run_security_scan is expected to call _scan_text"
    )
    # The import must exist inside the function OR the surrounding
    # module. Prefer explicit inside-function import to match the
    # pattern of the other lazy imports in this module.
    assert (
        "from routers.security_scan import" in body and
        "_scan_text" in body.split("from routers.security_scan import", 1)[1].split("\n", 1)[0]
    ) or (
        # Fallback: importer is on its own line inside the body.
        re.search(
            r"from routers\.security_scan import[^\n]*_scan_text",
            body,
        ) is not None
    ), (
        "Bug 3: _run_security_scan calls _scan_text() but never "
        "imports it — this is the F821 the previous agent dismissed. "
        "Add `_scan_text` to the routers.security_scan import block."
    )


def test_scan_text_imported_into_run_diff_security_scan():
    """`_run_diff_security_scan` also calls `_scan_text(...)`."""
    m = re.search(
        r"async def _run_diff_security_scan\(.*?(?=\nasync def |\ndef |\Z)",
        _ENGINE_SRC, re.DOTALL,
    )
    assert m, "_run_diff_security_scan not found in loop_engine.py"
    body = m.group(0)
    assert "_scan_text(" in body
    assert re.search(
        r"from routers\.security_scan import[^\n]*_scan_text",
        body,
    ) is not None, (
        "Bug 3: _run_diff_security_scan calls _scan_text() but never "
        "imports it. Live incident loop_678eea28436c4e produced "
        "`scan_results = NameError(\"name '_scan_text' is not defined\")`."
    )


def test_do_scan_fail_closed_on_exception():
    """Iter 319 fail-closed contract: any exception inside `_do_scan`
    (NameError, LLM timeout, whatever) must FAIL the loop — NOT set
    scan_results={"error":...} and return silently. The previous
    behaviour let the loop reach the ship-gate with a crashed scan."""
    m = re.search(
        r"    async def _do_scan\(.*?(?=\n    async def |\n    def |\n    # ── )",
        _ENGINE_SRC, re.DOTALL,
    )
    assert m, "_do_scan not found in loop_engine.py"
    body = m.group(0)

    # The exception handler must either call `self._fail(...)` (which
    # transitions to FAILED and releases the loop lock) OR set state
    # to FAILED + emit + return early. It MUST NOT silently return
    # with scan_results just carrying an "error" field.
    assert (
        "self._fail(" in body
        or "LoopState.FAILED" in body
    ), (
        "Bug 3 fail-closed: _do_scan's except block must transition "
        "the loop to FAILED — currently it just writes "
        "scan_results={'error': repr(e)} and returns, letting the "
        "state machine proceed to Ship with a crashed scan. This is "
        "the exact wrong default for a security scanner."
    )

    # Guard against the specific old behaviour: the except body must
    # not fall through to whatever comes AFTER _do_scan (i.e., ship).
    # A `return` after emitting FAILED is fine.
    assert (
        "scan_exception" in body
        or "scan_fail" in body
        or "scan_failed" in body
    ), (
        "Bug 3 fail-closed: _do_scan's failure path must carry a "
        "distinct marker (e.g. kind='scan_exception') in the FAILED "
        "emit so the founder sees WHY — not the generic 'failed'."
    )
