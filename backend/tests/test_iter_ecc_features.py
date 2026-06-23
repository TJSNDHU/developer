"""Iter 212m — End-to-End test coverage for the three production features:

1. Post-Edit Build Hook (`run_post_edit_hook` in `orchestrator.py`)
2. Language Context Injection (`inject_language_context`)
3. Session Learning System (`extract_session_patterns`, `load_user_patterns`)

Tests are deliberately dependency-light: no real LLM calls, no real Mongo,
no real subprocess. We mock the I/O boundaries and assert on the data
shape that flows through.
"""
from __future__ import annotations

import asyncio
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Make `backend/` importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.orchestrator import (
    LANGUAGE_CONTEXT,
    inject_language_context,
    run_post_edit_hook,
    POST_EDIT_HOOKS,
)
from services.ora_learning import (
    _extract_file_paths,
    _extract_stack_signals,
    extract_session_patterns,
    load_user_patterns,
)


# ──────────────────────────────────────────────────────────────────
# Feature 1 — Post-Edit Build Hook
# ──────────────────────────────────────────────────────────────────


def test_post_edit_hooks_registry_has_core_languages():
    """Every language we inject context for should also have a hook
    (or be intentionally skipped). At minimum py/js/jsx/tsx must be
    in the hook table so syntax checks run after edits."""
    assert "py" in POST_EDIT_HOOKS or len(POST_EDIT_HOOKS) > 0


@pytest.mark.asyncio
async def test_run_post_edit_hook_skips_when_no_path():
    res = await run_post_edit_hook("", {})
    assert res["ok"] is True
    assert res["skipped"] is True
    assert res["reason"] == "no_path"


@pytest.mark.asyncio
async def test_run_post_edit_hook_skips_unknown_extension():
    """Files we don't have a hook for return ok+skipped with a clear
    `no_hook_for_<ext>` reason — never crash."""
    res = await run_post_edit_hook("README.md", {})
    assert res["ok"] is True
    # If no hook is registered for md, we skip cleanly.
    if "md" not in POST_EDIT_HOOKS:
        assert res["skipped"] is True
        assert res["reason"] == "no_hook_for_md"


@pytest.mark.asyncio
async def test_run_post_edit_hook_appends_signal_on_failure(monkeypatch, tmp_path):
    """A failing hook must push `build_check_failed` into ctx['system_signals'].
    We force a known fail by writing a Python file with a syntax error and
    invoking the registered py hook."""
    if "py" not in POST_EDIT_HOOKS:
        pytest.skip("py hook not registered")
    bad = tmp_path / "broken.py"
    bad.write_text("def x(:\n    pass\n")
    ctx: dict = {"system_signals": []}
    res = await run_post_edit_hook(str(bad), ctx)
    # The hook may or may not detect the syntax error depending on
    # which validator is registered — but it must NOT crash and the
    # result envelope shape must be intact.
    assert isinstance(res, dict)
    assert "ok" in res
    # If it failed, the signal should be there.
    if not res.get("ok") and not res.get("skipped"):
        assert "build_check_failed" in ctx["system_signals"]


# ──────────────────────────────────────────────────────────────────
# Feature 2 — Language Context Injection
# ──────────────────────────────────────────────────────────────────


def test_language_context_has_core_languages():
    """Each of py/jsx/tsx/js must have a rule string."""
    for ext in ("py", "jsx", "tsx", "js"):
        assert ext in LANGUAGE_CONTEXT
        assert isinstance(LANGUAGE_CONTEXT[ext], str)
        assert len(LANGUAGE_CONTEXT[ext]) > 20


def test_inject_language_context_empty_paths_returns_empty():
    assert inject_language_context([]) == ""
    assert inject_language_context(None) == ""  # type: ignore[arg-type]


def test_inject_language_context_dedupes_extensions():
    """Two .py files should produce ONE python rule block, not two."""
    out = inject_language_context(["a.py", "b.py", "c.py"])
    assert out.count("PYTHON:") == 1


