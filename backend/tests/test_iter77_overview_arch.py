"""Iter 77 follow-up — AdminOverview + Architecture refresh."""
import os
import re


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def test_overview_lists_iter_75_76_77_features():
    js = _read("frontend/src/pages/AdminOverview.jsx")
    expected = [
        "e2b sandbox runner",
        "TF-IDF search fallback",
        "esbuild JSX gate",
        "MULTI-FILE CONTRACT",
        "DB-backed task_plan",
        "Live preview pane",
        "Split-pane Dashboard",
        "Milestone share toast",
        "Settings Wrapped embed",
        "Subscription tiers",
        "Stripe webhook",
    ]
    for label in expected:
        assert label in js, f"AdminOverview missing feature row: {label}"
    # Test count surfaced is the current 452 (Iter 77)
    assert "452 passing" in js


def test_overview_feature_row_total_at_least_35():
    """Sanity: keep growing the audited surface every iter."""
    js = _read("frontend/src/pages/AdminOverview.jsx")
    rows = re.findall(r"<FeatureRow name=", js)
    assert len(rows) >= 35, f"only {len(rows)} feature rows — expected 35+"


def test_architecture_renders_code_surface_map():
    js = _read("frontend/src/pages/Admin.jsx")
    assert "arch-code-surface" in js
    assert "CODE_SURFACE" in js
    # All four columns must be present
    for col in ("Routers", "Services", "Pages", "Components"):
        assert f'title: "{col}"' in js
    # Critical service files are listed
    for svc in ("sandbox_runner.py", "subscription_tiers.py",
                "vanguard_scanner.py", "orchestrator.py"):
        assert svc in js
