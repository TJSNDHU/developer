"""Iter 77 — share-loop polish:
  T1 — milestone share toast on Dashboard
  T2 — OraWrapped fallback share text + tweet URL
  T3 — Settings OraWrapped mini-card embed
"""
import os
import re


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def test_milestone_toast_in_dashboard():
    js = _read("frontend/src/pages/Dashboard.jsx")
    assert "SHARE_MILESTONES" in js
    # Hits the live stats endpoint (no fake counter)
    assert "/wrapped/me?period=all_time" in js
    # Per-milestone localStorage key (no nagging)
    assert "aurem_toast_${milestone}" in js
    # Click takes user straight to /wrapped
    assert 'navigate("/wrapped")' in js
    # Milestones cover the asked-for 10/25/50 set
    m = re.search(r"SHARE_MILESTONES\s*=\s*\[([^\]]+)\]", js)
    assert m, "could not parse SHARE_MILESTONES literal"
    nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
    assert 10 in nums and 25 in nums and 50 in nums


def test_toast_supports_onclick_handler():
    js = _read("frontend/src/components/Toast.jsx")
    assert "onClick = null" in js
    # The handler is invoked AND the toast self-dismisses on click
    assert "t.onClick()" in js
    # pointerEvents: auto so the toast can actually catch the click
    assert 'pointerEvents: "auto"' in js


def test_ora_wrapped_fallback_share_text():
    js = _read("frontend/src/components/OraWrapped.jsx")
    assert "fallbackShareText" in js
    # Exact phrasing the user asked for
    assert "@AUREMcto" in js
    assert "#AUREM #ShipWithAI" in js
    assert "Flat fee, no token surprises" in js
    # Falls back when server share_text is empty
    assert "data?.share_text && data.share_text.trim()" in js


def test_settings_embeds_ora_wrapped():
    js = _read("frontend/src/pages/Settings.jsx")
    assert "import OraWrapped" in js
    assert "<OraWrapped" in js
    assert 'data-testid="settings-wrapped"' in js
