# DELETE GATE — mandatory process for ANY file deletion (Iter 331)

**Why**: `tool_executor.py` was deleted 3× as "approved dead code" while being
LAZY-imported (`import` inside a function body inside `local_tools.py`) — the
delete only crashed at RUNTIME on the first tool call, breaking every chat
tool-call on production. Normal grep / IDE "find usages" missed it every time.
`VisualFixtures.jsx` / `LoopLiveFeedDemo.jsx` were likewise "approved" while
being live Playwright QA fixtures. This gate makes that bug class impossible.

## Layer 1 — Dependency-scan script (the tool)

```
./scripts/check-safe-to-delete.sh path/to/file.py
```

Catches what got missed 3×:
- Section 2: lazy/dynamic imports (`importlib.import_module`, in-function `import`)
- Section 3: string-keyed references (routing dicts like `TOOLS = {"tool_executor": …}`,
  route strings hit by Playwright specs)

## Layer 2 — Hard process gate (the actual fix)

A file enters the "approved for deletion" list ONLY with the script's FULL
output pasted. No output block → request auto-rejected, regardless of
confidence.

### Delete approval template (mandatory, no exceptions)

```
File: `path/to/file.py`
Script output:
[paste full ./scripts/check-safe-to-delete.sh output here]

Verdict: ✅ Safe / ❌ Not safe
Approved by: [name]
Date: [date]
```

## Layer 3 — Quarantine instead of hard-delete

Even with a ✅ verdict, never hard-delete immediately:

1. **Soft delete first** — either add at the top of the file:
   ```python
   import warnings
   warnings.warn(
       "<file> is scheduled for deletion on <date>. "
       "If you see this warning in logs, this file is still in use — DO NOT DELETE.",
       DeprecationWarning,
   )
   ```
   …or move the file to `_deprecated/<file>` (import path preserved via a stub
   if needed). Ship it. Monitor prod logs 1-2 weeks for
   `ModuleNotFoundError` / `DeprecationWarning`.
2. **Hard delete** — only after ZERO related errors in the quarantine window.

## Current verdict record (2026-07-28, script-verified)

| File | Verdict | Evidence |
|---|---|---|
| `backend/services/tool_executor.py` | ❌ NOT SAFE — restored | lazy-imported in `local_tools.py` `invoke_local_tool()`; every chat tool-call crashed |
| `backend/services/tools_bridge.py` | ❌ NOT SAFE | imported by `services/orchestrator.py:20`; orchestrator live in 8 routers; also `test_iter138` |
| `frontend/src/pages/VisualFixtures.jsx` | ❌ NOT SAFE | route `/dev/visual` used by Playwright `state_fixtures.spec.js` + `interaction_latency.spec.js`; App.jsx route |
| `frontend/src/pages/LoopLiveFeedDemo.jsx` | ❌ NOT SAFE | route `/dev/loop-live-feed` used by `a11y_journeys.spec.js` + `public_routes.spec.js`; App.jsx route |

**The founder-approved delete list #14 is therefore fully REJECTED.**
A new list requires fresh script runs + this template.
