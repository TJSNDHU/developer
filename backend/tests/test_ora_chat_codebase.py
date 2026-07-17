"""
tests/test_ora_chat_codebase.py — Iter 212m-246

Coverage for the codebase-awareness layer:
  - build_index walks /app/backend + /app/frontend/src, skips
    node_modules/.git/__pycache__/build/venv/.pytest_cache/etc.
  - _should_index rejects disallowed extensions + skip dirs
  - compact_tree stays under budget (~4 KB)
  - find_files globs correctly (substring and *)
  - read_file rejects path traversal + outside-root paths
  - search_defs finds real defs (test looks for known symbols)
  - bm25_relevant_files returns non-empty for a query about ORA Chat
  - New slash-commands are registered (/repo-tree, /find, /read, /defs)
"""
from __future__ import annotations

import pytest

from services.ora_chat import codebase_index as cb
from services.ora_chat.safety import KNOWN_COMMANDS, parse_slash_command
from services.ora_chat.slash_commands import DISPATCH


class TestIndexBuild:
    @pytest.mark.asyncio
    async def test_build_index_populates_files(self):
        r = await cb.build_index(force=True)
        assert r["ok"] is True
        assert r["files"] > 50  # We have plenty of files in /app
        stats = await cb.index_stats()
        assert stats["files"] == r["files"]
        assert "python" in stats["by_language"]

    @pytest.mark.asyncio
    async def test_skips_excluded_dirs(self):
        r = await cb.build_index(force=True)
        # No index entry should contain node_modules or __pycache__
        for f in cb._CACHE["files"]:
            assert "node_modules" not in f["path"]
            assert "__pycache__" not in f["path"]
            assert not f["path"].startswith(".")


class TestCompactTree:
    @pytest.mark.asyncio
    async def test_compact_tree_bounded(self):
        text = await cb.compact_tree(max_files=120)
        assert "AUREM repo tree" in text
        # Must stay well under 8 KB so it doesn't blow token budget
        assert len(text) < 8000


class TestFindFiles:
    @pytest.mark.asyncio
    async def test_substring_match(self):
        matches = await cb.find_files("ora_chat")
        # Multiple ora_chat files exist
        assert len(matches) >= 3

    @pytest.mark.asyncio
    async def test_glob_match(self):
        matches = await cb.find_files("*deep_research*")
        assert any("deep_research" in m for m in matches)

    @pytest.mark.asyncio
    async def test_empty_pattern_returns_empty(self):
        assert await cb.find_files("") == []


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing_python_file(self):
        r = await cb.read_file("backend/services/ora_chat/safety.py")
        assert r["ok"] is True
        assert r["lang"] == "python"
        assert "CORE_SAFETY_RULES" in r["content"]

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        r = await cb.read_file("../../etc/passwd")
        assert r["ok"] is False
        assert r["error"] in ("path_traversal_blocked", "outside_repo_root",
                                "not_found", "extension_not_allowed")

    @pytest.mark.asyncio
    async def test_rejects_absolute_outside_root(self):
        r = await cb.read_file("/etc/passwd")
        assert r["ok"] is False

    @pytest.mark.asyncio
    async def test_missing_file_returns_not_found(self):
        r = await cb.read_file("backend/does_not_exist.py")
        assert r["ok"] is False
        assert r["error"] == "not_found"


class TestSearchDefs:
    @pytest.mark.asyncio
    async def test_finds_known_python_def(self):
        await cb.build_index(force=True)
        hits = await cb.search_defs("classify_labels")
        assert len(hits) >= 1
        assert any("deep_research" in h["path"] for h in hits)

    @pytest.mark.asyncio
    async def test_finds_known_class(self):
        await cb.build_index(force=True)
        hits = await cb.search_defs("RouteConfig")
        assert len(hits) >= 1


class TestBM25:
    @pytest.mark.asyncio
    async def test_returns_relevant_files_for_ora_chat_query(self):
        await cb.build_index(force=True)
        hits = await cb.bm25_relevant_files("deep research classifier", top_k=3)
        assert len(hits) >= 1
        # deep_research.py should be near the top
        assert any("deep_research" in h["path"] for h in hits)

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        await cb.build_index(force=True)
        assert await cb.bm25_relevant_files("", top_k=3) == []


class TestSlashCommandsWired:
    def test_new_codebase_commands_in_known_list(self):
        for cmd in ("repo-tree", "repo-stats", "find", "read", "defs"):
            assert cmd in KNOWN_COMMANDS

    def test_new_codebase_commands_in_dispatch(self):
        for cmd in ("repo-tree", "repo-stats", "find", "read", "defs"):
            assert cmd in DISPATCH

    def test_parser_accepts_find_with_arg(self):
        parsed = parse_slash_command("/find deep_research")
        assert parsed == ("find", "deep_research")

    def test_parser_accepts_read_with_path(self):
        parsed = parse_slash_command("/read backend/services/ora_chat/safety.py")
        assert parsed == ("read", "backend/services/ora_chat/safety.py")

    def test_parser_accepts_defs_with_symbol(self):
        parsed = parse_slash_command("/defs classify_labels")
        assert parsed == ("defs", "classify_labels")

    @pytest.mark.asyncio
    async def test_repo_tree_handler_returns_text(self):
        r = await DISPATCH["repo-tree"]({}, "")
        assert r["ok"] is True
        assert "AUREM repo tree" in r["value"]

    @pytest.mark.asyncio
    async def test_find_handler_missing_arg(self):
        r = await DISPATCH["find"]({}, "")
        assert r["ok"] is False
        assert r["error"] == "missing_pattern"

    @pytest.mark.asyncio
    async def test_read_handler_rejects_missing_arg(self):
        r = await DISPATCH["read"]({}, "")
        assert r["ok"] is False
        assert r["error"] == "missing_path"

    @pytest.mark.asyncio
    async def test_defs_handler_missing_arg(self):
        r = await DISPATCH["defs"]({}, "")
        assert r["ok"] is False
        assert r["error"] == "missing_name"


class TestSystemPromptInjection:
    def test_assemble_accepts_codebase_tree_kwarg(self):
        from services.ora_chat.safety import assemble_system_prompt, CORE_SAFETY_RULES
        tree = "AUREM repo tree: mock tree content"
        prompt = assemble_system_prompt(codebase_tree=tree,
                                          include_runtime=False)
        # Safety layer STILL first
        assert prompt.startswith(CORE_SAFETY_RULES)
        # Tree included
        assert tree in prompt

    def test_omitting_codebase_tree_is_backward_compat(self):
        from services.ora_chat.safety import assemble_system_prompt, CORE_SAFETY_RULES
        prompt = assemble_system_prompt(include_runtime=False)
        assert prompt.startswith(CORE_SAFETY_RULES)
        assert "AUREM repo tree" not in prompt
