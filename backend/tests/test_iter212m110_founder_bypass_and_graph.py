"""
Iter 212m-110 — Tests for:
  - Founder/admin/unlimited users bypass token deduction on
    /codebase-health/fix (Bug Hunt free fixes).
  - Founder/admin/unlimited users bypass the sliding-window rate limit
    on /codebase-health/scan.
  - Sidebar Codebase Graph no longer navigates to /feature-window;
    it opens the GraphPanel drawer via `aurem:toggle-graph`.
"""
from __future__ import annotations

import pytest
from pathlib import Path


# ─── 1. Sidebar Codebase Graph wiring ─────────────────────────────────
def test_sidebar_graph_dispatches_drawer_event_not_navigate():
    """Dashboard.jsx must dispatch `aurem:toggle-graph` on sidebar
    "graph" click; the old `navigate('/feature-window')` call must be
    gone so non-founders no longer see ORA's internal feature map."""
    dash = Path("/app/frontend/src/pages/Dashboard.jsx").read_text()
    # Old leak must not be present any more.
    assert 'navigate("/feature-window")' not in dash, \
        "Sidebar 'graph' tool still navigates to ORA internal /feature-window"
    # New behaviour must dispatch the drawer event.
    assert 'aurem:toggle-graph' in dash, \
        "Sidebar 'graph' tool no longer dispatches aurem:toggle-graph"
    # And specifically inside the SidebarReal onToolClick path.
    sidebar_block = dash.split("onToolClick={(toolId) =>", 1)[-1].split("user={user}")[0]
    assert 'toolId === "graph"' in sidebar_block
    assert "aurem:toggle-graph" in sidebar_block


def test_sidebar_graph_visible_to_all_users():
    """SidebarBound.jsx no longer hides the Codebase Graph tool from
    non-founders — the drawer works for any connected GitHub repo."""
    sidebar = Path("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx").read_text()
    assert 'user?.is_admin' not in sidebar.split("TOOLS.filter", 1)[-1].split("}).map")[0], \
        "Codebase Graph still gated to founders/admins in SidebarBound"


# ─── 2. Bug Hunt / Health rate-limit founder bypass ───────────────────
def test_scan_route_treats_founder_as_admin():
    """codebase_health.py /scan endpoint must skip the sliding-window
    rate limit for any of is_admin / is_unlimited / tier=='founder'."""
    src = Path("/app/backend/routers/codebase_health.py").read_text()
    # Find the scan handler. The bypass union must mention all three flags.
    snippet = src.split("@router.post(\"/scan\")", 1)[1].split("@router.post", 1)[0]
    assert "is_unlimited" in snippet
    assert 'tier"' in snippet or '"founder"' in snippet
    assert "is_admin" in snippet


# ─── 3. /fix endpoint founder bypass ─────────────────────────────────
@pytest.mark.asyncio
async def test_fix_route_skips_token_deduction_for_founder(monkeypatch):
    """Founders / admins / unlimited users get tokens_charged=0 from
    POST /codebase-health/fix and no $inc on dev_users.tokens_remaining.

    Iter 212m-114: /fix now runs the REAL apply pipeline. We monkeypatch
    apply_finding_fix to return a successful commit so we can isolate
    the founder-bypass logic from the actual LLM+GitHub call."""
    from routers import codebase_health as ch

    deductions: list[dict] = []
    inserted: list[dict] = []

    class _Users:
        async def find_one(self, q, proj=None):
            return {"tokens_remaining": 0}  # no balance — would normally 402
        async def update_one(self, q, u):
            deductions.append({"q": q, "u": u})
            return type("R", (), {"modified_count": 1})()

    class _Tasks:
        async def insert_one(self, doc):
            inserted.append(doc)

    class _DB:
        dev_users = _Users()
        cto_tasks = _Tasks()

    async def fake_current_dev(authorization=None):
        return {
            "user_id":      "founder_1",
            "email":        "teji.ss1986@gmail.com",
            "is_admin":     True,
            "is_unlimited": True,
            "tier":         "founder",
        }

    async def fake_apply(*, db, user, project_id, finding):
        return {
            "ok": True, "commit_sha": "abc1234",
            "full_sha": "abc1234deadbeef",
            "html_url": "https://github.com/o/r/commit/abc1234",
            "file": finding["file"], "rule_id": finding["rule_id"],
            "message": "Fixed abc1234",
        }

    monkeypatch.setattr(ch, "current_dev", fake_current_dev)
    monkeypatch.setattr(ch, "get_db", lambda: _DB())
    import services.finding_fix_applier as ffa
    monkeypatch.setattr(ffa, "apply_finding_fix", fake_apply)

    body = {
        "project_id": "proj_1",
        "finding_id": "f1",
        "title":      "demo",
        "file":       "app.py",
        "line":       10,
        "message":    "x",
        "fix_hint":   "y",
        "tokens":     50,        # would normally 402 against balance=0
    }
    res = await ch.request_fix(body=body, authorization="Bearer x")

    assert res["ok"] is True
    assert res["tokens_charged"] == 0
    assert res["new_balance"] == 0
    assert res["commit_sha"] == "abc1234"
    # No token deduction occurred (founder bypass).
    assert deductions == [], "Founders must not be charged tokens on /fix"
    # The audit log record was still written.
    assert inserted and inserted[0]["kind"] == "health_fix"
    assert inserted[0]["status"] == "completed"
    assert inserted[0]["commit_sha"] == "abc1234deadbeef"


