"""
test_iter162_mode_d_tightening.py — regression tests for Iter 162.

Bug reported by founder (production):

    User typed: "ye error de rha hai chek kro kya prob hai"
    System replied: "🟢 ROOT CAUSE: insufficient signal to diagnose"

Root cause: the old `is_debug_request()` regex had `\b(error|bug|...)\b`
which fired on any casual mention of those words. Mode D then bailed
honestly because no real debug evidence was attached.

The fix splits signals into HARD (always fires) vs SOFT (only fires
when paired with a debug-action verb).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.mode_d_debugger import is_debug_request   # noqa: E402


# ── Casual / Hinglish — must NOT fire Mode D ─────────────────────────

CASUAL_NO_DEBUG = [
    "ye error de rha hai chek kro kya prob hai",
    "kuch broken hai shayad",
    "looks like something has a bug",
    "error aa rha hai",
    "the app is not working",
    "i think there's an error somewhere",
    "the build is broken",
    "kuch error hai",
    "bug hai isme",
    "hello",
    "what does this file do?",
    "explain how the auth flow works",
]


def test_casual_phrases_do_not_fire_mode_d():
    """Single mentions of error/bug/broken/etc without a debug action
    verb must NOT route to Mode D. They should fall through to A/B/C
    so the user gets a normal helpful reply."""
    misroutes = [p for p in CASUAL_NO_DEBUG if is_debug_request(p)]
    assert not misroutes, (
        "Casual phrases still routing to Mode D — would produce the "
        f"'insufficient signal' UX regression: {misroutes}"
    )


# ── Real debug requests — MUST still fire Mode D ─────────────────────

REAL_DEBUG_FIRES = [
    "TypeError: Cannot read properties of undefined",
    "I'm getting a 500 error on /api/projects",
    "the endpoint returns 422 sometimes",
    "ValueError: invalid literal for int()",
    "ECONNREFUSED when calling the gateway",
    "stack trace: \n  at Component (App.jsx:88)",
    'fix this 404 from /api/users',
    "debug the login flow",
    "what's wrong with the build — keeps failing",
    "investigate why the queue is stuck",
    "can you fix this error? it's been failing all day",
    "F12 shows undefined is not an object",
    'File "main.py", line 42, in <module>',
    "diagnose the slow query",
]


def test_real_debug_signals_still_fire():
    """Hard signals (stack trace, HTTP code, error class) must still
    route to Mode D. Soft signals paired with debug-action verbs must
    also still fire."""
    missed = [p for p in REAL_DEBUG_FIRES if not is_debug_request(p)]
    assert not missed, (
        "Real debug signals NOT routing to Mode D — regression "
        f"would let real bug reports fall through to chat mode: {missed}"
    )


# ── Edge cases ───────────────────────────────────────────────────────

def test_word_error_alone_does_not_fire():
    """The exact founder report phrase must not fire."""
    assert is_debug_request("ye error de rha hai chek kro kya prob hai") is False


def test_word_error_with_fix_verb_fires():
    """Same phrase + an explicit 'fix this' verb SHOULD route to D."""
    assert is_debug_request("fix this error in the login form") is True


def test_status_code_alone_fires():
    """A bare HTTP status code in the prompt is enough on its own."""
    assert is_debug_request("getting 500 on production") is True


def test_traceback_marker_fires():
    """Python traceback line format is a hard signal."""
    assert is_debug_request('File "main.py", line 42, in foo') is True