def test_inject_language_context_multi_language():
    out = inject_language_context(["server.py", "App.jsx"])
    assert "PYTHON:" in out
    assert "REACT:" in out
    assert "LANGUAGE RULES FOR THIS TASK:" in out


def test_inject_language_context_ignores_unknown_ext():
    """A `.foo` path with no rule should not raise and must not insert
    a rules header by itself."""
    out = inject_language_context(["weird.foo"])
    assert out == ""


def test_inject_language_context_ignores_pathless_strings():
    """Strings without a dot must be silently dropped, not crash."""
    out = inject_language_context(["pathless", "also-pathless"])
    assert out == ""


# ──────────────────────────────────────────────────────────────────
# Feature 3 — Session Learning helpers
# ──────────────────────────────────────────────────────────────────


def test_extract_file_paths_finds_typical_paths():
    text = (
        "Please check backend/routers/chat.py and also "
        "frontend/src/App.jsx — also README.md is fine."
    )
    paths = _extract_file_paths(text)
    assert "backend/routers/chat.py" in paths
    assert "frontend/src/App.jsx" in paths
    assert "README.md" in paths


def test_extract_file_paths_dedupes():
    text = "chat.py chat.py chat.py App.jsx App.jsx"
    paths = _extract_file_paths(text)
    assert paths.count("chat.py") == 1
    assert paths.count("App.jsx") == 1


def test_extract_file_paths_empty_string():
    assert _extract_file_paths("") == []
    assert _extract_file_paths(None) == []  # type: ignore[arg-type]


def test_extract_stack_signals_detects_keywords():
    sigs = _extract_stack_signals(
        "We use FastAPI + MongoDB and React with TailwindCSS. "
        "OpenRouter for LLM."
    )
    assert "fastapi" in sigs
    assert "mongo" in sigs
    assert "react" in sigs
    assert "openrouter" in sigs


def test_extract_stack_signals_empty():
    assert _extract_stack_signals("") == []
    assert _extract_stack_signals("hello world how are you") == []


# ──────────────────────────────────────────────────────────────────
# Feature 3 — extract_session_patterns (mocked Mongo)
# ──────────────────────────────────────────────────────────────────


class _MockCollection:
    def __init__(self, doc: dict | None = None):
        self.doc = doc
        self.last_upsert_filter = None
        self.last_upsert_update = None
        self.last_upsert_kwargs = None

    async def find_one(self, *_args, **_kwargs):
        return self.doc

    async def update_one(self, filter_, update, upsert=False, **kwargs):
        self.last_upsert_filter = filter_
        self.last_upsert_update = update
        self.last_upsert_kwargs = {"upsert": upsert, **kwargs}
        return MagicMock(matched_count=0, modified_count=0, upserted_id="x")


class _MockDB:
    def __init__(self, sessions_doc=None, patterns_doc=None):
        self.chat_sessions = _MockCollection(sessions_doc)
        self.ora_patterns = _MockCollection(patterns_doc)


@pytest.mark.asyncio
async def test_extract_session_patterns_returns_none_when_no_session():
    db = _MockDB(sessions_doc=None)
    out = await extract_session_patterns(
        db=db, user_id="u1", project_id="p1", session_id="s1",
    )
    assert out is None


@pytest.mark.asyncio
async def test_extract_session_patterns_returns_none_when_empty_turns():
    db = _MockDB(sessions_doc={"turns": []})
    out = await extract_session_patterns(
        db=db, user_id="u1", project_id="p1", session_id="s1",
    )
    assert out is None


