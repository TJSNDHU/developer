"""
Iter 212m-111 — Tests for:
  - Permanent night mode (TopBar theme toggle removed; theme="dark"
    hardcoded; legacy 3-state THEME_ORDER cycle gone).
  - Focus Mode auto-hide listeners (chat-focus event hides TopBar +
    auto-collapses Ask Advisor; cursor near right edge expands it).
  - Manual Ship gate (LoopEngine._do_ship pauses with
    data.kind="awaiting_ship"; confirm_ship() runs the actual
    GitHub push; POST /loop/{id}/confirm-ship endpoint exists).
"""
from __future__ import annotations

import pytest
from pathlib import Path


# ─── 1. Permanent night mode ──────────────────────────────────────────
def test_topbar_theme_toggle_removed():
    src = Path("/app/frontend/src/components/dashboard/v2/TopBar.jsx").read_text()
    # The user-facing toggle button + its testid must be gone.
    assert 'data-testid="ds2-theme-toggle"' not in src, \
        "Theme toggle button must be removed (permanent night mode)"
    # The 3-state cycle constant must be gone (we hard-pin dark).
    assert 'THEME_ORDER' not in src
    # Theme must still be dispatched as "dark" so any listener stays
    # synced with the locked theme.
    assert 'aurem:theme-changed' in src
    assert 'theme: "dark"' in src


def test_dashboard_hardcodes_dark_theme():
    src = Path("/app/frontend/src/pages/Dashboard.jsx").read_text()
    assert 'const effectiveTheme = "dark"' in src, \
        "Dashboard must hardcode effectiveTheme=\"dark\""
    # The runtime listener for aurem:theme-changed must be removed
    # (no need to listen — theme is permanent).
    assert 'window.addEventListener("aurem:theme-changed"' not in src


# ─── 2. Focus Mode ────────────────────────────────────────────────────
def test_chat_focus_event_dispatched_from_composer():
    src = Path("/app/frontend/src/components/ChatPanel.jsx").read_text()
    # The composer textarea must dispatch aurem:chat-focus on input/focus.
    assert "aurem:chat-focus" in src
    assert 'new CustomEvent("aurem:chat-focus")' in src


def test_topbar_listens_for_chat_focus_to_hide():
    src = Path("/app/frontend/src/components/dashboard/v2/TopBar.jsx").read_text()
    # The TopBar must auto-hide on aurem:chat-focus + reveal on mouse
    # at top (existing).
    assert "aurem:chat-focus" in src
    assert 'setAutoHidden(true)' in src
    assert 'e.clientY <= 20' in src


def test_dashboard_auto_collapses_advisor_on_chat_active():
    src = Path("/app/frontend/src/pages/Dashboard.jsx").read_text()
    # Effect should auto-collapse advisor when chatActive becomes true.
    assert "advisorAutoRef" in src
    assert "setAdvisorCollapsed(true)" in src
    # And expand it when cursor approaches right edge.
    assert "w - x <= 32" in src or "innerWidth" in src


# ─── 3. Manual Ship gate (backend) ────────────────────────────────────
def test_loop_engine_has_manual_ship_pause_and_confirm():
    src = Path("/app/backend/services/loop_engine.py").read_text()
    # _do_ship now prepares + pauses; confirm_ship runs the real commit.
    assert "async def confirm_ship(" in src
    assert 'kind":           "awaiting_ship"' in src or '"awaiting_ship"' in src
    # The pause must mark requires_user_action=True for the SSE event.
    assert "ship_pending" in src
    # The actual GitHub commit must NOT happen in _do_ship anymore —
    # confirm_ship is the only call site for commit_files().
    do_ship_block = src.split("async def _do_ship(", 1)[1].split("async def confirm_ship(", 1)[0]
    assert "from services.github_api_writer import commit_files" not in do_ship_block, \
        "_do_ship must NOT run commit_files — that's confirm_ship's job"


def test_loop_confirm_ship_endpoint_registered():
    src = Path("/app/backend/routers/loop.py").read_text()
    assert "/{loop_id}/confirm-ship" in src
    assert "confirm_ship" in src


