"""
tests/test_ora_chat_deep_research.py — Iter 212m-245

Coverage for the Auto Deep-Research feature:
  - multi-label classifier: JSON parsing + fallback on error
  - should_go_deep threshold (>=2 substantive labels OR NEEDS_DEEP)
  - _tools_for_labels dispatch table
  - _labels_to_source_tag ordering (used by the frontend badge)
  - orchestrate happy path (>=2 tools) — mocked tool + synth calls
  - orchestrate downgrade path (silent fallback to Sonar when budget
    remaining <$0.50)
  - `deep` route is registered on the routing table
  - `tool_orchestration` route is a feature-flagged stub (disabled
    unless both ORA_ENABLE_CLAUDE_TOOLS=1 AND ANTHROPIC_API_KEY set)

Run:
    cd /app/backend && python -m pytest tests/test_ora_chat_deep_research.py -v
"""
from __future__ import annotations

import os
from unittest.mock import patch, AsyncMock

import pytest

from services.ora_chat import deep_research as dr
from services.ora_chat.router import all_route_names, resolve
from routers.ora_chat import _labels_to_source_tag


# ══════════════════════════════════════════════════════════════════
# 1. CLASSIFIER
# ══════════════════════════════════════════════════════════════════
class TestClassifyLabels:
    @pytest.mark.asyncio
    async def test_parses_valid_json_array(self):
        with patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=('["NEEDS_WEB","NEEDS_GITHUB"]',
                                                {"input_tokens": 10,
                                                 "output_tokens": 5},
                                                None))):
            labels = await dr.classify_labels("compare fastapi vs express on github")
        assert labels == ["NEEDS_WEB", "NEEDS_GITHUB"]

    @pytest.mark.asyncio
    async def test_strips_code_fences(self):
        with patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=('```json\n["NEEDS_NEWS"]\n```',
                                                {"input_tokens": 5,
                                                 "output_tokens": 5},
                                                None))):
            labels = await dr.classify_labels("news today")
        assert labels == ["NEEDS_NEWS"]

    @pytest.mark.asyncio
    async def test_drops_invalid_labels(self):
        with patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=('["NEEDS_WEB","BOGUS_LABEL"]',
                                                {"input_tokens": 1,
                                                 "output_tokens": 1},
                                                None))):
            labels = await dr.classify_labels("q")
        assert labels == ["NEEDS_WEB"]

    @pytest.mark.asyncio
    async def test_falls_back_to_web_on_error(self):
        with patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("", None, "network_fail"))):
            labels = await dr.classify_labels("q")
        assert labels == ["NEEDS_WEB"]

    @pytest.mark.asyncio
    async def test_falls_back_to_web_on_unparseable(self):
        with patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("not json at all",
                                                {"input_tokens": 1,
                                                 "output_tokens": 1},
                                                None))):
            labels = await dr.classify_labels("q")
        assert labels == ["NEEDS_WEB"]


# ══════════════════════════════════════════════════════════════════
# 2. should_go_deep — the fan-out gate
# ══════════════════════════════════════════════════════════════════
class TestShouldGoDeep:
    @pytest.mark.asyncio
    async def test_web_alone_does_not_go_deep(self):
        # NEEDS_WEB has a standalone Sonar route → stays cheap
        assert await dr.should_go_deep(["NEEDS_WEB"]) is False

    @pytest.mark.asyncio
    async def test_non_web_single_label_forces_deep(self):
        # GitHub/Social/News/Codebase have NO standalone route → must
        # go through the orchestrator to actually fetch data.
        assert await dr.should_go_deep(["NEEDS_GITHUB"]) is True
        assert await dr.should_go_deep(["NEEDS_SOCIAL"]) is True
        assert await dr.should_go_deep(["NEEDS_NEWS"]) is True
        assert await dr.should_go_deep(["NEEDS_CODEBASE"]) is True

    @pytest.mark.asyncio
    async def test_two_substantive_labels_goes_deep(self):
        assert await dr.should_go_deep(["NEEDS_WEB", "NEEDS_GITHUB"]) is True
        assert await dr.should_go_deep(["NEEDS_NEWS", "NEEDS_SOCIAL"]) is True

    @pytest.mark.asyncio
    async def test_explicit_deep_flag_alone_goes_deep(self):
        assert await dr.should_go_deep(["NEEDS_DEEP"]) is True

    @pytest.mark.asyncio
    async def test_deep_flag_plus_one_substantive_still_goes_deep(self):
        # NEEDS_DEEP alone triggers, regardless of substantive count
        assert await dr.should_go_deep(["NEEDS_DEEP", "NEEDS_WEB"]) is True

    @pytest.mark.asyncio
    async def test_empty_labels_do_not_go_deep(self):
        assert await dr.should_go_deep([]) is False


