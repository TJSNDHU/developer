"""
test_iter66_design_tokens_lock.py — Iter 66.

Pinpoint test ensuring every color token in /app/frontend/src/index.css
matches the design spec the user shared (Screenshots 2026-06-02 20:18).

If a future agent (or LLM) drifts ANY token, this test fails loudly
with the expected hex so they can't silently re-introduce purple, lower
the contrast, or invent new shades.
"""
from __future__ import annotations

import os
import re


SPEC = {
    "--bg":             "#07080d",
    "--bg-elev":        "#0d1018",
    "--panel":          "#11141d",
    "--panel-2":        "#161a25",
    "--text":           "#f4ecdc",
    "--text-dim":       "#a39d8a",
    "--text-faint":     "#6b6557",
    "--accent":         "#ff8a2a",
    "--accent-end":     "#e57718",
    "--accent-2":       "#ffc560",
    "--ok":             "#6dd4a1",
    "--danger":         "#ff6b6b",
    "--warn":           "#ffc560",
    "--info":           "#7da4ff",
}

RGBA_SPEC = {
    "--border":        "rgba(255, 200, 120, 0.10)",
    "--border-strong": "rgba(255, 200, 120, 0.22)",
    "--accent-soft":   "rgba(255, 138, 42, 0.12)",
    "--danger-soft":   "rgba(255, 107, 107, 0.12)",
}


def _css():
    p = os.path.join(os.path.dirname(__file__), "..", "..",
                     "frontend", "src", "index.css")
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def test_root_tokens_match_spec_exactly():
    css = _css()
    m = re.search(r":root\s*\{([^}]+)\}", css, re.DOTALL)
    assert m, ":root block must exist"
    root_body = m.group(1)
    for token, expected in SPEC.items():
        # Allow trailing comment after the value
        pat = re.compile(
            rf"{re.escape(token)}\s*:\s*({re.escape(expected)})\s*;",
            re.IGNORECASE,
        )
        assert pat.search(root_body), (
            f"Design token {token} drift — expected exactly '{expected}'. "
            f"Spec lives in /app/memory/RECURRING_ISSUES.md / design ref. "
            f"Do NOT silently change hex values."
        )


def test_rgba_tokens_match_spec():
    css = _css()
    m = re.search(r":root\s*\{([^}]+)\}", css, re.DOTALL)
    root_body = m.group(1)
    for token, expected in RGBA_SPEC.items():
        # Normalize whitespace before compare
        norm_expected = re.sub(r"\s+", "", expected)
        pat = re.compile(
            rf"{re.escape(token)}\s*:\s*(rgba\([^)]+\))\s*;",
        )
        m2 = pat.search(root_body)
        assert m2, f"Token {token} missing from :root"
        assert re.sub(r"\s+", "", m2.group(1)) == norm_expected, (
            f"{token} drift — expected '{expected}', got '{m2.group(1)}'"
        )


def test_primary_button_uses_token_gradient():
    """The primary button gradient endpoints must come from CSS variables,
    not hardcoded hex. Iter 66 introduced --accent-end for that purpose."""
    css = _css()
    m = re.search(r"\.btn-primary\s*\{([^}]+)\}", css, re.DOTALL)
    assert m, ".btn-primary block must exist"
    body = m.group(1)
    assert "var(--accent)" in body
    assert "var(--accent-end)" in body
    # Spec: disabled opacity must be 0.4 (not 0.5)
    full = css[m.end():m.end() + 400]
    assert "opacity: 0.4" in full, (
        ".btn-primary:disabled must use opacity: 0.4 per spec"
    )


def test_danger_button_exists_with_correct_bg():
    """Spec shows a danger button (rgba(255,107,107,0.12) bg). Iter 66
    materializes that as `.btn-danger` so callers don't reinvent it."""
    css = _css()
    m = re.search(r"\.btn-danger\s*\{([^}]+)\}", css, re.DOTALL)
    assert m, ".btn-danger class must exist (Iter 66)"
    body = m.group(1)
    assert "var(--danger-soft)" in body
    assert "var(--danger)" in body


def test_status_palette_strip_documented():
    """The user-shared spec strip ('ok=#6dd4a1 · error=#ff6b6b · warn=#ffc560
    · info=#7da4ff · accent=#ff8a2a') must exist as code-level truth."""
    css = _css()
    # All 5 colors present
    for hex_val in ("#6dd4a1", "#ff6b6b", "#ffc560", "#7da4ff", "#ff8a2a"):
        assert hex_val in css, (
            f"Status palette color {hex_val} missing from index.css"
        )
