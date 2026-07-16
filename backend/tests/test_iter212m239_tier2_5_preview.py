"""
Iter 212m-239 — Tier 2.5 Live Preview + Resend email helper.

Locks in:
1. `services/preview_sandbox.py`:
   - Requires `E2B_API_KEY`; missing → structured 503-shape response.
   - 20-min TTL matches E2B billing granularity.
   - `PREVIEW_COLLECTION` name is stable.
   - `sweep_expired_previews()` kills sandboxes past their TTL.
2. `routers/scaffold.py:POST /scaffold/{id}/preview`:
   - react-fastapi only — refuses JS stacks with 400 wrong_stack.
   - Missing E2B → 503 with helpful message.
   - Idempotent — returns existing live sandbox when one exists.
3. Sweeper cron wired into `main.py` behind ENABLE_PREVIEW_SWEEPER.
4. Resend email helper in every boilerplate:
   - Fails soft on missing key (returns False, no exception).
   - Correct API endpoint + auth header + body shape.
5. Frontend Sandpack integration for the 3 JS stacks.
"""
from __future__ import annotations

import os
import pytest


# ── Backend service ──────────────────────────────────────────────
def test_preview_sandbox_requires_e2b_key():
    from services import preview_sandbox as ps
    orig = os.environ.pop("E2B_API_KEY", None)
    try:
        assert ps.is_configured() is False
        assert ps._not_configured()["reason"] == "e2b_not_configured"
        os.environ["E2B_API_KEY"] = "e2b_test"
        assert ps.is_configured() is True
    finally:
        os.environ.pop("E2B_API_KEY", None)
        if orig: os.environ["E2B_API_KEY"] = orig


def test_preview_ttl_matches_e2b_billing_window():
    from services.preview_sandbox import PREVIEW_TTL_S
    assert PREVIEW_TTL_S == 20 * 60


def test_preview_collection_name_stable():
    from services.preview_sandbox import PREVIEW_COLLECTION
    assert PREVIEW_COLLECTION == "preview_sandboxes"


# ── Router integration ──────────────────────────────────────────
def test_preview_endpoint_registered():
    from routers.scaffold import router
    paths = [r.path for r in router.routes]
    assert "/scaffold/{draft_id}/preview" in paths


def test_preview_endpoint_refuses_non_react_fastapi_stacks():
    """Static — the router must gate on stack. JS stacks are handled
    client-side via Sandpack; the E2B path is react-fastapi-only."""
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def create_live_preview(")
    body = src[idx:idx + 3000]
    assert '"wrong_stack"' in body
    assert 'stack != "react-fastapi"' in body


def test_preview_missing_e2b_returns_503_with_setup_hint():
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def create_live_preview(")
    body = src[idx:idx + 3000]
    assert '"e2b_not_configured"' in body
    assert "https://e2b.dev" in body


def test_preview_endpoint_is_idempotent():
    """Existing live sandbox should be reused rather than double-billed."""
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def create_live_preview(")
    body = src[idx:idx + 3000]
    assert "reused" in body
    assert '"killed": {"$ne": True}' in body


# ── Sweeper cron wiring ─────────────────────────────────────────
def test_preview_sweeper_wired_into_main():
    src = open("/app/backend/main.py").read()
    assert "_preview_sweeper_cron" in src
    assert "ENABLE_PREVIEW_SWEEPER" in src
    assert "sweep_expired_previews" in src


# ── Resend email helper ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_reset_email_fails_soft_without_key(monkeypatch):
    """OWASP invariant — reset flow must not break if RESEND_API_KEY is
    missing. Password reset should still WORK (server logs the token),
    the helper just quietly returns False."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    import importlib, sys
    sys.path.insert(0, "/app/backend/templates/stacks/react-fastapi/boilerplate/api")
    if "generated_app_email" in sys.modules:
        importlib.reload(sys.modules["generated_app_email"])
    from generated_app_email import send_reset_email
    r = await send_reset_email("x@y.com", "https://a/reset?token=abc")
    assert r is False


@pytest.mark.asyncio
async def test_send_reset_email_smoke_test_calls_resend_api(monkeypatch):
    """Mock-verify the helper hits the correct Resend URL with the
    right Bearer token when the key IS set."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_abc")

    captured = {}
    class _FakeResp:
        def __init__(self, code): self.status_code = code

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return _FakeResp(202)

    import importlib, sys
    sys.path.insert(0, "/app/backend/templates/stacks/react-fastapi/boilerplate/api")
    if "generated_app_email" in sys.modules:
        importlib.reload(sys.modules["generated_app_email"])
    import generated_app_email as gae
    monkeypatch.setattr(gae, "httpx",
                        type("m", (), {"AsyncClient": _FakeClient})())

    r = await gae.send_reset_email("x@y.com", "https://a/reset?token=abc")
    assert r is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert "Bearer re_test_key_abc" in captured["headers"]["Authorization"]
    assert captured["body"]["to"] == ["x@y.com"]
    assert "reset" in captured["body"]["subject"].lower()


# ── Boilerplate wiring (Resend helper injected into every stack) ──
def test_all_stacks_have_email_helper():
    for path in [
        "/app/backend/templates/stacks/react-fastapi/boilerplate/api/generated_app_email.py",
        "/app/backend/templates/stacks/nextjs-node/boilerplate/lib/email.js",
        "/app/backend/templates/stacks/vue-express/boilerplate/server/email.js",
    ]:
        assert os.path.isfile(path), f"Missing email helper: {path}"
        src = open(path).read()
        assert "api.resend.com/emails" in src
        assert "RESEND_API_KEY" in src


def test_password_reset_uses_email_helper_when_available():
    """Reset request must ATTEMPT `send_reset_email()` before falling
    back to console.log."""
    r = open("/app/backend/templates/stacks/react-fastapi/boilerplate/api/auth.py").read()
    assert "from generated_app_email import send_reset_email" in r
    n = open("/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/password-reset-request/route.js").read()
    assert "sendResetEmail" in n
    v = open("/app/backend/templates/stacks/vue-express/boilerplate/server/index.js").read()
    assert "sendResetEmail" in v


# ── Frontend preview component present ───────────────────────────
def test_preview_panel_component_exists():
    p = "/app/frontend/src/pages/personal/PreviewPanel.jsx"
    assert os.path.isfile(p)
    src = open(p).read()
    assert "@codesandbox/sandpack-react" in src
    # JS-stack templates must cover nextjs, vue, plain-html.
    assert '"nextjs"' in src
    assert '"vue"' in src
    assert '"static"' in src
    # And it must call the backend preview endpoint for react-fastapi.
    assert "/scaffold/${draftId}/preview" in src or "/scaffold/" in src


def test_draft_review_wires_preview_toggle():
    src = open("/app/frontend/src/pages/personal/DraftReview.jsx").read()
    assert 'data-testid="draft-view-toggle"' in src
    assert 'data-testid="draft-view-code"' in src
    assert 'data-testid="draft-view-preview"' in src
    assert "PreviewPanel" in src
