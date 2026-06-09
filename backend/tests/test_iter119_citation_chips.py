"""
tests/test_iter119_citation_chips.py

Iter 119 — Citation chip support.

Covers `_extract_web_sources()` and `_dedupe_sources()` in
`services.orchestrator`. End-to-end propagation into the chat SSE
done payload is exercised in higher-level chat tests; here we only
lock in the helper semantics.
"""

from services.orchestrator import _extract_web_sources, _dedupe_sources


# ──────────────────────────────────────────────────────────────────────
# _extract_web_sources
# ──────────────────────────────────────────────────────────────────────

def test_extract_web_search_returns_url_title_pairs():
    res = {
        "ok": True,
        "results": [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.com/b", "title": "B"},
        ],
    }
    out = _extract_web_sources("web_search", {"query": "x"}, res)
    assert out == [
        {"url": "https://example.com/a", "title": "A", "tool": "web_search"},
        {"url": "https://example.com/b", "title": "B", "tool": "web_search"},
    ]


def test_extract_web_search_summarize_pulls_from_citations():
    res = {
        "ok": True,
        "citations": [
            {"url": "https://docs.python.org/3/", "title": "Python Docs"},
        ],
    }
    out = _extract_web_sources("web_search_and_summarize", {"query": "py"}, res)
    assert len(out) == 1
    assert out[0]["url"] == "https://docs.python.org/3/"
    assert out[0]["tool"] == "web_search_and_summarize"


def test_extract_firecrawl_scrape_uses_args_url():
    out = _extract_web_sources(
        "firecrawl_scrape",
        {"url": "https://news.ycombinator.com/"},
        {"ok": True, "markdown": "..."},
    )
    assert out == [{"url": "https://news.ycombinator.com/", "title": "", "tool": "firecrawl_scrape"}]


def test_extract_skips_non_web_tools():
    out = _extract_web_sources(
        "read_repo_file",
        {"path": "main.py"},
        {"ok": True, "content": "..."},
    )
    assert out == []


def test_extract_skips_failed_results():
    out = _extract_web_sources(
        "web_search",
        {"query": "x"},
        {"ok": False, "error": "Tavily rate-limited"},
    )
    assert out == []


def test_extract_rejects_non_http_urls():
    res = {
        "ok": True,
        "results": [
            {"url": "javascript:alert(1)", "title": "evil"},
            {"url": "file:///etc/passwd", "title": "evil2"},
            {"url": "https://safe.example/", "title": "safe"},
        ],
    }
    out = _extract_web_sources("web_search", {"query": "x"}, res)
    assert [s["url"] for s in out] == ["https://safe.example/"]


def test_extract_caps_at_5_per_call():
    res = {
        "ok": True,
        "results": [
            {"url": f"https://e{i}.example/", "title": f"r{i}"} for i in range(10)
        ],
    }
    out = _extract_web_sources("web_search", {"query": "x"}, res)
    assert len(out) == 5


def test_extract_truncates_long_titles():
    res = {
        "ok": True,
        "results": [
            {"url": "https://x.example/", "title": "T" * 500},
        ],
    }
    out = _extract_web_sources("web_search", {"query": "x"}, res)
    assert len(out[0]["title"]) <= 140


# ──────────────────────────────────────────────────────────────────────
# _dedupe_sources
# ──────────────────────────────────────────────────────────────────────

def test_dedupe_preserves_first_seen_order_and_caps_at_8():
    raw = [
        {"url": f"https://e{i}.example/", "title": f"t{i}", "tool": "web_search"}
        for i in range(12)
    ]
    # add 3 duplicates
    raw.append({"url": "https://e0.example/", "title": "dup", "tool": "web_search"})
    raw.append({"url": "https://e1.example/", "title": "dup", "tool": "web_search"})
    out = _dedupe_sources(raw)
    assert len(out) == 8
    assert out[0]["url"] == "https://e0.example/"
    # Duplicates kept first-seen title
    assert out[0]["title"] == "t0"


def test_dedupe_drops_entries_with_no_url():
    out = _dedupe_sources([
        {"url": "https://a.example/", "title": "a", "tool": "web_search"},
        {"url": "", "title": "no-url", "tool": "web_search"},
        {"title": "missing-url-key", "tool": "web_search"},
    ])
    assert len(out) == 1
    assert out[0]["url"] == "https://a.example/"
