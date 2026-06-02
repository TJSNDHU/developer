"""
tests/test_iter56_oauth_redirect_origin.py
============================================

Iter 56 — deployment-blocker fix pinned at the source.

The deployment agent caught the GitHub OAuth flow building its
redirect URL from `process.env.REACT_APP_BACKEND_URL` instead of
`window.location.origin`. This breaks auth across environments
(preview, auremcto.com, custom domain) because the build-time
backend URL doesn't match the runtime origin.

A regression here is invisible until a user actually tries OAuth on
a non-preview domain, so we pin both call sites at the SOURCE.
"""
from __future__ import annotations
import os


def _read(rel: str) -> str:
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src", rel
    )
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_login_oauth_uses_window_location_origin():
    """Login GitHub OAuth button must not read REACT_APP_BACKEND_URL
    inside its onClick. Build-time backend URL leaks the wrong domain
    into the OAuth callback when the user is on auremcto.com or a
    custom domain."""
    src = _read("pages/Login.jsx")
    # The new line MUST be the live-origin assignment inside the
    # OAuth click handler.
    assert "window.location.origin" in src
    # And the smoking-gun pattern MUST be gone from the OAuth path.
    # We allow the env var elsewhere (e.g. inside a comment), so we
    # specifically look for the bad assignment style.
    assert 'const base = process.env.REACT_APP_BACKEND_URL' not in src


def test_projects_oauth_uses_window_location_origin():
    """Same fix for the in-app 'Connect a repo' modal which has its
    own OAuth entry point."""
    src = _read("pages/Projects.jsx")
    assert "window.location.origin" in src
    assert 'const base = process.env.REACT_APP_BACKEND_URL' not in src


def test_no_other_oauth_call_sites_left_with_envvar():
    """Sweep for any future regression: anywhere we navigate the
    browser to `.../github/oauth/connect`, the base must come from
    the runtime origin, not the env var."""
    import re
    sweep_files = [
        "pages/Login.jsx",
        "pages/Signup.jsx",
        "pages/Projects.jsx",
        "components/AuremAdminPanel.jsx",
    ]
    for rel in sweep_files:
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "frontend", "src", rel,
        )
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        # Find every github/oauth/connect navigation.
        for m in re.finditer(r"github/oauth/connect", src):
            # Walk back ~250 chars and check the base var.
            window = src[max(0, m.start() - 300): m.start()]
            if "REACT_APP_BACKEND_URL" in window and "window.location.origin" not in window:
                raise AssertionError(
                    f"{rel}: OAuth nav still using build-time backend URL "
                    f"instead of window.location.origin"
                )
