"""
test_iter71_cosmetic_and_unlock_ux.py — Iter 71 audit-cleanup fixes.

Locks in:
  • admin.py no longer mis-labels real endpoints as "stubs"
  • domain.py docstring no longer says "P4 placeholder"
  • harden.py docstring honest about manual hardening
  • unlock.py POST returns user-facing `message`, GET flags `stale` requests
"""
from __future__ import annotations

import os


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def test_admin_stub_comment_removed():
    src = _read("backend/routers/admin.py")
    assert "Empty stubs for unbuilt features" not in src


def test_domain_docstring_no_longer_placeholder():
    src = _read("backend/routers/domain.py")
    assert "P4 placeholder" not in src
    # Honest new wording about what's actually shipped
    assert "Custom domain config" in src


def test_harden_docstring_honest():
    src = _read("backend/routers/harden.py")
    assert "(placeholder)" not in src
    assert "P1 server auto-hardening" not in src
    # Honest text: real status endpoint, manual hardening
    assert "polarisbuiltinc@gmail.com" in src
    assert "manual operation" in src


def test_unlock_post_returns_user_facing_message():
    src = _read("backend/routers/unlock.py")
    assert "polarisbuiltinc@gmail.com" in src
    assert '"message":' in src
    assert "reviews" in src and "manually" in src
    # Response shape includes both ok flag and message
    assert '"ok":         True' in src or '"ok": True' in src


def test_unlock_mine_flags_stale_pending_requests():
    src = _read("backend/routers/unlock.py")
    assert "stale" in src
    # 7-day threshold lives as a named constant for clarity
    assert "_STALE_AFTER_S = 7 * 86400" in src
    # The flag is only meaningful on pending requests
    assert 'd.get("status") == "pending"' in src


def test_no_frontend_harden_ui():
    """Sanity — if a future PR adds a 'Harden Server' button without
    actually wiring hardening work, that's user-facing fake UX. Lock
    the current zero-reference state."""
    import subprocess
    fe_dir = os.path.join(os.path.dirname(__file__), "..", "..",
                          "frontend", "src")
    result = subprocess.run(
        ["grep", "-rn", "/harden", fe_dir],
        capture_output=True, text=True,
    )
    # Allow zero hits OR only hits in comments/imports/lints
    real_hits = [
        ln for ln in result.stdout.splitlines()
        if "/harden" in ln and not ln.strip().startswith("//")
    ]
    assert len(real_hits) == 0, (
        "Frontend now references /harden — if you wired a UI button, "
        "make sure backend actually hardens, otherwise it's misleading UX. "
        f"Hits: {real_hits}"
    )
