"""
tests/test_iter267_url_fetch_retry.py — ORA Research Parity addendum.

GAP 1: generic (non-GitHub) URL fetch — readable-text extraction,
robots.txt respect, explicit per-URL failure blocks.
GAP 2: retry-with-reformulation on thin results for ALL search tools.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import services.ora_chat.deep_research as dr
from services.ora_chat.deep_research import (
    extract_fetchable_urls, has_fetchable_url, _extract_readable_text,
    _fetch_urls, _is_thin_result, _with_empty_retry,
)

_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    def factory(**kw):
        return _RealAsyncClient(transport=httpx.MockTransport(handler),
                                timeout=kw.get("timeout"))
    return factory


class TestUrlExtraction:
    def test_skips_github_urls(self):
        q = "dekho https://github.com/foo/bar aur https://example.com/post padho"
        assert extract_fetchable_urls(q) == ["https://example.com/post"]

    def test_caps_at_two_and_dedupes(self):
        q = ("https://a.com/1 https://a.com/1 https://b.com/2 "
             "https://c.com/3")
        assert extract_fetchable_urls(q) == ["https://a.com/1",
                                              "https://b.com/2"]

    def test_strips_trailing_punctuation(self):
        assert extract_fetchable_urls("padho https://x.com/article.") \
            == ["https://x.com/article"]

    def test_has_fetchable_url(self):
        assert has_fetchable_url("ye https://news.site/a dekho") is True
        assert has_fetchable_url("https://github.com/o/r dekho") is False
        assert has_fetchable_url("koi url nahi hai") is False


class TestReadableExtraction:
    def test_strips_scripts_nav_ads(self):
        html = """<html><head><script>evil()</script></head><body>
        <nav>Menu Home About</nav>
        <article><h1>Real Title</h1><p>Actual article body text here.</p></article>
        <footer>copyright junk</footer></body></html>"""
        text = _extract_readable_text(html)
        assert "Real Title" in text
        assert "Actual article body" in text
        assert "evil()" not in text
        assert "Menu Home" not in text
        assert "copyright junk" not in text


class TestFetchUrls:
    @pytest.mark.asyncio
    async def test_success_html_page(self, monkeypatch):
        def handler(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, headers={"content-type": "text/html"},
                                   html="<html><body><article>page ka content yahan hai bhai</article></body></html>")
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_urls("padho https://site-a1.test/post kya likha hai")
        assert out["ok"] is True
        assert len(out["fetched"]) == 1
        assert "page ka content" in out["fetched"][0]["text"]
        assert out["failed"] == []

    @pytest.mark.asyncio
    async def test_403_is_explicit_failure(self, monkeypatch):
        def handler(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(403)
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_urls("ye https://blocked-b2.test/page dekho")
        assert out["ok"] is True
        assert out["fetched"] == []
        assert out["failed"][0]["error"] == "http_403"

    @pytest.mark.asyncio
    async def test_robots_disallow_blocks_fetch(self, monkeypatch):
        def handler(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private")
            return httpx.Response(200, headers={"content-type": "text/html"},
                                   html="<body>secret</body>")
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_urls("https://robots-c3.test/private/doc padho")
        assert out["failed"][0]["error"] == "blocked_by_robots_txt"

    @pytest.mark.asyncio
    async def test_robots_allows_other_paths(self, monkeypatch):
        def handler(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private")
            return httpx.Response(200, headers={"content-type": "text/html"},
                                   html="<body><p>public content available here</p></body>")
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_urls("https://robots-d4.test/blog/post padho")
        assert len(out["fetched"]) == 1

    @pytest.mark.asyncio
    async def test_unsupported_content_type(self, monkeypatch):
        def handler(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                   content=b"%PDF-1.4")
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_urls("https://pdf-e5.test/doc.pdf padho")
        assert out["failed"][0]["error"].startswith("unsupported_content_type")


class TestEmptyRetry:
    def test_thin_detection(self):
        assert _is_thin_result({"ok": True, "results": []}) is True
        assert _is_thin_result({"ok": True, "results": [{"a": 1}]}) is False
        assert _is_thin_result({"ok": True, "text": "hi"}) is True
        assert _is_thin_result({"ok": True, "text": "x" * 100}) is False
        # tool ERRORS are not "thin" — they take the error path
        assert _is_thin_result({"ok": False, "error": "http_403"}) is False

    @pytest.mark.asyncio
    async def test_retry_fires_once_and_recovers(self):
        calls = []
        async def fake_fetch(q):
            calls.append(q)
            if len(calls) == 1:
                return {"tool": "social", "ok": True, "results": []}
            return {"tool": "social", "ok": True,
                    "results": [{"title": "hit"}]}
        out = await _with_empty_retry(
            fake_fetch, "koi accha fastapi rate limiting thread dhundo reddit pe")
        assert len(calls) == 2
        assert calls[1] == "fastapi rate limiting thread reddit"
        assert out["results"] == [{"title": "hit"}]
        assert out["retried_with"] == calls[1]

    @pytest.mark.asyncio
    async def test_still_empty_after_retry_marked_genuine(self):
        async def fake_fetch(q):
            return {"tool": "news", "ok": True, "results": []}
        out = await _with_empty_retry(fake_fetch, "xyzzy foobar news batao kya hai")
        assert out["empty"] is True
        assert out["reason"] == "no_results_after_retry"

    @pytest.mark.asyncio
    async def test_no_retry_on_good_first_result(self):
        calls = []
        async def fake_fetch(q):
            calls.append(q)
            return {"tool": "web", "ok": True, "text": "x" * 200}
        await _with_empty_retry(fake_fetch, "kuch bhi query")
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_error_passthrough_no_retry(self):
        calls = []
        async def fake_fetch(q):
            calls.append(q)
            return {"tool": "web", "ok": False, "error": "http_500"}
        out = await _with_empty_retry(fake_fetch, "query with substantive tokens fastapi")
        assert len(calls) == 1 and out["error"] == "http_500"


_BUDGET = AsyncMock(return_value={"day_cap_usd": 2.5,
                                   "day_spent_usd": 0.1,
                                   "mode": "normal"})


class TestOrchestrateUrlIntegration:
    @pytest.mark.asyncio
    async def test_fetched_content_reaches_synth_prompt(self):
        async def fake_urls(q):
            return {"tool": "url", "ok": True,
                    "fetched": [{"url": "https://news.test/a", "ok": True,
                                  "text": "breaking article body"}],
                    "failed": []}
        with patch("services.ora_chat.deep_research._fetch_urls", side_effect=fake_urls), \
             patch("services.ora_chat.deep_research.has_fetchable_url", return_value=True), \
             patch("services.ora_chat.deep_research._fetch_sonar",
                   new=AsyncMock(return_value={"tool": "web", "ok": True, "text": "x" * 60,
                                                "usage": {}, "cost_usd": 0.0})), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status", new=_BUDGET), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("ans", {"input_tokens": 1, "output_tokens": 1}, None))):
            out = await dr.orchestrate("https://news.test/a padho", [])
        assert '<fetched_url_content source="https://news.test/a">' in out["synth_prompt"]
        assert "breaking article body" in out["synth_prompt"]
        assert "url" in out["sources_fired"]

    @pytest.mark.asyncio
    async def test_failed_fetch_gets_explicit_block(self):
        async def fake_urls(q):
            return {"tool": "url", "ok": True, "fetched": [],
                    "failed": [{"url": "https://x.test/p", "ok": False,
                                 "error": "http_403"}]}
        with patch("services.ora_chat.deep_research._fetch_urls", side_effect=fake_urls), \
             patch("services.ora_chat.deep_research.has_fetchable_url", return_value=True), \
             patch("services.ora_chat.deep_research._fetch_sonar",
                   new=AsyncMock(return_value={"tool": "web", "ok": True, "text": "x" * 60,
                                                "usage": {}, "cost_usd": 0.0})), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status", new=_BUDGET), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("ans", {"input_tokens": 1, "output_tokens": 1}, None))):
            out = await dr.orchestrate("https://x.test/p padho", [])
        assert '<url_fetch_failed url="https://x.test/p" error="http_403">' in out["synth_prompt"]

    @pytest.mark.asyncio
    async def test_social_no_match_block_after_retry(self):
        async def fake_reddit(q):
            return {"tool": "social", "ok": True, "results": []}
        with patch("services.ora_chat.deep_research._fetch_reddit", side_effect=fake_reddit), \
             patch("services.ora_chat.deep_research._fetch_sonar",
                   new=AsyncMock(return_value={"tool": "web", "ok": True, "text": "x" * 60,
                                                "usage": {}, "cost_usd": 0.0})), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status", new=_BUDGET), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("ans", {"input_tokens": 1, "output_tokens": 1}, None))):
            out = await dr.orchestrate(
                "log kya bol rahe hain xyzzy library ke bare mein reddit pe",
                ["NEEDS_WEB", "NEEDS_SOCIAL"])
        assert "<social_no_match" in out["synth_prompt"]
        assert "no_results_after_retry" in out["synth_prompt"]
