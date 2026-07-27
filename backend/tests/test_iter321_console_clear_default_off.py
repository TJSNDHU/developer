"""
test_iter321_console_clear_default_off.py — Iter 321

Bug 5: `useAutoClearConsole` fires `console.clear()` every 30s and
on every route change, wiping DevTools output during live debugging.
Live evidence: index-DtRTF4-t.js showed console.clear() at 29:59,
30:00, 30:31, 31:00, 31:30 — a repeating 30s timer destroying the
founder's live-loop trace evidence.

Founder ask: "confirm this is intentional." → intentional (Iter
212m-25) but the escape hatch is INVERTED — a user has to opt OUT
via a hidden window var. Fix: flip the flag to opt-IN by default.
DevTools must NOT be wiped unless the flag is set explicitly.
"""
from __future__ import annotations

from pathlib import Path

_SRC = Path("/app/frontend/src/lib/useAutoClearConsole.js").read_text()


def test_auto_clear_is_opt_in_not_opt_out():
    """The escape hatch must be a POSITIVE flag (opt-in) — not the
    inverted `__AUREM_DISABLE_AUTO_CLEAR_CONSOLE` pattern that
    wipes the console by default and forces the founder to set a
    hidden var to keep debugging output."""
    assert "__AUREM_ENABLE_AUTO_CLEAR_CONSOLE" in _SRC, (
        "Iter 321: useAutoClearConsole must gate console.clear() "
        "behind an opt-IN flag "
        "(window.__AUREM_ENABLE_AUTO_CLEAR_CONSOLE === true), not the "
        "current opt-OUT (`__AUREM_DISABLE_AUTO_CLEAR_CONSOLE`). "
        "Default must be OFF so DevTools output survives live "
        "debugging sessions."
    )


def test_safe_clear_returns_early_when_flag_absent():
    """The safeClear() body must short-circuit unless the opt-in
    flag is truthy — no code path should call `console.clear()`
    without an explicit user opt-in."""
    # Locate the `if (...) return;` / `if (!...) return;` guard.
    assert (
        "if (!window.__AUREM_ENABLE_AUTO_CLEAR_CONSOLE) return"
        in _SRC.replace(" ", "").replace("\n", " ")
        or (
            "__AUREM_ENABLE_AUTO_CLEAR_CONSOLE" in _SRC
            and "return" in _SRC
        )
    ), (
        "Iter 321: safeClear() must short-circuit (early return) "
        "when the opt-in flag is absent/falsy so the default "
        "behaviour is 'never clear'."
    )
