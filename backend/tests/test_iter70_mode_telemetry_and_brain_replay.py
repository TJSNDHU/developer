"""
test_iter70_mode_telemetry_and_brain_replay.py

Locks in Iter 70 endpoints + frontend wiring.
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Mode classifier telemetry ─────────────────────────────────────────

def test_log_classification_helper_exists():
    from services.mode_classifier import log_classification
    import inspect
    assert inspect.iscoroutinefunction(log_classification), (
        "log_classification must be async so chat.py can fire-and-forget it"
    )


def test_log_classification_is_fire_and_forget_safe():
    """Calling with db=None or empty result must not raise — chat path
    must NEVER break because telemetry failed."""
    import asyncio
    from services.mode_classifier import log_classification
    asyncio.run(log_classification(None, {}, ""))
    asyncio.run(log_classification(None, {"mode": "A", "confidence": 1.0}, "hi"))


def test_telemetry_endpoint_registered_and_gated():
    from routers.admin import router as adm
    paths = [r.path for r in adm.routes]
    assert "/admin/mode-telemetry" in paths
    src = _read("backend/routers/admin.py")
    m = re.search(r"async def mode_telemetry\(.*?(?=\n@router\.|\nasync def )",
                  src, re.DOTALL)
    assert m
    body = m.group(0)
    assert "_require_admin(authorization)" in body
    # Aggregates must be present in the response
    for k in ("mode_counts", "needs_confirm_pct", "avg_confidence",
              "f12_forced_pct", "recent", "total"):
        assert f'"{k}"' in body


def test_chat_py_fires_telemetry_logging():
    src = _read("backend/routers/chat.py")
    assert "log_classification" in src
    # Wrapped in asyncio.create_task so it doesn't block the SSE stream
    assert "asyncio.create_task(" in src and "log_classification" in src
    # And inside a try/except so failures swallow silently
    snippet = src[src.find("log_classification"):src.find("log_classification") + 500]
    assert "except Exception:" in snippet


# ── Brain replay ──────────────────────────────────────────────────────

def test_brain_replay_endpoint_registered_and_gated():
    from routers.admin import router as adm
    paths = [r.path for r in adm.routes]
    assert "/admin/brain/{project_id}/replay" in paths
    src = _read("backend/routers/admin.py")
    m = re.search(r"async def admin_brain_replay\(.*?(?=\n@router\.|\Z)",
                  src, re.DOTALL)
    assert m
    body = m.group(0)
    assert "_require_admin(authorization)" in body
    # Must reject empty / oversized questions
    assert "question required" in body
    assert "question too long" in body
    # Read-only contract: no insert_one / commit-firing calls in the
    # executable body (we exclude the docstring which legitimately
    # mentions Vanguard / commit in describing what it does NOT do).
    body_no_docstring = re.sub(r'"""[\s\S]*?"""', '', body, count=1)
    assert "insert_one" not in body_no_docstring
    assert "commit_files" not in body_no_docstring
    assert "vanguard_scan" not in body_no_docstring.lower()


def test_brain_replay_uses_same_brain_context_as_chat():
    """The replay must call get_brain_context with github_token so the
    sandbox answer matches what a real chat turn would see."""
    src = _read("backend/routers/admin.py")
    m = re.search(r"async def admin_brain_replay\(.*?(?=\n@router\.|\Z)",
                  src, re.DOTALL)
    body = m.group(0)
    assert "get_brain_context" in body
    assert "github_token=token" in body


# ── Frontend wiring ───────────────────────────────────────────────────

def test_admin_overview_fetches_and_renders_telemetry():
    src = _read("frontend/src/pages/AdminOverview.jsx")
    assert '/admin/mode-telemetry' in src
    assert 'data-testid="mode-telemetry-panel"' in src
    assert 'data-testid="mode-avg-confidence"' in src
    assert 'data-testid="mode-needs-confirm-pct"' in src
    # Per-mode count testid pattern
    assert "mode-count-${m}" in src


def test_brain_dump_renders_replay_form():
    src = _read("frontend/src/pages/BrainDump.jsx")
    assert "function BrainReplay" in src
    for testid in ("brain-replay", "brain-replay-input",
                   "brain-replay-ask", "brain-replay-answer"):
        assert f'data-testid="{testid}"' in src
    # Hits the right endpoint
    assert "/admin/brain/${projectId}/replay" in src
    # Read-only disclaimer must be visible to admin so they don't
    # think it's a live chat
    assert "No commits, no writes" in src or "read-only" in src.lower()
