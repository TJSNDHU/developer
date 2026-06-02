"""
tests/test_iter60_hosted_deploy_and_engage.py
==============================================

Iter 60 — Hosted Deploy (Vercel/Netlify hook) + Mode F (Engage / Market).
"""
from __future__ import annotations
import os
import inspect


def _read(rel: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", rel)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ─── Hosted Deploy router ──────────────────────────────────────────────

def test_hosted_deploy_router_registered():
    from routers.hosted_deploy import router as hd
    paths = [r.path for r in hd.routes]
    assert "/hosted-deploy/connect" in paths
    assert "/hosted-deploy/status/{project_id}" in paths
    assert "/hosted-deploy/ship" in paths
    assert "/hosted-deploy/disconnect/{project_id}" in paths


def test_main_includes_hosted_deploy():
    src = _read("main.py")
    assert "hosted_deploy_router" in src
    assert "app.include_router(hosted_deploy_router" in src


def test_vercel_hook_regex_accepts_real_url():
    from routers.hosted_deploy import _VERCEL_HOOK_RX
    ok = "https://api.vercel.com/v1/integrations/deploy/prj_abc123XYZ/hook_xyz"
    assert _VERCEL_HOOK_RX.match(ok) is not None
    bad = [
        "https://vercel.com/deploy/abc",                 # wrong host
        "http://api.vercel.com/v1/integrations/deploy/x/y",  # http not https
        "https://api.netlify.com/build_hooks/abc",       # wrong provider
        "https://api.vercel.com/v1/foo/bar",             # wrong path
    ]
    for b in bad:
        assert _VERCEL_HOOK_RX.match(b) is None, f"should reject: {b}"


def test_netlify_hook_regex_accepts_real_url():
    from routers.hosted_deploy import _NETLIFY_HOOK_RX
    ok = "https://api.netlify.com/build_hooks/65ab1234cd"
    assert _NETLIFY_HOOK_RX.match(ok) is not None
    bad = [
        "https://netlify.com/build_hooks/abc",
        "http://api.netlify.com/build_hooks/abc",
        "https://api.vercel.com/v1/integrations/deploy/x/y",
    ]
    for b in bad:
        assert _NETLIFY_HOOK_RX.match(b) is None


def test_ship_handler_is_async_and_authed():
    from routers.hosted_deploy import ship
    assert inspect.iscoroutinefunction(ship)
    sig = inspect.signature(ship)
    assert "authorization" in sig.parameters
    assert "body" in sig.parameters


def test_hook_stored_encrypted_not_plaintext():
    """The /connect handler must call encrypt() on the hook URL before
    writing to Mongo. Source-level pin so a regression that drops the
    encrypt call fails CI."""
    src = _read("routers/hosted_deploy.py")
    # Must encrypt the hook on the way in.
    assert "encrypt(body.hook_url" in src
    # Must store under deploy_hook_enc (NOT a plaintext field name).
    assert "deploy_hook_enc" in src
    assert "deploy_hook_plaintext" not in src  # smoke check — no such field


# ─── Mode F (Engage) ────────────────────────────────────────────────────

def test_engage_classifier_matches_market_questions():
    from services.mode_f_engage import is_engage_request
    pos = [
        "how do we beat cursor",
        "what is our USP vs replit",
        "who are my competitors",
        "write me a launch tweet about my project",
        "write a tagline for my SaaS",
        "what's the GTM strategy here",
        "how should I price my product",
        "who is the ideal customer for our app",
    ]
    for m in pos:
        assert is_engage_request(m), f"engage classifier missed: {m!r}"


def test_engage_classifier_rejects_coding_and_greetings():
    from services.mode_f_engage import is_engage_request
    neg = [
        "add a stripe checkout to /api/billing",
        "fix the bug in app.py",
        "hi there",
        "how do I read a file in Python",
        "show me the diff",
        # NB: "deploy this" is a Mode C signal, not Engage. Mode C runs
        # FIRST in classify_intent so even if F matched this, C wins.
    ]
    for m in neg:
        assert not is_engage_request(m), f"engage false positive: {m!r}"


def test_classify_intent_routes_F_for_engage():
    """End-to-end: classify_intent must return 'F' on engage prompts."""
    from routers.chat import classify_intent
    assert classify_intent("how do we beat cursor", None) == "F"
    assert classify_intent("write me a launch tweet", None) == "F"
    # And NOT F for coding:
    assert classify_intent("add a route to /api/users", None) != "F"


def test_run_engage_is_async_with_correct_signature():
    from services.mode_f_engage import run_engage
    assert inspect.iscoroutinefunction(run_engage)
    sig = inspect.signature(run_engage)
    for kw in ("prompt", "repo_ctx", "brain_ctx"):
        assert kw in sig.parameters


def test_chat_stream_routes_mode_F():
    """The chat router must dispatch Mode F to run_engage before falling
    through to the generic orchestrator (which would burn tokens on a
    full tool loop for a market question)."""
    src = _read("routers/chat.py")
    assert "Mode F (Engage" in src or "_mode == \"F\"" in src
    assert "from services.mode_f_engage import run_engage" in src
    assert '"mode": "F"' in src
