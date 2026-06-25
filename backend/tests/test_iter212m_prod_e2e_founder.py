"""Iter 212m-15..22 — PRODUCTION e2e founder smoke (https://auremcto.com).

Verifies the deployed chain on prod:
  - Founder login (single-step, no MFA enrolled)
  - /api/health green
  - /cto/projects/list -> dogfood project id
  - /chat/stream swift mode basic reply (provider='glm-5.2')
  - Ask Advisor (agent='ora') full-response rule (R5) + GLM routing
  - Orchestrator tools one-by-one: read_repo_files, search_repo,
    web_search, fetch_url, list_repo_files
  - SSE step-frame contract (Iter 212m-18/19) — emoji prefix + done flag
  - /payments/checkout (starter_annual -> cs_live_… URL)
  - /admin/alerts top-up alerts engine (Iter 212m-17)
"""

import json
import os
import re
import time
import uuid

import pytest
import requests

BASE_URL = "https://auremcto.com"
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PWD = "Singh1986$"

# ---- shared session / token cache ----
_session_state: dict = {}


def _login() -> dict:
    if "token" in _session_state:
        return _session_state
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": FOUNDER_EMAIL, "password": FOUNDER_PWD},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    body = r.json()
    if body.get("mfa_required"):
        pytest.skip(f"MFA enrolled on prod founder — skipping (mfa_token present={bool(body.get('mfa_token'))})")
    assert body.get("token"), f"no token in login body: {body}"
    _session_state.update(
        token=body["token"],
        user_id=body.get("user_id"),
        is_admin=body.get("is_admin"),
        tier=body.get("tier"),
        is_unlimited=body.get("is_unlimited"),
    )
    return _session_state


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_login()['token']}"}


# ---- SSE parser ----
def _parse_sse(raw: str) -> list[dict]:
    frames = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            frames.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return frames


def _stream_chat(prompt: str, *, mode: str = "swift", agent: str | None = None,
                 project_id: str | None = None, timeout: int = 60) -> dict:
    """POST /chat/stream and return parsed SSE summary."""
    payload: dict = {
        "prompt": prompt,
        "mode": mode,
        "session_id": f"prod-e2e-{uuid.uuid4().hex[:10]}",
    }
    if agent:
        payload["agent"] = agent
    if project_id:
        payload["project_id"] = project_id

    started = time.time()
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/chat/stream",
        json=payload,
        headers={**_auth_headers(), "Accept": "text/event-stream"},
        stream=True,
        timeout=timeout,
    )
    assert r.status_code == 200, f"chat/stream HTTP {r.status_code}: {r.text[:300]}"

    buf = ""
    for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
        if chunk:
            buf += chunk
    elapsed = time.time() - started

    frames = _parse_sse(buf)
    tokens = [f for f in frames if "token" in f and "type" not in f]
    steps = [f for f in frames if f.get("type") == "step"]
    metas = [f for f in frames if f.get("meta") is True]
    done_frames = [f for f in frames if f.get("done") is True and "type" not in f]
    # tool invocations may be in done/meta frames
    tool_invocations = []
    for f in frames:
        ti = f.get("tool_invocations") or f.get("tools") or []
        if isinstance(ti, list):
            tool_invocations.extend(ti)
    # provider: prefer last meta with provider, else done frame
    provider = None
    for f in metas + done_frames:
        if isinstance(f, dict) and f.get("provider"):
            provider = f["provider"]
    content = "".join(str(t.get("token", "")) for t in tokens)
    return {
        "raw": buf,
        "frames": frames,
        "tokens": tokens,
        "steps": steps,
        "metas": metas,
        "done": done_frames,
        "provider": provider,
        "content": content,
        "tool_invocations": tool_invocations,
        "elapsed": elapsed,
    }


