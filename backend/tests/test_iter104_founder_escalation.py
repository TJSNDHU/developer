"""Iter 104 — escalation memory for repeated founder-contact asks.

Threshold contract (user-specified):
  1–2 asks: generic answer (no escalation note injected)
  3rd ask: inject hint → suggest emailing ora@aurem.live
  4–5 asks: inject hint → ask user to wait for support reply
  6+ asks: inject hint → share founder's LinkedIn as last resort
"""
from services.orchestrator import (
    _count_founder_asks,
    _founder_escalation_note,
    _FOUNDER_ASK_RX,
)


# ── Regex coverage ────────────────────────────────────────────────
def test_regex_catches_common_phrasings():
    samples = [
        "who is the founder?",
        "who built this app",
        "who made aurem cto",
        "I want to contact the founder",
        "how do I reach out to the team",
        "what's the founder's email",
        "who runs this company",
        "talk to the CEO please",
        "who created Aurem?",
        "can you share founder's LinkedIn",
        "company behind this product",
    ]
    for s in samples:
        assert _FOUNDER_ASK_RX.search(s), f"missed: {s!r}"


def test_regex_does_not_misfire_on_normal_chat():
    misfires = [
        "fix the login bug",
        "add a /health endpoint",
        "thanks!",
        "how does SSE work",
        "what's the best db",
        "review backend/auth.py for me",
    ]
    for s in misfires:
        assert not _FOUNDER_ASK_RX.search(s), f"false positive: {s!r}"


# ── Counting in session history ─────────────────────────────────
def _hx(*msgs):
    """Build history_lines as the orchestrator format expects."""
    return [f"[USER] {m}" if i % 2 == 0 else f"[ASSISTANT] {m}"
            for i, m in enumerate(msgs)]


def test_count_returns_zero_when_current_message_unrelated():
    hist = _hx("who is the founder", "I can't share that",
               "who built aurem", "Built by the team")
    # Even with 2 prior asks, current message is unrelated → 0
    assert _count_founder_asks(hist, "fix my login") == 0


def test_count_increments_with_prior_user_asks():
    hist = _hx("who is the founder", "I can't share that",
               "who built aurem", "Built by the AUREM team")
    # Current is the 3rd founder-ask
    assert _count_founder_asks(hist, "how do I contact the founder") == 3


def test_count_ignores_assistant_lines_mentioning_founder():
    """Assistant might say 'the founder' in its reply — must not count
    that toward the user's ask total."""
    hist = [
        "[USER] hi",
        "[ASSISTANT] hello — AUREM CTO is built by the founder team",
        "[USER] cool",
        "[ASSISTANT] anything else?",
    ]
    # First user mention of founder
    assert _count_founder_asks(hist, "who is the founder") == 1


# ── Escalation note thresholds ──────────────────────────────────
def test_no_note_for_1_or_2_asks():
    assert _founder_escalation_note(0) == ""
    assert _founder_escalation_note(1) == ""
    assert _founder_escalation_note(2) == ""


def test_3rd_ask_suggests_email_only():
    note = _founder_escalation_note(3)
    assert "ora@aurem.live" in note
    assert "FOUNDER ASK #3" in note
    # The LinkedIn URL must NOT appear yet (the word may appear in
    # a "do not share LinkedIn yet" instruction, which is fine).
    assert "linkedin.com/in/tejinder-sandhu" not in note


def test_4th_and_5th_ask_say_wait_for_reply():
    for c in (4, 5):
        note = _founder_escalation_note(c)
        assert "ora@aurem.live" in note
        assert "1 working day" in note or "wait" in note.lower() or "give the team" in note.lower()
        # Still no LinkedIn URL at this stage
        assert "linkedin.com/in/tejinder-sandhu" not in note


def test_6th_plus_ask_shares_linkedin():
    for c in (6, 7, 12):
        note = _founder_escalation_note(c)
        assert "linkedin.com/in/tejinder-sandhu" in note
        assert "ora@aurem.live" in note  # email still mentioned as primary
        # no fabricated bio
        assert "Shubham" not in note
        assert "India" not in note


# ── End-to-end mini scenario ─────────────────────────────────────
def test_full_escalation_flow():
    """Simulate a real session where the user pesters."""
    hist = []
    # Turn 1
    c = _count_founder_asks(hist, "who is the founder?")
    assert c == 1 and _founder_escalation_note(c) == ""
    hist += ["[USER] who is the founder?", "[ASSISTANT] built by the team"]
    # Turn 2
    c = _count_founder_asks(hist, "no really, who built this?")
    assert c == 2 and _founder_escalation_note(c) == ""
    hist += ["[USER] no really, who built this?", "[ASSISTANT] team"]
    # Turn 3 → email
    c = _count_founder_asks(hist, "i need to contact the founder")
    note = _founder_escalation_note(c)
    assert c == 3 and "ora@aurem.live" in note and "linkedin.com/in/tejinder-sandhu" not in note
    hist += ["[USER] i need to contact the founder", "[ASSISTANT] email ora"]
    # Turn 4 → wait
    c = _count_founder_asks(hist, "i need the founder again")
    note = _founder_escalation_note(c)
    assert c == 4 and "linkedin.com/in/tejinder-sandhu" not in note
    hist += ["[USER] i need the founder again", "[ASSISTANT] please wait"]
    # Turn 5 → still wait
    c = _count_founder_asks(hist, "founder contact please")
    note = _founder_escalation_note(c)
    assert c == 5 and "linkedin.com/in/tejinder-sandhu" not in note
    hist += ["[USER] founder contact please", "[ASSISTANT] please wait"]
    # Turn 6 → LinkedIn
    c = _count_founder_asks(hist, "give me the founder's linkedin")
    note = _founder_escalation_note(c)
    assert c == 6 and "linkedin.com/in/tejinder-sandhu" in note
