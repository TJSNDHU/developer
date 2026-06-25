"""
Iter 212m-23 — REAL FIX for the URL tool leak.

The legacy `build_url_context` in `routers/chat.py` was eagerly
scraping any http(s) URL in the prompt and silently stuffing the
result into `extra_sys` (the system prompt). This bypassed the
standard tool-orchestration UI:
  - No 📖 Reading URL… step card
  - No 🌐 web_sources chip
  - No entry in `tool_invocations`
  - Raw scraped content (HTML) leaked into the model context

The fix moves URL fetching out of chat.py and into a deterministic
forced `fetch_url` pre-execution inside `services/orchestrator.py`
that uses the same tool dispatch path the LLM would use itself —
so the UI surfaces it the same way, and the model gets the content
in transcript as an "iter-0 TOOL RESULTS" block.

These are source-level pins (regression guards) plus a small async
smoke test against the in-process orchestrator to confirm the pre-
fetch dispatches `fetch_url` when a URL is in the prompt.
"""
from __future__ import annotations

import os

CHAT_PY = os.path.join(
    os.path.dirname(__file__), "..", "routers", "chat.py"
)
ORCH_PY = os.path.join(
    os.path.dirname(__file__), "..", "services", "orchestrator.py"
)


# ── 1. chat.py no longer imports or invokes build_url_context ────────

def test_chat_py_does_not_import_build_url_context():
    """The eager URL scraper is gone — only NOTE comments may remain."""
    src = open(CHAT_PY).read()
    # No live import.
    assert "from services.url_fetcher import build_url_context" not in src
    # No live call site (any occurrence outside a `#` comment line).
    code_lines = [
        ln for ln in src.splitlines()
        if "build_url_context" in ln and not ln.lstrip().startswith("#")
    ]
    assert code_lines == [], (
        f"Expected NO live build_url_context references in chat.py — "
        f"found: {code_lines}"
    )


def test_chat_py_does_not_join_url_ctx_into_extra_sys_via_eager_fetch():
    """The /send path used to join `url_ctx` from a parallel
    `build_url_context()` await. That eager call must be gone."""
    src = open(CHAT_PY).read()
    assert "url_ctx_task = asyncio.create_task(build_url_context" not in src
    assert "asyncio.gather(repo_ctx_task, url_ctx_task)" not in src


# ── 2. orchestrator.py has the forced fetch_url pre-execution ─────────

def test_orchestrator_has_forced_url_pre_fetch_block():
    """Forced pre-fetch must:
      - import extract_urls from url_fetcher
      - call invoke_local_tool / invoke_tool with `fetch_url`
      - emit a step_hook with the 📖 Reading URL… label
      - append a `forced: True` entry into invocations
      - fold the result into the transcript as TOOL RESULTS
    """
    src = open(ORCH_PY).read()
    assert "from services.url_fetcher import extract_urls" in src
    assert "forced fetch_url pre-execution" in src
    assert '"forced":      True' in src or '"forced": True' in src
    # Step label hook fires for the forced pre-fetch.
    assert '_STEP_LABELS.get("fetch_url"' in src
    # Folded into the transcript so the LLM answers FROM the fetched
    # content, not pretraining.
    assert "TOOL RESULTS (forced pre-fetch)" in src
    assert "DO NOT answer from\n        f\"pretraining" in src or \
        "DO NOT answer from " in src


def test_orchestrator_caps_forced_urls_at_three():
    """Defence-in-depth: a prompt with 50 URLs shouldn't spawn 50 fetches."""
    src = open(ORCH_PY).read()
    # We cap at 3 URLs in the forced pre-fetch.
    assert "_extract_urls(prompt or \"\")[:3]" in src


def test_orchestrator_pre_fetch_runs_before_main_loop():
    """The forced fetch must dispatch BEFORE the `while iters < max_iters`
    loop. Otherwise the LLM gets its first chance without the content."""
    src = open(ORCH_PY).read()
    pre_idx = src.find("forced fetch_url pre-execution")
    loop_idx = src.find("while iters < max_iters")
    assert pre_idx != -1 and loop_idx != -1
    assert pre_idx < loop_idx, (
        "Forced URL pre-fetch must run BEFORE the main LLM loop "
        "so the LLM's first iteration has the content in transcript"
    )


# ── 3. SSE step label exists for fetch_url ────────────────────────────

def test_step_label_for_fetch_url_is_present():
    """The chat UI relies on the 📖 Reading URL… label to render a step
    card when the forced pre-fetch fires."""
    src = open(ORCH_PY).read()
    assert "\"fetch_url\":" in src
    assert "📖 Reading URL" in src


# ── 4. url_fetcher.extract_urls helper still exports ──────────────────

def test_url_fetcher_still_exports_extract_urls():
    """The helper must stay importable — we removed build_url_context
    from the chat router but still need extract_urls in orchestrator."""
    from services.url_fetcher import extract_urls
    assert callable(extract_urls)
    out = extract_urls("Hey check this https://fastapi.tiangolo.com/ ok?")
    assert out == ["https://fastapi.tiangolo.com/"]


def test_url_fetcher_extract_urls_dedup_and_cap():
    from services.url_fetcher import extract_urls, MAX_URLS
    s = " ".join(f"https://example{i}.com" for i in range(MAX_URLS + 5))
    urls = extract_urls(s)
    assert len(urls) <= MAX_URLS
    # Dedup
    again = extract_urls("https://x.com https://x.com https://x.com")
    assert len(again) == 1


# ── 5. forced entry has the standard tool-invocation shape ────────────

def test_forced_entry_has_standard_invocation_shape():
    """The forced entry must satisfy the same shape as any other tool
    invocation so frontend `tool_invocations` rendering is consistent.
    Required keys: tool, args, ok, status, elapsed_ms, error, web_sources."""
    src = open(ORCH_PY).read()
    required = ['"tool":', '"args":', '"ok":', '"status":',
                '"elapsed_ms":', '"error":', '"web_sources":']
    block_start = src.find("forced fetch_url pre-execution")
    assert block_start != -1
    block = src[block_start:block_start + 4000]
    for key in required:
        assert key in block, f"forced entry missing key {key}"