# ══════════════════════════════════════════════════════════════════
# 3. _tools_for_labels — dispatch table
# ══════════════════════════════════════════════════════════════════
class TestToolsForLabels:
    def test_web_label_maps_to_sonar(self):
        tasks = dr._tools_for_labels({"NEEDS_WEB"}, "q")
        try:
            assert [t[0] for t in tasks] == ["web"]
        finally:
            for _, coro in tasks:
                coro.close()

    def test_multiple_labels_produce_multiple_tasks(self):
        tasks = dr._tools_for_labels(
            {"NEEDS_WEB", "NEEDS_GITHUB", "NEEDS_SOCIAL", "NEEDS_NEWS"}, "q")
        try:
            tags = [t[0] for t in tasks]
            assert set(tags) == {"web", "github", "social", "news"}
            # respects _MAX_PARALLEL
            assert len(tasks) <= dr._MAX_PARALLEL
        finally:
            for _, coro in tasks:
                coro.close()

    def test_caps_at_max_parallel(self):
        # All 4 real labels — still <= _MAX_PARALLEL
        tasks = dr._tools_for_labels(
            {"NEEDS_WEB", "NEEDS_GITHUB", "NEEDS_SOCIAL", "NEEDS_NEWS"}, "q")
        try:
            assert len(tasks) == 4
        finally:
            for _, coro in tasks:
                coro.close()


# ══════════════════════════════════════════════════════════════════
# 4. Route badge helper
# ══════════════════════════════════════════════════════════════════
class TestLabelsToSourceTag:
    def test_orders_canonically(self):
        # github+social+news+web is the canonical display order
        assert _labels_to_source_tag(["web", "github"]) == "github+web"
        assert _labels_to_source_tag(["news", "github", "web"]) == "github+news+web"

    def test_empty_returns_none(self):
        assert _labels_to_source_tag([]) == "none"

    def test_preserves_unknown_tags_at_end(self):
        assert _labels_to_source_tag(["web", "xtool"]).endswith("xtool")


# ══════════════════════════════════════════════════════════════════
# 5. orchestrate() — happy path
# ══════════════════════════════════════════════════════════════════
class TestOrchestrate:
    @pytest.mark.asyncio
    async def test_happy_path_fires_multiple_tools_and_synthesizes(self):
        async def fake_web(q):
            return {"tool": "web", "ok": True, "text": "Sonar result",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                    "cost_usd": 0.001}
        async def fake_github(q):
            return {"tool": "github", "ok": True,
                    "results": [{"name": "foo/bar", "stars": 1000}]}
        async def fake_reddit(q):
            return {"tool": "social", "ok": True,
                    "results": [{"title": "reddit post", "sub": "r/x"}]}

        with patch("services.ora_chat.deep_research._fetch_sonar", side_effect=fake_web), \
             patch("services.ora_chat.deep_research._fetch_github", side_effect=fake_github), \
             patch("services.ora_chat.deep_research._fetch_reddit", side_effect=fake_reddit), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status",
                   new=AsyncMock(return_value={"day_cap_usd": 2.5,
                                                "day_spent_usd": 0.1,
                                                "mode": "normal"})), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("synthesized answer",
                                                {"input_tokens": 500,
                                                 "output_tokens": 200},
                                                None))):
            out = await dr.orchestrate(
                "compare fastapi vs express with reddit sentiment",
                ["NEEDS_WEB", "NEEDS_GITHUB", "NEEDS_SOCIAL"],
            )
        assert out["ok"] is True
        assert out["text"] == "synthesized answer"
        assert set(out["sources_fired"]) == {"web", "github", "social"}
        assert out["downgraded"] is False
        assert out["tool_cost_usd"] > 0.0

    @pytest.mark.asyncio
    async def test_tool_failure_does_not_block_synthesis(self):
        async def fake_web(q):
            return {"tool": "web", "ok": True, "text": "Sonar result",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                    "cost_usd": 0.001}
        async def fake_github(q):
            return {"tool": "github", "ok": False, "error": "http_500"}

        with patch("services.ora_chat.deep_research._fetch_sonar", side_effect=fake_web), \
             patch("services.ora_chat.deep_research._fetch_github", side_effect=fake_github), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status",
                   new=AsyncMock(return_value={"day_cap_usd": 2.5,
                                                "day_spent_usd": 0.1,
                                                "mode": "normal"})), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("partial synth",
                                                {"input_tokens": 100,
                                                 "output_tokens": 50},
                                                None))):
            out = await dr.orchestrate("q",
                                       ["NEEDS_WEB", "NEEDS_GITHUB"])
        assert out["ok"] is True
        assert "web" in out["sources_fired"]
        assert "github" not in out["sources_fired"]
        assert any("github" in e for e in out["errors"])


