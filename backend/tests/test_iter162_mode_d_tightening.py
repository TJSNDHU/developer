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
    # Iter 212f — "debug the login flow" / "investigate why the queue
    # is stuck" / "diagnose the slow query" no longer fire Mode D on
    # their own. They now route to Mode C (agentic) where the LLM can
    # actually read code, instead of burning a Mode-D call that bails
    # with "insufficient signal to diagnose". They're tested separately
    # in REAL_AGENT_FIRES below.
    "what's wrong with the build — keeps failing",
    "can you fix this error? it's been failing all day",
    "F12 shows undefined is not an object",
    'File "main.py", line 42, in <module>',
]

# Iter 212f — debug verbs paired with a *target* (repo / file / flow /
# auth / api) now route to Mode C, not D. Mode C gets repo context and
# can call tools; Mode D doesn't. We use the route-level classifier for
# this assertion since `is_debug_request` deliberately ignores them.
REAL_AGENT_FIRES = [
    # `debug <target>` → Mode C (agentic)
    ("debug the login flow",   "C"),
    ("debug full repo",        "C"),
    ("review the auth module", "C"),
    # `scan` / `audit` → Mode E (auditor is also agentic, runs the
    # mode-E auditor which reads code). Either C or E is acceptable
    # for the user; D is the only bad outcome.
    ("scan the codebase",      "E"),
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


def test_agentic_debug_requests_route_to_mode_c():
    """Iter 212f — `debug <target>` / `investigate <target>` etc. now
    route to Mode C/E (agentic) instead of Mode D (which used to bail
    with "insufficient signal"). Mode C/E both get repo context."""
    from routers.chat import classify_intent
    misrouted = []
    for msg, expected in REAL_AGENT_FIRES:
        m = classify_intent(msg, None)
        if m != expected:
            misrouted.append((msg, expected, m))
    assert not misrouted, (
        "Agentic debug verbs misrouted: "
        f"{misrouted} — must route to the agentic mode noted, never D."
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