@pytest.mark.asyncio
async def test_confirm_ship_approved_runs_commit_and_emits_completed(monkeypatch):
    """The confirm_ship(approved=True) flow must call commit_files()
    and emit a state=completed event with the real commit_sha."""
    from services import loop_engine as le

    emits: list[dict] = []
    persisted: list[dict] = []
    commits_called: list[dict] = []

    async def fake_commit_files(**kw):
        commits_called.append(kw)
        return {
            "sha":      "abc1234",
            "full_sha": "abc1234deadbeef",
            "html_url": "https://github.com/o/r/commit/abc1234",
        }

    async def fake_persist(db, doc):
        persisted.append(dict(doc))

    monkeypatch.setattr("services.github_api_writer.commit_files", fake_commit_files)
    monkeypatch.setattr(le, "_persist_session", fake_persist)

    eng = le.LoopEngine.__new__(le.LoopEngine)
    eng.db          = None
    eng.loop_id     = "loop_x"
    eng.user_id     = "u1"
    eng.project_id  = "p1"
    eng.user_message = "Add a new endpoint"
    eng.state       = le.LoopState.PAUSED_FOR_USER
    eng.phase       = "ship"
    eng.context     = {
        "ship_pending": {
            "owner":          "o",
            "repo":           "r",
            "branch":         "main",
            "token":          "ghp_x",
            "files":          {"app.py": "print('hi')"},
            "commit_message": "feat: add endpoint",
        },
    }

    async def fake_emit(state, phase, **kw):
        emits.append({"state": state.value if hasattr(state, "value") else state,
                      "phase": phase, **kw})
    eng._emit = fake_emit

    await eng.confirm_ship(True)

    assert commits_called and commits_called[0]["owner"] == "o"
    assert commits_called[0]["files"] == {"app.py": "print('hi')"}
    assert eng.state == le.LoopState.COMPLETED
    # Final event must carry the real commit_sha.
    final = [e for e in emits if e.get("state") == "completed"]
    assert final, "must emit a COMPLETED event after the commit"
    assert final[-1].get("data", {}).get("commit_sha") == "abc1234"
    # ship_pending must be cleared (contains the token — security).
    assert "ship_pending" not in eng.context


@pytest.mark.asyncio
async def test_confirm_ship_rejected_aborts_no_commit(monkeypatch):
    from services import loop_engine as le

    commits_called: list = []

    async def fake_commit_files(**kw):
        commits_called.append(kw)
        return {}

    async def fake_persist(db, doc):
        pass

    monkeypatch.setattr("services.github_api_writer.commit_files", fake_commit_files)
    monkeypatch.setattr(le, "_persist_session", fake_persist)

    eng = le.LoopEngine.__new__(le.LoopEngine)
    eng.db          = None
    eng.loop_id     = "loop_y"
    eng.user_id     = "u1"
    eng.project_id  = "p1"
    eng.user_message = "X"
    eng.state       = le.LoopState.PAUSED_FOR_USER
    eng.phase       = "ship"
    eng.context     = {
        "ship_pending": {
            "owner": "o", "repo": "r", "branch": "main", "token": "x",
            "files": {"a.py": "x"}, "commit_message": "m",
        },
    }
    async def fake_emit(state, phase, **kw): pass
    eng._emit = fake_emit

    await eng.confirm_ship(False)

    assert commits_called == [], "Cancelled ship must NOT call commit_files"
    assert eng.state == le.LoopState.ABORTED
    assert "ship_pending" not in eng.context  # token wiped


# ─── 4. Manual Ship gate (frontend) ───────────────────────────────────
def test_ship_pending_card_component_exists():
    p = Path("/app/frontend/src/components/ShipPendingCard.jsx")
    assert p.exists(), "ShipPendingCard.jsx must exist"
    src = p.read_text()
    assert 'data-testid="ship-to-github-btn"' in src
    assert 'data-testid="ship-cancel-btn"' in src
    assert "Ship to GitHub" in src


def test_chat_panel_renders_ship_pending_card_on_awaiting_ship():
    src = Path("/app/frontend/src/components/ChatPanel.jsx").read_text()
    assert "ShipPendingCard" in src
    assert 'data.kind === "awaiting_ship"' in src
    # ChatPanel must call the new confirmShip helper.
    assert "confirmShip" in src


def test_loop_api_exposes_confirm_ship_helper():
    src = Path("/app/frontend/src/lib/loopApi.js").read_text()
    assert "export async function confirmShip" in src
    assert "/confirm-ship" in src
