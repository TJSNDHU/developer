"""Iter 76 follow-up — routing audit fixes.

Locks the missing routes / nav links the audit surfaced:
  • /admin/overview, /admin/architecture, /wrapped routes mounted
  • Shell sidebar has Ship Wall + Wrapped entries
  • Admin accepts an `initialTab` prop so /admin/architecture deep-links
"""
import os


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def test_new_routes_mounted():
    js = _read("frontend/src/App.jsx")
    assert '<Route path="/admin/overview"' in js
    assert '<Route path="/admin/architecture"' in js
    assert '<Route path="/wrapped"' in js
    assert "import AdminOverview from \"./pages/AdminOverview\"" in js
    assert "import Wrapped from \"./pages/Wrapped\"" in js


def test_shell_has_wall_and_wrapped_nav():
    js = _read("frontend/src/components/Shell.jsx")
    assert 'to: "/wall"' in js
    assert 'to: "/wrapped"' in js
    assert 'testid: "nav-wall"' in js
    assert 'testid: "nav-wrapped"' in js


def test_admin_accepts_initial_tab_prop():
    js = _read("frontend/src/pages/Admin.jsx")
    assert 'function Admin({ initialTab = "overview" })' in js
    assert "useState(initialTab)" in js


def test_wrapped_page_wires_ora_component():
    js = _read("frontend/src/pages/Wrapped.jsx")
    assert "OraWrapped" in js
    assert "requireAuth" in js
