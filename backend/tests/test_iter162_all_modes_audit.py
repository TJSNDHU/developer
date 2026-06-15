"""
test_iter162_all_modes_audit.py — pre-launch full classifier sweep.

Mode contract (`routers/chat.classify_intent`):

  A — Chat / casual / greeting        (cheap)
  B — Advice / decision               (5-adviser council on stuck calls)
  C — Code Ship                       (read repo → write → commit)
  D — Debug                           (Mode D LLM with file context)
  E — Audit                           (full repo scan, expensive)
  F — Engage / Market                 (positioning, copy, GTM)

False positives are EXPENSIVE — Mode E spins up a full repo scan,
Mode D burns LLM tokens with file inspection, Mode F sends an unrelated
positioning prompt back. False NEGATIVES are also bad — a real debug
ask going to Mode A loses the file-inspection context.

This sweep enumerates real founder-grade prompts across both axes so
launch-day regressions get caught by `pytest -q` before merge.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers.chat import classify_intent   # noqa: E402

# ── Casual / chat / greeting must land in Mode A ─────────────────────

CASUAL_A = [
    "hello",
    "hi how are you",
    "what does this file do?",
    "explain how the auth flow works",
    "tell me about the project",
    "good morning",
    "kuch nahi bas hi bola",
    "ye error de rha hai chek kro kya prob hai",   # iter 162 founder report
    "what's wrong with this approach",              # casual, not audit
    "the tech debt is killing us",                  # venting, not audit
    "persona is wrong here",                        # not engage
    "icp is enterprise devs",                       # not engage
    "the page is not working",                      # soft only, no action verb
]


# ── Real debug requests must land in Mode D ──────────────────────────

REAL_DEBUG_D = [
    "TypeError: Cannot read properties of undefined",
    "I'm getting a 500 error on /api/projects",
    "fix this 422 from /api/users",
    'File "main.py", line 42, in foo',
    "debug the login flow",
    "diagnose the slow query",
    "investigate why the queue is stuck",
    "can you fix the broken pagination?",
    "F12 shows undefined is not an object",
    "ECONNREFUSED when calling the gateway",
]


# ── Real audit requests must land in Mode E ──────────────────────────

REAL_AUDIT_E = [
    "audit my repo for secrets",
    "review my codebase",
    "security audit",
    "scan the codebase for vulnerabilities",
    "find all bugs in the project",
    "audit the whole project",
    "owasp scan",
    "review my entire codebase",
]


# ── Real engage / market requests must land in Mode F ────────────────

REAL_ENGAGE_F = [
    "write a launch tweet about Maxx mode",
    "how should we position against Cursor",
    "competitor analysis vs Devin",
    "what's the gtm plan",
    "define our ICP",
    "describe our ideal customer persona",
    "write a cold email for outreach",
    "how do we differentiate from Copilot",
    "what's our moat",
]


# ── Code ship requests must land in Mode C ───────────────────────────

REAL_CODE_C = [
    "add a dark-mode toggle to my app",
    "ship this fix to github",
    "commit the migration to main",
    "deploy to vercel",
    "build a /api/health endpoint in my repo",
]


def _assert_mode(samples, want, label):
    bad = [(p, classify_intent(p, None)) for p in samples
           if classify_intent(p, None) != want]
    assert not bad, f"\n{label} → {want} regressions:\n" + "\n".join(
        f"  GOT {got!r}  WANT {want!r}  ←  {p!r}" for p, got in bad
    )


def test_casual_lands_in_A():
    _assert_mode(CASUAL_A, "A", "Casual")


def test_real_debug_lands_in_D():
    _assert_mode(REAL_DEBUG_D, "D", "Debug")


def test_real_audit_lands_in_E():
    _assert_mode(REAL_AUDIT_E, "E", "Audit")


def test_real_engage_lands_in_F():
    _assert_mode(REAL_ENGAGE_F, "F", "Engage")


def test_real_code_lands_in_C():
    _assert_mode(REAL_CODE_C, "C", "Code")
