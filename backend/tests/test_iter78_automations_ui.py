"""
test_iter78_automations_ui.py — Automations page + nav + route wiring.

Locks the front-end glue that exposes Iter 78 to users so a future
refactor can't quietly delete the page or the nav link.
"""
from __future__ import annotations

import os


BASE = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


def test_route_registered_in_app():
    src = _read("frontend/src/App.jsx")
    assert "import Automations" in src
    assert 'path="/automations"' in src


def test_nav_link_present_in_shell():
    src = _read("frontend/src/components/Shell.jsx")
    assert 'to: "/automations"' in src
    assert 'testid: "nav-automations"' in src


def test_page_has_required_testids_and_template_hint():
    src = _read("frontend/src/pages/Automations.jsx")
    for tid in ("automations-page", "webhook-url", "copy-webhook",
                "auto-name", "auto-repo", "auto-trigger",
                "auto-branch", "auto-template", "auto-save"):
        assert f'data-testid="{tid}"' in src, f"missing testid {tid}"
    # Template variable hints must be advertised for the user.
    for var in ("{branch}", "{pusher}", "{commit_messages}"):
        assert var in src
    # Webhook URL is built from the canonical API base.
    assert "API_BASE" in src
    assert "/automations/webhook/github" in src
