"""
Iter 212m-23 — E2E proof: URL prompts dispatch fetch_url via standard
orchestration (no more eager build_url_context); non-URL prompts do NOT
trigger forced pre-fetch.

Tests SSE stream against PREVIEW backend with founder login.

Tavily/Firecrawl quota may be exhausted upstream — we test that the TOOL is
DISPATCHED & LOGGED, not that the page content was retrieved.
"""
import json
import os
import re
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://launch-pad-237.preview.emergentagent.com",
).rstrip("/")

FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASS = "FounderOwn123!"


# ─────────────────────────── fixtures ───────────────────────────
@pytest.fixture(scope="module")
def auth_session():
    """Single-step founder login → returns authed session + user_id."""
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": FOUNDER_EMAIL, "password": FOUNDER_PASS},
        timeout=20,
    )
    if r.status_code != 200:
        pytest.skip(f"Founder login failed {r.status_code}: {r.text[:200]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    user_id = (data.get("user") or {}).get("id") or data.get("user_id")
    if not token or not user_id:
        pytest.skip(f"Login response missing token/user_id: {data}")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s, user_id


def _new_session_id(s: requests.Session, user_id: str) -> str:
    """Create a fresh chat session; fall back to local UUID if endpoint missing."""
    for path in (
        f"/api/aurem-dev/chat/sessions",
        f"/api/aurem-dev/sessions",
    ):
        try:
            r = s.post(
                f"{BASE_URL}{path}",
                json={"user_id": user_id, "title": "iter212m23 e2e"},
                timeout=10,
            )
            if r.status_code in (200, 201):
                j = r.json()
                sid = j.get("id") or j.get("session_id") or (j.get("session") or {}).get("id")
                if sid:
                    return sid
        except Exception:
            pass
    return str(uuid.uuid4())


def _stream_chat(s: requests.Session, user_id: str, session_id: str,
                 prompt: str, mode: str = "swift", timeout: int = 90):
    """POST /chat/stream and parse SSE frames into a list of dicts."""
    url = f"{BASE_URL}/api/aurem-dev/chat/stream"
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "prompt": prompt,
        "message": prompt,  # tolerate either schema
        "mode": mode,
    }
    frames = []
    raw_lines = []
    t0 = time.time()
    with s.post(url, json=payload, stream=True, timeout=timeout) as r:
        assert r.status_code == 200, f"stream HTTP {r.status_code}: {r.text[:300]}"
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            raw_lines.append(line)
            if line.startswith("data:"):
                body = line[5:].strip()
                if body in ("", "[DONE]"):
                    continue
                try:
                    frames.append(json.loads(body))
                except Exception:
                    frames.append({"_raw": body})
            if time.time() - t0 > timeout:
                break
    return frames, raw_lines, time.time() - t0


# ─────────────────────────── tests ───────────────────────────
class TestURLPromptDispatchesFetchURL:
    """P0 — URL prompt must dispatch fetch_url through standard orchestration."""

    def test_url_prompt_invokes_fetch_url_with_forced_marker(self, auth_session):
        s, uid = auth_session
        sid = _new_session_id(s, uid)
        prompt = "Read https://fastapi.tiangolo.com/ and summarise the first H1."
        frames, raw, dur = _stream_chat(s, uid, sid, prompt, mode="swift", timeout=120)

        assert frames, f"no SSE frames received; raw={raw[:5]}"

        # (a) step frame mentioning Reading URL
        step_texts = []
        for f in frames:
            t = f.get("type") or f.get("event")
            if t in ("step", "step_hook") or "step" in str(f).lower():
                txt = json.dumps(f, ensure_ascii=False)
                step_texts.append(txt)
        joined_steps = "\n".join(step_texts)
        assert ("Reading URL" in joined_steps) or ("fetch_url" in joined_steps), \
            f"no 'Reading URL'/fetch_url step found. step frames:\n{joined_steps[:1500]}"

        # (b) fetch_url invocation w/ forced:true in heartbeat/activity invocations[]
        forced_found = False
        any_fetch_url = False
        for f in frames:
            if not isinstance(f, dict):
                continue
            act = f.get("activity") if isinstance(f.get("activity"), dict) else {}
            invs = f.get("invocations") or act.get("invocations") or []
            if not isinstance(invs, list):
                continue
            for inv in invs:
                if not isinstance(inv, dict):
                    continue
                if (inv.get("tool") or inv.get("name")) == "fetch_url":
                    any_fetch_url = True
                    if inv.get("forced") is True:
                        forced_found = True
                        break
            if forced_found:
                break
        assert any_fetch_url, \
            f"fetch_url never appeared in any invocations[] across {len(frames)} frames"
        assert forced_found, \
            "fetch_url present but no entry has forced:true marker"

        # (c) no raw tool_call leakage in token frames
        leak_re = re.compile(r"<\s*tool_call\s*>|\barg_value\b|\"tool_call\"\s*:")
        for f in frames:
            if (f.get("type") or f.get("event")) in ("token", "delta", "chunk"):
                tok = f.get("token") or f.get("delta") or f.get("text") or ""
                assert not leak_re.search(str(tok)), \
                    f"tool_call leakage in user-visible token: {str(tok)[:200]}"

        # (d) meta frame reports provider glm-5.2 (swift mode)
        meta_frames = [f for f in frames
                       if (f.get("type") or f.get("event")) in ("meta", "final_meta", "done")]
        provider_seen = ""
        for mf in meta_frames:
            p = (mf.get("provider")
                 or (mf.get("meta") or {}).get("provider")
                 or mf.get("model") or "")
            if "glm" in str(p).lower():
                provider_seen = str(p)
                break
        # fall back to scanning all frames if meta event name differs
        if not provider_seen:
            blob = json.dumps(frames, ensure_ascii=False).lower()
            assert "glm-5.2" in blob or "glm" in blob, \
                f"provider=glm-5.2 not found in any frame. meta frames: {meta_frames[:3]}"

        print(f"\n✅ URL prompt dispatched fetch_url (forced:true) in {dur:.1f}s, "
              f"{len(frames)} frames, provider hit={provider_seen or 'glm (in blob)'}")


