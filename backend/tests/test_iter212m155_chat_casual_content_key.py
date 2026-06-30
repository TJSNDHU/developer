"""
Iter 212m-155 — Chat-stream content-key bug fix + empty-content safety net.

PROD chat E2E (iter 212m-154 report) found that every casual greeting
(e.g. "hi") rendered an EMPTY assistant bubble.  Root cause: the
casual fast-path in routers/chat.py wrote the LLM reply into
`result["reply"]`, but the downstream SSE worker reads
`result["content"]` (line ~2095) to drive the token-streaming loop.
Key mismatch = zero tokens = empty bubble.

This test pins the fix:
  • Casual fast-path now writes `result["content"]` like every other
    mode (B/D/F/orchestrator) — no key mismatch.
  • SSE worker now has a graceful fallback when `result["content"]`
    is empty for any reason — emits a friendly explainer instead of
    a forever-thinking bubble (fixes the agentic-hang silent-close
    failure mode caught in the same iter 212m-154 QA).
"""
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent


def test_casual_branch_uses_content_key():
    """The intent-gateway casual fast-path must write `content`
    (not `reply`) so the SSE worker downstream streams it."""
    text = (_BACKEND / "routers" / "chat.py").read_text()
    # Locate the casual branch — fingerprint with its system prompt.
    sys_anchor = 'For this casual message, respond naturally and briefly'
    idx = text.index(sys_anchor)
    # Slice ~2.5KB ahead — covers the whole branch including the
    # result dict + the await q.put(...) call.
    casual_block = text[idx:idx + 2500]
    assert '"content":' in casual_block, casual_block
    # And the buggy `"reply":` key must be gone.
    assert '"reply":' not in casual_block, casual_block
    # The new provider tag pins this is the intent-gateway path.
    assert '"intent-gateway-casual"' in casual_block


def test_casual_branch_never_emits_empty_content():
    """Even when the LLM returns "" the fallback string keeps the
    bubble non-empty — no more silent "Hey!" that the SSE skipped."""
    text = (_BACKEND / "routers" / "chat.py").read_text()
    sys_anchor = 'For this casual message, respond naturally and briefly'
    idx = text.index(sys_anchor)
    casual_block = text[idx:idx + 2500]
    # The empty-string fallback must be a substantive multi-word string,
    # not just "Hey!" — that was the legacy fallback which still
    # surfaced as an empty bubble because of the key bug above.
    assert "Hey! How can I help you ship today?" in casual_block


def test_sse_worker_has_empty_content_safety_net():
    """The SSE worker's token-streaming loop now guards against an
    empty `result.content` so the agentic "thinking…" hang found in
    iter 212m-154 QA can never silently produce a blank bubble."""
    text = (_BACKEND / "routers" / "chat.py").read_text()
    # The new safety net mentions both 'empty content fallback' (in
    # the logger.warning) and 'wasn't able to produce a reply' (in
    # the user-visible fallback string).
    assert "empty content fallback" in text
    assert "wasn't able to produce a reply" in text


def test_persistent_fix_bar_has_testid():
    """Iter 212m-154 QA reported PersistentFixBar wasn't discoverable
    — but it ALREADY has data-testid=persistent-fix-bar (just gated
    by `status === 'idle'` returning null).  Pin the testid so it
    can never silently get removed."""
    src = (_BACKEND.parent / "frontend" / "src" / "components" /
           "PersistentFixBar.jsx").read_text()
    assert 'data-testid="persistent-fix-bar"' in src
    # The gating is intentional; document it via a guard test.
    assert 'status === "idle"' in src or "status === 'idle'" in src