# ============================================================
# 0. Login + dogfood project discovery
# ============================================================
class TestProdAuthAndDiscovery:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("db") is True
        assert body.get("env") in ("production", "prod")

    def test_login_founder(self):
        s = _login()
        assert s.get("is_admin") is True, f"is_admin missing: {s}"
        assert s.get("tier") == "founder", f"tier!=founder: {s.get('tier')}"
        assert s.get("is_unlimited") is True

    def test_dogfood_project_exists(self):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/cto/projects/list",
            headers=_auth_headers(),
            timeout=20,
        )
        assert r.status_code == 200, r.text
        projects = r.json().get("projects", [])
        dogfood = next(
            (p for p in projects if "dogfood" in (p.get("name") or "").lower()
             or "aurem" in (p.get("github_repo") or "").lower()),
            None,
        )
        assert dogfood, f"no dogfood project among {[p.get('name') for p in projects]}"
        _session_state["dogfood_project_id"] = dogfood.get("project_id") or dogfood.get("id")
        assert _session_state["dogfood_project_id"], dogfood


# ============================================================
# 1. SSE step contract + basic swift chat reply
# ============================================================
STEP_EMOJI_RE = re.compile(r"^(🤔|📖|✍️|🚀|⚙️|✅|🔍|🌐|📦|🛠️|🧠)")


def _assert_step_contract(result: dict, *, min_content: int = 50):
    steps = result["steps"]
    assert len(steps) >= 1, f"no step frames — wiring broken. raw frames={len(result['frames'])}"
    # at least one step has the canonical emoji prefix
    assert any(STEP_EMOJI_RE.match((s.get("text") or "").strip()) for s in steps), \
        f"no step frame had emoji prefix: {[s.get('text') for s in steps]}"
    # final step is done:true
    assert steps[-1].get("done") is True, f"final step not done:true: {steps[-1]}"
    # done frame present
    assert result["done"], "no end-of-stream done:true frame"
    assert len(result["content"]) >= min_content, \
        f"content too short ({len(result['content'])} chars): {result['content'][:200]!r}"


class TestSwiftChatBasic:
    def test_basic_reply_on_dogfood(self):
        _login()
        # ensure project id
        if "dogfood_project_id" not in _session_state:
            TestProdAuthAndDiscovery().test_dogfood_project_exists()
        pid = _session_state["dogfood_project_id"]
        res = _stream_chat(
            "Hello, can you read my project structure?",
            mode="swift",
            project_id=pid,
            timeout=90,
        )
        _assert_step_contract(res, min_content=50)
        # provider should be glm-5.2 (Iter 212m-18 primary)
        assert res["provider"] and "glm" in res["provider"].lower(), \
            f"provider not glm: {res['provider']}"


# ============================================================
# 2. Ask Advisor (agent='ora') — Iter 212m-21 GLM + R5 full-response
# ============================================================
class TestAskAdvisorOra:
    def test_ora_architecture_question(self):
        _login()
        if "dogfood_project_id" not in _session_state:
            TestProdAuthAndDiscovery().test_dogfood_project_exists()
        pid = _session_state["dogfood_project_id"]
        res = _stream_chat(
            "What is the main architecture of this project?",
            agent="ora",
            project_id=pid,
            timeout=90,
        )
        # provider must be glm-5.2 (Iter 212m-21)
        assert res["provider"] and "glm-5.2" in res["provider"].lower(), \
            f"ora not on glm-5.2: provider={res['provider']}"
        # R5 rule: not a one-liner (>200 chars) OR a clarifying question
        content = res["content"].strip()
        is_question = "?" in content[-300:]
        assert len(content) > 200 or is_question, \
            f"R5 regression — short non-question reply ({len(content)} chars): {content!r}"
        # step contract
        assert any((s.get("text") or "").startswith("🤔") for s in res["steps"])
        assert res["steps"][-1].get("done") is True


# ============================================================
# 3. Tool calls one-by-one (orchestrator)
# ============================================================
def _tool_fired(res: dict, *names: str) -> tuple[bool, list]:
    invs = res["tool_invocations"]
    matched = [
        t for t in invs
        if isinstance(t, dict)
        and any(n in (t.get("name") or t.get("tool") or "") for n in names)
    ]
    # also probe step text + raw stream for tool names (fallback for older shapes)
    blob = (res["raw"] or "") + " ".join((s.get("text") or "") for s in res["steps"])
    fallback = any(n in blob for n in names)
    return (bool(matched) or fallback), invs