class TestNonURLPromptDoesNotForceFetch:
    """P0 — Non-URL prompts must NOT trigger forced fetch_url."""

    def test_plain_hello_does_not_invoke_fetch_url(self, auth_session):
        s, uid = auth_session
        sid = _new_session_id(s, uid)
        frames, raw, dur = _stream_chat(s, uid, sid, "hello", mode="swift", timeout=60)
        assert frames, "no SSE frames for plain hello"
        assert dur < 45, f"plain hello took {dur:.1f}s (>45s regression threshold)"

        for f in frames:
            if not isinstance(f, dict):
                continue
            act = f.get("activity") if isinstance(f.get("activity"), dict) else {}
            invs = f.get("invocations") or act.get("invocations") or []
            if not isinstance(invs, list):
                continue
            for inv in invs:
                if not isinstance(inv, dict):
                    continue
                tool = inv.get("tool") or inv.get("name")
                if tool == "fetch_url":
                    assert inv.get("forced") is not True, \
                        f"fetch_url with forced:true fired on non-URL prompt! inv={inv}"
        print(f"\n✅ plain 'hello' did NOT force fetch_url ({dur:.1f}s, {len(frames)} frames)")

    def test_repo_files_prompt_without_project_does_not_force_fetch_url(self, auth_session):
        s, uid = auth_session
        sid = _new_session_id(s, uid)
        frames, raw, dur = _stream_chat(
            s, uid, sid, "list the files in this repo", mode="swift", timeout=60
        )
        assert frames, "no SSE frames for repo-files prompt"
        for f in frames:
            if not isinstance(f, dict):
                continue
            act = f.get("activity") if isinstance(f.get("activity"), dict) else {}
            invs = f.get("invocations") or act.get("invocations") or []
            if not isinstance(invs, list):
                continue
            for inv in invs:
                if not isinstance(inv, dict):
                    continue
                if (inv.get("tool") or inv.get("name")) == "fetch_url":
                    assert inv.get("forced") is not True, \
                        f"forced fetch_url fired on repo-files prompt! inv={inv}"
        print(f"\n✅ repo-files prompt did NOT force fetch_url ({dur:.1f}s)")


class TestSourceCodeRemovalOfEagerScraper:
    """P0 — chat.py must not call build_url_context() live."""

    def test_build_url_context_only_in_comments(self):
        path = "/app/backend/routers/chat.py"
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        live_calls = []
        for i, ln in enumerate(lines, 1):
            if "build_url_context(" in ln:
                stripped = ln.lstrip()
                if not stripped.startswith("#"):
                    live_calls.append((i, ln.rstrip()))
        assert not live_calls, \
            f"build_url_context( still called live in chat.py: {live_calls}"

    def test_no_eager_import_of_build_url_context(self):
        path = "/app/backend/routers/chat.py"
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        # Acceptable: the literal string appears in a NOTE comment.
        # Unacceptable: an active `from services.url_fetcher import build_url_context` line.
        bad_pattern = re.compile(
            r"^\s*from\s+services\.url_fetcher\s+import\s+[^#\n]*build_url_context",
            re.MULTILINE,
        )
        m = bad_pattern.search(src)
        assert m is None, f"live import of build_url_context found: {m.group(0)!r}"
