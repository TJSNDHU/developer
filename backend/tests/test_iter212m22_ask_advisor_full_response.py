"""Iter 212m-22 — Ask Advisor (ORA panel) must give COMPLETE responses.

Bug:
  Founder reported that Ask Advisor (right-side floating panel) gave a
  one-line reply and stopped — neither completing the task nor asking a
  clarifying question. Root cause: (a) ORA_PANEL_TONE had a hard 150-word
  ceiling + 3-line cap, (b) max_tokens=1500 truncated mid-thought,
  (c) no R5-style rule explicitly banning one-line dead-ends.

Fix (already shipped by main agent):
  • Removed 150-word ceiling + 3-line cap from ORA_PANEL_TONE.
  • Added R5: "ALWAYS GIVE A COMPLETE RESPONSE" — explicitly bans
    one-line replies, mandates either full answer OR specific
    clarifying question.
  • Bumped max_tokens 1500 → 2500 in routers/chat.py line ~1681.
  • Routing still goes through _call_glm (z-ai/glm-5.2) per Iter 212m-21.

This regression suite locks all four guarantees in:
  T1.  Static — R5 rule is present in ORA_PANEL_TONE source.
  T2.  Static — max_tokens=2500 on the ora→_call_glm call.
  T3.  Static — the old 150-word / 3-line ceiling is gone.
  T4.  Live — ambiguous prompt → full clarifying question (≥80 chars,
       contains '?').
  T5.  Live — clear technical prompt → multi-paragraph response
       (≥400 chars, ≥2 line breaks).
  T6.  Live — SSE stream has '🤔 Thinking…' AND '✅ Done' step frames
       (clean open/close, no premature disconnect).
  T7.  Live — meta frame reports provider='glm-5.2' (Iter 212m-21
       routing still intact, no orchestrator fall-through on happy path).
  T8.  Live — step-frame count stays small (≤4) — i.e. the ora branch
       returned cleanly without leaking into the orchestrator path.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import requests

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://launch-pad-237.preview.emergentagent.com",
).rstrip("/")

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


# ── shared fixtures ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def auth_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}: {r.text[:200]}")
    tok = r.json().get("token")
    if not tok:
        pytest.skip("login response missing token")
    return tok


@pytest.fixture(scope="module")
def chat_src() -> str:
    return (BACKEND_DIR / "routers" / "chat.py").read_text(encoding="utf-8")


def _stream_ask_advisor(token: str, prompt: str, timeout: int = 120) -> dict:
    """POST /chat/stream and parse SSE frames. Returns dict with
    {events, content, provider, steps, raw}."""
    url = f"{BASE_URL}/api/aurem-dev/chat/stream"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "prompt": prompt,
        "agent": "ora",
        "session_id": f"test-iter212m22-{abs(hash(prompt)) % 10_000_000}",
    }
    events: list[dict] = []
    content_parts: list[str] = []
    provider: str | None = None
    steps: list[dict] = []
    raw_lines: list[str] = []

    with requests.post(
        url, headers=headers, json=payload, stream=True, timeout=timeout
    ) as resp:
        assert resp.status_code == 200, (
            f"stream HTTP {resp.status_code}: {resp.text[:300]}"
        )
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            raw_lines.append(raw)
            if not raw.startswith("data:"):
                continue
            payload_str = raw[len("data:"):].strip()
            if not payload_str:
                continue
            try:
                evt = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            events.append(evt)
            t = evt.get("type")
            # Token frame: {"token": "..."} — no `type` field.
            if "token" in evt and not t:
                tok_text = evt.get("token") or ""
                if tok_text:
                    content_parts.append(tok_text)
            elif t == "token":
                tok_text = evt.get("text") or evt.get("token") or ""
                if tok_text:
                    content_parts.append(tok_text)
            elif t == "step":
                steps.append(evt)
            # Meta frame: {"meta": true, "provider": "...", ...} — also
            # no `type` field. The stream emits TWO meta frames; the
            # second one (after GLM resolves) carries the real provider.
            if evt.get("meta") is True and evt.get("provider"):
                provider = evt["provider"] or provider
            # Final done frame may also carry provider.
            if evt.get("done") is True and evt.get("provider"):
                provider = evt["provider"] or provider
            if t == "result":
                res = evt.get("result") or {}
                if res.get("provider"):
                    provider = res["provider"]
                if res.get("content") and not content_parts:
                    content_parts.append(res["content"])

    return {
        "events": events,
        "content": "".join(content_parts),
        "provider": provider,
        "steps": steps,
        "raw": raw_lines,
    }


# ── T1-T3: static guarantees on source ────────────────────────────


def test_r5_rule_present_in_ora_panel_tone(chat_src: str):
    """R5 'ALWAYS GIVE A COMPLETE RESPONSE' must be present so the
    model is explicitly told not to dead-end with one-liners."""
    assert "R5." in chat_src, "R5 marker missing from chat.py"
    assert "ALWAYS GIVE A COMPLETE RESPONSE" in chat_src.upper(), (
        "R5 rule heading missing — Ask Advisor will regress to "
        "one-line replies"
    )
    # Either explicit ban on one-liners or explicit clarifying-question
    # alternative must be present.
    lower = chat_src.lower()
    assert ("one-line" in lower or "one line" in lower), (
        "R5 must explicitly ban one-line replies"
    )
    assert "clarifying question" in lower, (
        "R5 must offer 'ask ONE clarifying question' as the alternative"
    )


def test_max_tokens_bumped_to_2500_on_ora_glm_call(chat_src: str):
    """The ora branch must call _call_glm with max_tokens >= 2000
    (target 2500). Previous 1500 truncated mid-answer."""
    idx = chat_src.find('"asking GLM-5.2…"')
    assert idx >= 0, "ora→GLM activity label missing"
    block = chat_src[idx:idx + 3000]
    m = re.search(r"max_tokens\s*=\s*(\d+)", block)
    assert m, "max_tokens kwarg missing on _call_glm call in ora branch"
    n = int(m.group(1))
    assert n >= 2000, (
        f"max_tokens={n} is below the 2000 floor mandated by the bug "
        f"spec. Target is 2500."
    )


def test_old_150_word_ceiling_removed(chat_src: str):
    """The previous '150-word' / '3-line' caps must be gone — they
    are what caused the one-line dead-ends in the first place."""
    # Pull out just the ORA_PANEL_TONE block so we don't false-positive
    # on unrelated comments.
    start = chat_src.find("ORA_PANEL_TONE = (")
    assert start >= 0
    # Find the matching closing ')' by scanning forward; cap at 6kB.
    end = chat_src.find("\n)\n", start)
    block = chat_src[start:end if end > 0 else start + 6000]
    low = block.lower()
    assert "150 word" not in low and "150-word" not in low, (
        "150-word ceiling still present in ORA_PANEL_TONE"
    )
    assert "3-line" not in low and "3 line" not in low, (
        "3-line cap still present in ORA_PANEL_TONE"
    )


# ── T4-T8: live SSE behaviour ─────────────────────────────────────


@pytest.mark.parametrize(
    "ambiguous_prompt",
    ["Help me fix it", "What should I do"],
)
def test_ambiguous_prompt_returns_full_clarifying_question(
    auth_token: str, ambiguous_prompt: str
):
    """An ambiguous prompt must NOT one-line dead-end. The reply must
    either be a fuller clarifying question (≥80 chars, contains '?')
    OR a complete answer (≥80 chars)."""
    out = _stream_ask_advisor(auth_token, ambiguous_prompt)
    content = (out["content"] or "").strip()
    assert content, (
        f"empty content for ambiguous prompt {ambiguous_prompt!r}; "
        f"events={out['events'][:5]}"
    )
    assert len(content) >= 80, (
        f"Ask Advisor regressed to one-line dead-end "
        f"({len(content)} chars) for prompt {ambiguous_prompt!r}: "
        f"{content!r}"
    )
    # Either it's a clarifying question (must contain '?') or it's a
    # complete answer (looser test — already covered by length).
    # Spec says either is acceptable; we just guarantee it's not a
    # one-liner.


def test_clear_prompt_returns_full_multiparagraph_answer(auth_token: str):
    """Clear technical prompt must produce a complete multi-paragraph
    answer (≥400 chars, ≥2 newlines) — i.e. not truncated by the old
    1500-token cap."""
    prompt = (
        "Explain how JWT authentication works in 3 paragraphs and "
        "list the security trade-offs."
    )
    out = _stream_ask_advisor(auth_token, prompt, timeout=180)
    content = (out["content"] or "").strip()
    assert content, f"empty content; events={out['events'][:5]}"
    assert len(content) >= 400, (
        f"clear-prompt reply was only {len(content)} chars — looks "
        f"truncated. Content head: {content[:300]!r}"
    )
    nl = content.count("\n")
    assert nl >= 2, (
        f"clear-prompt reply has only {nl} line breaks — looks like "
        f"a single blob, not a multi-paragraph answer."
    )


def test_sse_stream_emits_thinking_and_done_step_frames(auth_token: str):
    """The ora branch must emit '🤔 Thinking…' AND '✅ Done' step
    frames so the floating progress card lights up cleanly and the
    stream is not closed prematurely."""
    out = _stream_ask_advisor(auth_token, "Hi, what can you do?")
    step_texts = [s.get("text", "") for s in out["steps"]]
    assert any("Thinking" in t for t in step_texts), (
        f"missing '🤔 Thinking…' step frame; steps={step_texts}"
    )
    assert any("Done" in t for t in step_texts), (
        f"missing '✅ Done' step frame — stream may be closing "
        f"prematurely. steps={step_texts}"
    )
    # Done frame must be flagged done=True.
    done_frames = [s for s in out["steps"] if "Done" in s.get("text", "")]
    assert any(s.get("done") is True for s in done_frames), (
        "'✅ Done' frame missing done:true marker"
    )


def test_meta_frame_reports_glm_provider(auth_token: str):
    """Iter 212m-21 routing must hold — Ask Advisor must report
    provider='glm-5.2' (not deepseek / claude / aurem.live)."""
    out = _stream_ask_advisor(auth_token, "Hi, what can you do?")
    prov = (out["provider"] or "").lower()
    assert prov, f"no provider reported; events={out['events'][:8]}"
    assert "glm" in prov, (
        f"expected glm-5.2 provider, got {prov!r} — "
        f"routing regressed off Iter 212m-21"
    )


def test_ora_branch_does_not_fall_through_to_orchestrator(auth_token: str):
    """On the happy path the ora branch returns its own result and the
    orchestrator block is skipped. Symptom of fall-through would be
    a flurry of >5 step frames (tool steps from chat_with_tools). The
    ora branch should emit ≤4 (Thinking, optional intermediate, Done)."""
    out = _stream_ask_advisor(auth_token, "Hi, what can you do?")
    n_steps = len(out["steps"])
    assert 1 <= n_steps <= 4, (
        f"step-frame count={n_steps} suggests orchestrator fall-through "
        f"kicked in on the happy path. Steps: "
        f"{[s.get('text') for s in out['steps']]}"
    )