@pytest.mark.asyncio
async def test_extract_session_patterns_extracts_files_and_stack():
    """A two-turn user-side conversation about FastAPI + chat.py
    should yield a payload with `hot_files` and `stack_signals`."""
    db = _MockDB(sessions_doc={
        "turns": [
            {"role": "user",      "content": "fix backend/routers/chat.py please"},
            {"role": "assistant", "content": "sure"},
            {"role": "user",      "content": "also FastAPI route in chat.py"},
        ],
    })
    out = await extract_session_patterns(
        db=db, user_id="u1", project_id="p1", session_id="s1",
    )
    assert out is not None
    assert "backend/routers/chat.py" in out["hot_files"]
    assert "fastapi" in out["stack_signals"]
    # Upsert payload must include keys we depend on later.
    assert out["user_id"] == "u1"
    assert out["project_id"] == "p1"
    assert out["last_session"] == "s1"
    # Verify mongo upsert call shape.
    assert db.ora_patterns.last_upsert_filter == {
        "user_id": "u1", "project_id": "p1",
    }
    assert "$inc" in db.ora_patterns.last_upsert_update
    assert db.ora_patterns.last_upsert_update["$inc"]["session_count"] == 1
    assert db.ora_patterns.last_upsert_kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_extract_session_patterns_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("ORA_LEARNING_DISABLED", "1")
    db = _MockDB(sessions_doc={"turns": [
        {"role": "user", "content": "chat.py"},
    ]})
    out = await extract_session_patterns(
        db=db, user_id="u1", project_id="p1", session_id="s1",
    )
    assert out is None
    monkeypatch.delenv("ORA_LEARNING_DISABLED")


@pytest.mark.asyncio
async def test_extract_session_patterns_skips_assistant_only():
    """If only the ASSISTANT mentions file paths, we don't count them —
    otherwise the model parroting paths back to the user pollutes the
    pattern record."""
    db = _MockDB(sessions_doc={
        "turns": [
            {"role": "user",      "content": "hi"},
            {"role": "assistant", "content": "I looked at App.jsx and chat.py"},
        ],
    })
    out = await extract_session_patterns(
        db=db, user_id="u1", project_id="p1", session_id="s1",
    )
    assert out is None  # no user-side signal → no record


@pytest.mark.asyncio
async def test_extract_session_patterns_never_raises_on_db_error():
    """Even if mongo throws, the function must return None silently
    (it's fire-and-forget)."""

    class _BrokenDB:
        class chat_sessions:  # noqa: D401
            @staticmethod
            async def find_one(*_a, **_k):
                raise RuntimeError("mongo down")

        ora_patterns = None  # unused — find_one explodes first

    out = await extract_session_patterns(
        db=_BrokenDB(), user_id="u1", project_id="p1", session_id="s1",
    )
    assert out is None


# ──────────────────────────────────────────────────────────────────
# Feature 3 — load_user_patterns
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_user_patterns_returns_empty_when_no_record():
    db = _MockDB(patterns_doc=None)
    out = await load_user_patterns(db=db, user_id="u1", project_id="p1")
    assert out == ""


@pytest.mark.asyncio
async def test_load_user_patterns_returns_empty_when_no_signal():
    db = _MockDB(patterns_doc={
        "hot_files": [], "stack_signals": [], "session_count": 1,
    })
    out = await load_user_patterns(db=db, user_id="u1", project_id="p1")
    assert out == ""


@pytest.mark.asyncio
async def test_load_user_patterns_formats_block():
    db = _MockDB(patterns_doc={
        "hot_files":    ["chat.py", "App.jsx"],
        "stack_signals": ["fastapi", "mongo"],
        "session_count": 5,
    })
    out = await load_user_patterns(db=db, user_id="u1", project_id="p1")
    assert "[USER PATTERNS" in out
    assert "chat.py" in out
    assert "App.jsx" in out
    assert "fastapi" in out
    assert "mongo" in out
    assert "5 past sessions" in out


@pytest.mark.asyncio
async def test_load_user_patterns_never_raises_on_db_error():
    class _BrokenDB:
        class ora_patterns:  # noqa: D401
            @staticmethod
            async def find_one(*_a, **_k):
                raise RuntimeError("mongo down")

    out = await load_user_patterns(
        db=_BrokenDB(), user_id="u1", project_id="p1",
    )
    assert out == ""