@pytest.mark.asyncio
async def test_fix_route_still_charges_non_founder(monkeypatch):
    """Non-founder users still hit the standard token deduction path.

    Iter 212m-114: with the REAL apply pipeline mocked to success, the
    deduction must happen once + 1000-50=950 balance + audit row."""
    from routers import codebase_health as ch

    deductions: list[dict] = []
    inserted: list[dict] = []

    class _Users:
        async def find_one(self, q, proj=None):
            return {"tokens_remaining": 1000}
        async def update_one(self, q, u):
            deductions.append({"q": q, "u": u})
            return type("R", (), {"modified_count": 1})()

    class _Tasks:
        async def insert_one(self, doc):
            inserted.append(doc)

    class _DB:
        dev_users = _Users()
        cto_tasks = _Tasks()

    async def fake_current_dev(authorization=None):
        return {
            "user_id": "free_1",
            "email":   "free@aurem.dev",
            "tier":    "free",
        }

    async def fake_apply(*, db, user, project_id, finding):
        return {
            "ok": True, "commit_sha": "deadbee",
            "full_sha": "deadbeef" * 5,
            "html_url": "https://github.com/o/r/commit/deadbee",
            "file": finding["file"], "rule_id": finding["rule_id"],
            "message": "Fixed deadbee",
        }

    monkeypatch.setattr(ch, "current_dev", fake_current_dev)
    monkeypatch.setattr(ch, "get_db", lambda: _DB())
    import services.finding_fix_applier as ffa
    monkeypatch.setattr(ffa, "apply_finding_fix", fake_apply)

    body = {
        "project_id": "proj_1",
        "finding_id": "f1",
        "title":      "demo",
        "file":       "app.py",
        "line":       10,
        "message":    "x",
        "fix_hint":   "y",
        "tokens":     50,
    }
    res = await ch.request_fix(body=body, authorization="Bearer x")
    assert res["tokens_charged"] == 50
    assert res["new_balance"] == 950
    assert res["commit_sha"] == "deadbee"
    assert len(deductions) == 1
    assert deductions[0]["u"] == {"$inc": {"tokens_remaining": -50}}
    assert inserted[0]["status"] == "completed"
    assert inserted[0]["tokens_charged"] == 50


@pytest.mark.asyncio
async def test_fix_route_refunds_tokens_when_patch_fails(monkeypatch):
    """Iter 212m-114 — if apply_finding_fix returns ok=False (patch
    rejected by re-validation, etc.), the deducted tokens MUST be
    refunded atomically and NO audit row is written."""
    from routers import codebase_health as ch
    from fastapi import HTTPException

    deductions: list[dict] = []
    inserted: list[dict] = []

    class _Users:
        async def find_one(self, q, proj=None):
            return {"tokens_remaining": 1000}
        async def update_one(self, q, u):
            deductions.append({"q": q, "u": u})
            return type("R", (), {"modified_count": 1})()

    class _Tasks:
        async def insert_one(self, doc):
            inserted.append(doc)

    class _DB:
        dev_users = _Users()
        cto_tasks = _Tasks()

    async def fake_current_dev(authorization=None):
        return {"user_id": "free_1", "tier": "free"}

    async def fake_apply(*, db, user, project_id, finding):
        return {"ok": False, "error": "patch_did_not_resolve_finding"}

    monkeypatch.setattr(ch, "current_dev", fake_current_dev)
    monkeypatch.setattr(ch, "get_db", lambda: _DB())
    import services.finding_fix_applier as ffa
    monkeypatch.setattr(ffa, "apply_finding_fix", fake_apply)

    with pytest.raises(HTTPException) as exc:
        await ch.request_fix(body={
            "project_id": "proj_1", "finding_id": "f1",
            "file": "app.py", "line": 10, "title": "x",
            "message": "x", "fix_hint": "x", "tokens": 50,
        }, authorization="Bearer x")
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert detail["error"] == "patch_did_not_resolve_finding"
    assert detail["tokens_refunded"] is True
    # Two deductions: -50 on entry, +50 refund.
    assert len(deductions) == 2
    assert deductions[0]["u"] == {"$inc": {"tokens_remaining": -50}}
    assert deductions[1]["u"] == {"$inc": {"tokens_remaining": 50}}
    # NO audit row should be written for a failed fix.
    assert inserted == []
