"""
tests/test_iter266_github_adapter.py — GitHub adapter fix (Iter 266).

Covers the 3-tier fix from the iter-265 evidence investigation:
  1. URL / owner-repo extraction → direct GET /repos/{o}/{r}
  2. Filler-stripped search query (Hinglish conversational words)
  3. Distinct shapes + user-facing blocks for genuine-empty vs
     tool-failure (previously merged into one vague "empty").
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import services.ora_chat.deep_research as dr
from services.ora_chat.deep_research import (
    _extract_github_target, _clean_search_query, _fetch_github,
)


class TestExtractTarget:
    def test_url_with_git_suffix(self):
        q = "analyse karo https://github.com/multica-ai/andrej-karpathy-skills.git"
        assert _extract_github_target(q) == ("multica-ai", "andrej-karpathy-skills")

    def test_url_without_git_suffix(self):
        q = "dekho https://github.com/multica-ai/andrej-karpathy-skills kya hai"
        assert _extract_github_target(q) == ("multica-ai", "andrej-karpathy-skills")

    def test_url_with_trailing_path(self):
        q = "https://github.com/foo/bar/tree/main/src padho"
        assert _extract_github_target(q) == ("foo", "bar")

    def test_ssh_style_url(self):
        q = "clone git@github.com:foo/bar.git kaise karun"
        assert _extract_github_target(q) == ("foo", "bar")

    def test_shorthand_needs_repo_keyword(self):
        assert _extract_github_target(
            "repo multica-ai/andrej-karpathy-skills analyse karo"
        ) == ("multica-ai", "andrej-karpathy-skills")

    def test_shorthand_without_keyword_is_ignored(self):
        # Codebase paths must NOT be treated as GitHub slugs.
        assert _extract_github_target("backend/routers kya karta hai") is None

    def test_plain_sentence_none(self):
        assert _extract_github_target("kya best build hai hmara system main") is None


class TestCleanQuery:
    def test_strips_hinglish_fillers(self):
        q = "is repo ko analyse karo aur batao kya karta hai ye fastapi wrapper"
        cleaned = _clean_search_query(q)
        for bad in ("repo", "analyse", "karo", "batao", "karta"):
            assert bad not in cleaned.split()
        assert "fastapi" in cleaned
        assert "wrapper" in cleaned

    def test_strips_urls(self):
        q = "https://github.com/foo/bar dekho aur karpathy skills dhundo"
        cleaned = _clean_search_query(q)
        assert "github.com" not in cleaned
        assert "karpathy" in cleaned
        assert "skills" in cleaned

    def test_caps_at_six_tokens(self):
        q = " ".join(f"uniqueterm{i}" for i in range(12))
        assert len(_clean_search_query(q).split()) == 6


_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    def factory(**kw):
        return _RealAsyncClient(transport=httpx.MockTransport(handler),
                                timeout=kw.get("timeout"))
    return factory


class TestFetchGithubMocked:
    @pytest.mark.asyncio
    async def test_direct_lookup_success(self, monkeypatch):
        def handler(req):
            assert req.url.path == "/repos/multica-ai/andrej-karpathy-skills"
            return httpx.Response(200, json={
                "full_name": "multica-ai/andrej-karpathy-skills",
                "stargazers_count": 194000, "description": "skills",
                "html_url": "https://github.com/multica-ai/andrej-karpathy-skills",
                "language": "Python", "topics": ["ai"],
            })
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github(
            "analyse https://github.com/multica-ai/andrej-karpathy-skills.git")
        assert out["ok"] is True and out["lookup"] == "direct"
        assert out["results"][0]["name"] == "multica-ai/andrej-karpathy-skills"
        assert out["results"][0]["stars"] == 194000

    @pytest.mark.asyncio
    async def test_direct_404_is_genuine_empty(self, monkeypatch):
        def handler(req):
            return httpx.Response(404, json={"message": "Not Found"})
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github("repo foo/definitely-not-real dekho")
        assert out["ok"] is True and out["empty"] is True
        assert out["reason"].startswith("repo_not_found:")

    @pytest.mark.asyncio
    async def test_search_zero_results_is_genuine_empty(self, monkeypatch):
        def handler(req):
            assert req.url.path == "/search/repositories"
            assert "in:name,description,readme" in req.url.params.get("q", "")
            return httpx.Response(200, json={"total_count": 0, "items": []})
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github("koi obscure xyzzy library dhundo github pe nahi hai")
        assert out["ok"] is True and out["empty"] is True
        assert out["reason"] == "no_search_match"

    @pytest.mark.asyncio
    async def test_rate_limit_is_tool_error_not_empty(self, monkeypatch):
        def handler(req):
            return httpx.Response(403, json={"message": "rate limit exceeded"})
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github("fastapi middleware library github")
        assert out["ok"] is False
        assert out["error"] == "http_403_rate_limit"
        assert "empty" not in out

    @pytest.mark.asyncio
    async def test_timeout_is_tool_error(self, monkeypatch):
        def handler(req):
            raise httpx.ReadTimeout("slow")
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github("fastapi github")
        assert out["ok"] is False and out["error"] == "ReadTimeout"


_BUDGET = AsyncMock(return_value={"day_cap_usd": 2.5,
                                   "day_spent_usd": 0.1,
                                   "mode": "normal"})


class TestOrchestrateDistinction:
    @pytest.mark.asyncio
    async def test_genuine_empty_gets_no_match_block(self):
        async def fake_web(q):
            return {"tool": "web", "ok": True, "text": "web result",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "cost_usd": 0.001}
        async def fake_github(q):
            return {"tool": "github", "ok": True, "empty": True,
                    "results": [], "reason": "no_search_match"}
        with patch("services.ora_chat.deep_research._fetch_sonar", side_effect=fake_web), \
             patch("services.ora_chat.deep_research._fetch_github", side_effect=fake_github), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status", new=_BUDGET), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("ans", {"input_tokens": 1, "output_tokens": 1}, None))):
            out = await dr.orchestrate("q", ["NEEDS_WEB", "NEEDS_GITHUB"])
        assert "<github_no_match" in out["synth_prompt"]
        assert '<github_tool_error error=' not in out["synth_prompt"]
        assert "github" in out["sources_fired"]  # tool worked (200)

    @pytest.mark.asyncio
    async def test_tool_error_gets_error_block(self):
        async def fake_web(q):
            return {"tool": "web", "ok": True, "text": "web result",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "cost_usd": 0.001}
        async def fake_github(q):
            return {"tool": "github", "ok": False,
                    "error": "http_403_rate_limit"}
        with patch("services.ora_chat.deep_research._fetch_sonar", side_effect=fake_web), \
             patch("services.ora_chat.deep_research._fetch_github", side_effect=fake_github), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status", new=_BUDGET), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("ans", {"input_tokens": 1, "output_tokens": 1}, None))):
            out = await dr.orchestrate("q", ["NEEDS_WEB", "NEEDS_GITHUB"])
        assert '<github_tool_error error="http_403_rate_limit"' in out["synth_prompt"]
        assert "github" not in out["sources_fired"]
        assert any("github:http_403_rate_limit" in e for e in out["errors"])

    @pytest.mark.asyncio
    async def test_github_only_failure_still_reaches_user(self):
        # Previously: early-return → generic "sources didn't respond".
        # Now: synthesis runs on the error block → explicit message.
        async def fake_github(q):
            return {"tool": "github", "ok": False, "error": "ReadTimeout"}
        with patch("services.ora_chat.deep_research._fetch_github", side_effect=fake_github), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status", new=_BUDGET), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("tool fail hua", {"input_tokens": 1, "output_tokens": 1}, None))):
            out = await dr.orchestrate("repo dekho", ["NEEDS_GITHUB"])
        assert out["ok"] is True
        assert "<github_tool_error" in out["synth_prompt"]
        assert out["sources_fired"] == []