# ══════════════════════════════════════════════════════════════════
# 6. Cost guard — downgrade to Sonar when near daily cap
# ══════════════════════════════════════════════════════════════════
class TestDowngrade:
    @pytest.mark.asyncio
    async def test_downgrades_when_within_margin(self):
        called_tools = []
        async def fake_web(q):
            called_tools.append("web")
            return {"tool": "web", "ok": True, "text": "Sonar",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "cost_usd": 0.001}
        async def fake_github(q):
            called_tools.append("github")
            return {"tool": "github", "ok": True, "results": []}

        # remaining = 2.5 - 2.2 = 0.3 (< 0.5 margin) → downgrade
        with patch("services.ora_chat.deep_research._fetch_sonar", side_effect=fake_web), \
             patch("services.ora_chat.deep_research._fetch_github", side_effect=fake_github), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status",
                   new=AsyncMock(return_value={"day_cap_usd": 2.5,
                                                "day_spent_usd": 2.2,
                                                "mode": "warning"})), \
             patch("services.ora_chat.deep_research.one_shot",
                   new=AsyncMock(return_value=("cheap answer",
                                                {"input_tokens": 10,
                                                 "output_tokens": 5},
                                                None))):
            out = await dr.orchestrate("q",
                                       ["NEEDS_WEB", "NEEDS_GITHUB"])
        assert out["downgraded"] is True
        # github must NOT have been called — only web should have fired
        assert "github" not in called_tools
        assert called_tools == ["web"]


# ══════════════════════════════════════════════════════════════════
# 7. Route registration + Claude tool_orchestration stub gate
# ══════════════════════════════════════════════════════════════════
class TestRouteRegistration:
    def test_deep_route_is_registered(self):
        assert "deep" in all_route_names()
        cfg = resolve("deep")
        assert cfg["route"] == "deep"
        assert cfg["temperature"] > 0.0

    def test_tool_orchestration_stub_registered(self):
        assert "tool_orchestration" in all_route_names()
        cfg = resolve("tool_orchestration")
        assert "haiku" in cfg["model"].lower() or "claude" in cfg["model"].lower()


class TestClaudeToolsFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        # Sanity — ensure defaults are OFF even if key is present.
        monkeypatch.delenv("ORA_ENABLE_CLAUDE_TOOLS", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        assert dr.use_claude_tools() is False

    def test_enabled_only_when_both_flag_and_key_set(self, monkeypatch):
        monkeypatch.setenv("ORA_ENABLE_CLAUDE_TOOLS", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
        assert dr.use_claude_tools() is True

    def test_flag_without_key_stays_disabled(self, monkeypatch):
        monkeypatch.setenv("ORA_ENABLE_CLAUDE_TOOLS", "1")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert dr.use_claude_tools() is False
