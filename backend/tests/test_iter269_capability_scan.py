"""
tests/test_iter269_capability_scan.py — P1a/P1b/P2a/P2b from the
"karpathy-skills repo scan" incident post-mortem.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import services.ora_chat.deep_research as dr
from services.ora_chat.deep_research import _fetch_github, _SCAN_INTENT_RE
from services.ora_chat.grounding_check import (
    extract_line_claims, extract_unknown_commands, run_post_response_check,
)
from services.ora_chat.safety import AUREM_CONTEXT
from services.ora_chat.adversarial_review import (
    _parse_flags, verify_quotes, corrective_prompt, _HARD_TYPES,
)

_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    def factory(**kw):
        return _RealAsyncClient(transport=httpx.MockTransport(handler),
                                timeout=kw.get("timeout"))
    return factory


class TestRepoContentScan:
    def test_scan_intent_regex(self):
        assert _SCAN_INTENT_RE.search("is repo ko full detailed scan kro")
        assert _SCAN_INTENT_RE.search("kitni useful ho skti hai")
        assert not _SCAN_INTENT_RE.search("star kitne hain bas")

    @pytest.mark.asyncio
    async def test_scan_fetches_tree_and_top_files(self, monkeypatch):
        def handler(req):
            p = req.url.path
            if p == "/repos/o/r":
                return httpx.Response(200, json={
                    "full_name": "o/r", "stargazers_count": 5,
                    "description": "d", "html_url": "u",
                    "default_branch": "main"})
            if p.startswith("/repos/o/r/git/trees/"):
                return httpx.Response(200, json={"tree": [
                    {"path": "CLAUDE.md", "type": "blob"},
                    {"path": "src/x.py", "type": "blob"}]})
            if p == "/repos/o/r/contents/CLAUDE.md":
                return httpx.Response(200, text="# Karpathy rules content")
            return httpx.Response(404)
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github(
            "https://github.com/o/r is repo ko scan kro aur batao")
        res = out["results"][0]
        assert res["tree"] == ["CLAUDE.md", "src/x.py"]
        assert res["files"][0]["path"] == "CLAUDE.md"
        assert "Karpathy rules" in res["files"][0]["content_head"]

    @pytest.mark.asyncio
    async def test_no_scan_intent_metadata_only(self, monkeypatch):
        def handler(req):
            if req.url.path == "/repos/o/r":
                return httpx.Response(200, json={
                    "full_name": "o/r", "stargazers_count": 5,
                    "description": "d", "html_url": "u",
                    "default_branch": "main"})
            raise AssertionError("tree/contents must not be fetched")
        monkeypatch.setattr(dr.httpx, "AsyncClient",
                            _mock_client_factory(handler))
        out = await _fetch_github("https://github.com/o/r ka star count?")
        assert "tree" not in out["results"][0]


class TestCapabilityManifest:
    def test_manifest_present(self):
        assert "CAPABILITY MANIFEST" in AUREM_CONTEXT
        assert "there is NO /deploy command" in AUREM_CONTEXT
        assert "say so in your FIRST" in AUREM_CONTEXT


class TestLineAndCommandClaims:
    def test_line_claim_extraction_both_orders(self):
        r1 = "Media query conflict in ChatPanel.jsx at line 210 hai."
        r2 = "Dekho line 42 of backend/routers/ora_chat.py mein."
        assert extract_line_claims(r1) == [("ChatPanel.jsx", 210)]
        assert extract_line_claims(r2) == [("backend/routers/ora_chat.py", 42)]

    def test_unknown_command_flagged_known_passes(self):
        r = ("Deploy karo `/deploy-production --hotfix` se, phir "
             "/read backend/main.py aur /repo-tree chalao.")
        cmds = extract_unknown_commands(r)
        assert "/deploy-production" in cmds
        assert "/read" not in cmds and "/repo-tree" not in cmds

    def test_file_paths_not_command_flagged(self):
        r = "Config /app/backend/main.py mein hai aur /tmp/x.log dekho."
        assert extract_unknown_commands(r) == []

    @pytest.mark.asyncio
    async def test_hook_merges_new_checks(self):
        r = await run_post_response_check(
            user_id="t", session_id="t", query="mobile bug kaha hai?",
            reply=("ChatPanel.jsx line 210 pe z-index clash hai. "
                   "Fix ke baad `/deploy-production` chala dena."),
            route="general")
        assert "/deploy-production" in r["fabricated"]
        assert any(u.endswith(":L210") for u in r["unverified"])

    @pytest.mark.asyncio
    async def test_line_claim_grounded_when_file_content_retrieved(self):
        r = await run_post_response_check(
            user_id="t", session_id="t", query="q",
            reply="ChatPanel.jsx line 210 pe issue hai.",
            route="general",
            retrieved_context="/read ChatPanel.jsx output:\n... line 210 ...")
        assert not any(u.endswith(":L210") for u in r["unverified"])


class TestIgnoredTaskFlag:
    def test_parse_accepts_ignored_task(self):
        raw = ('[{"quote":"full detailed scan kro",'
               '"type":"IGNORED_TASK","reason":"no scan output"}]')
        flags, ok = _parse_flags(raw)
        assert ok and flags[0]["type"] == "IGNORED_TASK"
        assert "IGNORED_TASK" in _HARD_TYPES

    def test_quote_guard_allows_query_quote(self):
        flags = [{"quote": "full detailed scan kro",
                  "type": "IGNORED_TASK", "reason": "r"}]
        kept, dropped = verify_quotes(flags, draft="kuch aur likha hai",
                                      query="repo ko full detailed scan kro")
        assert len(kept) == 1 and dropped == []

    def test_quote_guard_caps_one_ignored_task(self):
        flags = [{"quote": "", "type": "IGNORED_TASK", "reason": "a"},
                 {"quote": "", "type": "IGNORED_TASK", "reason": "b"}]
        kept, dropped = verify_quotes(flags, "d", "q")
        assert len(kept) == 1 and len(dropped) == 1

    def test_corrective_mentions_ignored_task(self):
        p = corrective_prompt([
            {"quote": "fake.py sab karta hai", "type": "FABRICATED",
             "reason": "r"},
            {"quote": "scan kro", "type": "IGNORED_TASK", "reason": "r"}])
        assert "unsupported claims" in p
        assert "FAILED to address" in p and '"scan kro"' in p