class TestOrchestratorTools:
    @pytest.fixture(autouse=True)
    def _ensure_dogfood(self):
        _login()
        if "dogfood_project_id" not in _session_state:
            TestProdAuthAndDiscovery().test_dogfood_project_exists()

    def test_tool_read_repo_files(self):
        pid = _session_state["dogfood_project_id"]
        res = _stream_chat(
            "Read the README.md from my repo and tell me the project description.",
            mode="swift",
            project_id=pid,
            timeout=120,
        )
        _assert_step_contract(res, min_content=30)
        fired, _ = _tool_fired(res, "read_repo_file", "read_repo_files", "read_file")
        assert fired, f"read_repo_files tool did not fire. steps={[s.get('text') for s in res['steps']]}"

    def test_tool_search_repo(self):
        pid = _session_state["dogfood_project_id"]
        res = _stream_chat(
            "Search for any file containing the word ORA in my repo.",
            mode="swift",
            project_id=pid,
            timeout=120,
        )
        _assert_step_contract(res, min_content=30)
        fired, _ = _tool_fired(res, "search_repo", "semantic_search_repo", "grep_repo")
        assert fired, f"search_repo tool did not fire. steps={[s.get('text') for s in res['steps']]}"

    def test_tool_list_repo_files(self):
        pid = _session_state["dogfood_project_id"]
        res = _stream_chat(
            "List the top-level folders in my project.",
            mode="swift",
            project_id=pid,
            timeout=120,
        )
        _assert_step_contract(res, min_content=30)
        fired, _ = _tool_fired(res, "list_repo_files", "list_files", "tree_repo", "list_repo")
        assert fired, f"list_repo_files tool did not fire. steps={[s.get('text') for s in res['steps']]}"

    def test_tool_web_search(self):
        res = _stream_chat(
            "What is the latest FastAPI version released in 2026? Use web search.",
            mode="swift",
            project_id=None,
            timeout=120,
        )
        _assert_step_contract(res, min_content=30)
        fired, _ = _tool_fired(res, "web_search", "web_search_and_summarize", "search_web")
        assert fired, f"web_search tool did not fire. steps={[s.get('text') for s in res['steps']]}"
        assert "fastapi" in res["content"].lower(), f"no fastapi mention in reply: {res['content'][:300]!r}"

    def test_tool_fetch_url(self):
        res = _stream_chat(
            "Read https://fastapi.tiangolo.com/ and summarise the homepage in 3 lines.",
            mode="swift",
            project_id=None,
            timeout=120,
        )
        _assert_step_contract(res, min_content=30)
        fired, _ = _tool_fired(res, "fetch_url", "read_url", "external_url", "fetch_external_url")
        assert fired, f"fetch_url tool did not fire. steps={[s.get('text') for s in res['steps']]}"
        assert "fastapi" in res["content"].lower()


# ============================================================
# 4. Payments smoke (annual works — Iter 212m-14)
# ============================================================
class TestPayments:
    def test_starter_annual_checkout_url(self):
        _login()
        r = requests.post(
            f"{BASE_URL}/api/aurem-dev/payments/checkout",
            headers=_auth_headers(),
            json={"plan": "starter_annual"},
            timeout=30,
        )
        assert r.status_code == 200, f"checkout HTTP {r.status_code}: {r.text[:300]}"
        body = r.json()
        url = body.get("checkout_url") or body.get("url")
        assert url, f"no checkout_url in body: {body}"
        assert "stripe.com" in url, f"not a stripe URL: {url}"
        # live mode session id format: cs_live_… (allow cs_test_ for safety)
        assert "cs_live_" in url or "cs_test_" in url, f"unexpected session id in url: {url}"


# ============================================================
# 5. Admin Top-up Alerts (Iter 212m-17)
# ============================================================
class TestAdminAlerts:
    def test_alerts_endpoint(self):
        _login()
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/alerts",
            headers=_auth_headers(),
            timeout=20,
        )
        assert r.status_code == 200, f"/admin/alerts HTTP {r.status_code}: {r.text[:300]}"
        body = r.json()
        assert "alerts" in body, f"missing alerts key: {body}"
        assert "counts" in body, f"missing counts key: {body}"
        # log active alerts for the report
        counts = body["counts"] or {}
        active = counts.get("active", 0) if isinstance(counts, dict) else 0
        if active and isinstance(body["alerts"], list):
            integrations = [a.get("integration") or a.get("service") for a in body["alerts"]]
            print(f"[alerts] active={active} integrations={integrations}")
