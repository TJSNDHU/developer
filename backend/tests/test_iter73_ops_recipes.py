"""
test_iter73_ops_recipes.py — Iter 73 ops-redirect feature.

Locks:
  • looks_like_ops_request() detects operational requests
  • chat.py emits ops_redirect SSE event
  • Frontend OpsRecipes page exists with all 5 recipes + testids
  • Admin sidebar has discoverable link
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Ops intent detector ───────────────────────────────────────────────

def test_looks_like_ops_request_detects_common_phrases():
    from services.mode_classifier import looks_like_ops_request
    for phrase in (
        "restart supervisor",
        "Can you restart supervisor for me?",
        "disk full on prod",
        "free disk space",
        "mongo connection refused",
        "ssh into the server and check logs",
    ):
        assert looks_like_ops_request(phrase), \
            f"Should detect ops request: {phrase!r}"


def test_looks_like_ops_request_rejects_codebase_requests():
    from services.mode_classifier import looks_like_ops_request
    for phrase in (
        "add dark mode",
        "my login is broken",
        "what does auth.py do?",
        "hi",
        "",
        None,
    ):
        assert not looks_like_ops_request(phrase or ""), \
            f"Should NOT detect ops in: {phrase!r}"


# ── chat.py wiring ────────────────────────────────────────────────────

def test_chat_emits_ops_redirect_sse_event():
    src = _read("backend/routers/chat.py")
    assert "looks_like_ops_request" in src
    assert '"type": "ops_redirect"' in src
    assert '"url": "/admin/ops"' in src


# ── Frontend OpsRecipes page ──────────────────────────────────────────

def test_ops_recipes_page_has_all_runbooks():
    src = _read("frontend/src/pages/OpsRecipes.jsx")
    for recipe_id in (
        "supervisor-restart",
        "service-logs",
        "disk-full",
        "mongo-connection",
        "deploy-stuck",
    ):
        assert f'id: "{recipe_id}"' in src, f"Recipe {recipe_id} missing"
    # Copy buttons + back button
    assert 'data-testid="ops-back"' in src
    assert "ops-copy-" in src
    # Honest support fallback
    assert "polarisbuiltinc@gmail.com" in src


def test_app_jsx_wires_ops_route():
    src = _read("frontend/src/App.jsx")
    assert "import OpsRecipes" in src
    assert '/admin/ops' in src


def test_admin_sidebar_links_to_ops_recipes():
    src = _read("frontend/src/pages/Admin.jsx")
    assert 'data-testid="admin-nav-ops"' in src
    assert "/admin/ops" in src


def test_chat_panel_renders_ops_redirect_banner():
    src = _read("frontend/src/components/ChatPanel.jsx")
    assert "opsRedirect" in src
    assert "setOpsRedirect" in src
    assert 'data-testid="ops-redirect-banner"' in src
    assert 'data-testid="ops-redirect-link"' in src
    # api.js forwards the new event type
    api_src = _read("frontend/src/lib/api.js")
    assert "ops_redirect" in api_src
    assert "onOpsRedirect" in api_src
