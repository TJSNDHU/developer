"""
test_iter68_brain_commits_and_ambiguity_ui.py

Locks in:
  • Pattern #3 fix — Mode D system prompt no longer hard-bails on
    natural-language symptoms. Diagnoses with a Mode-A read plan instead.
  • Pattern #5-ish — get_brain_context() now accepts github_token and
    appends remote commit history (non-blocking on failure).
  • Frontend wiring of needs_confirm — ChatPanel renders the
    disambiguation banner and clears it on next send.
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Mode D — Pattern #3 threshold loosened ────────────────────────────

def test_mode_d_prompt_lists_valid_diagnostic_signals():
    """Symptoms must now be accepted as valid signal — Mode D should
    not bail with the generic 'insufficient signal' boilerplate."""
    src = _read("backend/services/mode_d_debugger.py")
    assert "VALID DIAGNOSTIC SIGNALS" in src, (
        "Mode D prompt must explicitly list what counts as valid signal "
        "(per RECURRING_ISSUES.md Pattern #3)"
    )
    for phrase in (
        "Natural-language symptoms",
        "Screenshot description",
        "ONLY bail with",
    ):
        assert phrase in src, f"Mode D prompt must mention: {phrase}"


def test_mode_d_prefers_read_plan_over_bail():
    src = _read("backend/services/mode_d_debugger.py")
    assert "prefer to output a Mode-A-style READ plan over bailing" in src


# ── project_brain accepts github_token + appends remote commits ───────

def test_brain_signature_accepts_github_token():
    src = _read("backend/services/project_brain.py")
    m = re.search(
        r"async def get_brain_context\((.*?)\) -> str:",
        src, re.DOTALL,
    )
    assert m
    sig = m.group(1)
    assert "github_token" in sig
    assert "github_token: str | None = None" in sig, (
        "github_token must be optional (= None) so existing callers "
        "keep working"
    )


def test_brain_swallows_github_api_failures():
    """The brain MUST be resilient to any GitHub API failure — bad
    token, rate limit, network. Otherwise a single API blip kills
    every chat turn."""
    src = _read("backend/services/project_brain.py")
    m = re.search(
        r"async def _maybe_append_github_commits.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "_maybe_append_github_commits helper must exist"
    body = m.group(0)
    # bare except — must catch ALL exceptions (broad on purpose)
    assert "except Exception:" in body
    # Status check before parsing
    assert "resp.status_code != 200" in body
    # Returns existing_context on failure (never raises)
    assert "return existing_context" in body


def test_brain_callers_pass_github_token():
    """The two main callers that hold a PAT must thread it through."""
    chat_src = _read("backend/routers/chat.py")
    proj_src = _read("backend/routers/cto_projects.py")
    # chat.py best-effort decrypts the PAT and forwards it
    assert "github_token=_pat" in chat_src
    # cto_projects calls also forward the user_token they already have
    assert "github_token=user_token" in proj_src


# ── Frontend wiring of needs_confirm ──────────────────────────────────

def test_api_js_forwards_full_mode_payload():
    """lib/api.js previously extracted only payload.mode and dropped the
    confidence/scores/needs_confirm fields. After the fix, onMode must
    receive the whole payload."""
    src = _read("frontend/src/lib/api.js")
    assert 'onMode?.(payload);' in src, (
        "api.js must forward the full mode payload, not just payload.mode"
    )
    # Old single-field pattern must be gone
    assert "onMode?.(payload.mode)" not in src


def test_chat_panel_handles_needs_confirm_payload():
    src = _read("frontend/src/components/ChatPanel.jsx")
    # State for the ambiguous-mode banner exists
    assert "modeAmbiguous" in src
    assert "setModeAmbiguous" in src
    # onMode handler reads m.needs_confirm
    assert "m.needs_confirm" in src
    # Backward compat: typeof string still works for old callers
    assert 'typeof m === "string"' in src
    # The banner data-testid + key controls exist
    assert 'data-testid="mode-ambiguous-banner"' in src
    assert 'data-testid="mode-ambiguous-ok"' in src
    # New prompt clears the banner
    assert "// Clear any leftover ambiguous-mode banner" in src \
        or "setModeAmbiguous(null);" in src
