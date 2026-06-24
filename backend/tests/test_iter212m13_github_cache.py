"""Iter 212m-13 — GitHub API short-TTL cache.

Locks the contract that:

  • Repeated `_fetch_file` / `_fetch_tree` calls for the same
    (owner, repo, path, branch, token) inside the TTL hit the
    cache instead of GitHub — eliminating the dominant duplicate-
    fetch cost in repo-aware chat turns.
  • Different tokens get different cache entries (no cross-user
    leakage).
  • Empty / None results are NOT cached (a 404 today can succeed
    immediately when the file lands).
  • `invalidate_repo(...)` after a write drops every entry for
    that repo so the LLM can never read its own stale body.
  • TTL expiry actually expires.
  • LRU bound holds.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import github_cache  # noqa: E402
from services import repo_context  # noqa: E402


# ── github_cache unit tests ───────────────────────────────────────


def setup_function(_fn):
    github_cache.clear()


def test_file_cache_round_trip():
    k = github_cache.file_key("o", "r", "main.py", "main", "tok")
    assert github_cache.get_file(k) is None
    github_cache.set_file(k, "print('hi')")
    assert github_cache.get_file(k) == "print('hi')"


def test_tree_cache_round_trip():
    k = github_cache.tree_key("o", "r", "main", "tok")
    assert github_cache.get_tree(k) is None
    github_cache.set_tree(k, ([{"path": "a.py"}], False))
    assert github_cache.get_tree(k) == ([{"path": "a.py"}], False)


def test_different_tokens_get_different_entries():
    k1 = github_cache.file_key("o", "r", "main.py", "main", "tok_user1")
    k2 = github_cache.file_key("o", "r", "main.py", "main", "tok_user2")
    assert k1 != k2
    github_cache.set_file(k1, "user1 content")
    assert github_cache.get_file(k2) is None  # user2 sees a miss
    github_cache.set_file(k2, "user2 content")
    assert github_cache.get_file(k1) == "user1 content"
    assert github_cache.get_file(k2) == "user2 content"


def test_empty_content_is_not_cached():
    k = github_cache.file_key("o", "r", "empty.py", "main", "tok")
    github_cache.set_file(k, "")
    assert github_cache.get_file(k) is None


def test_empty_tree_is_not_cached():
    k = github_cache.tree_key("o", "r", "main", "tok")
    github_cache.set_tree(k, ([], False))
    assert github_cache.get_tree(k) is None


def test_ttl_expiry():
    k = github_cache.file_key("o", "r", "x.py", "main", "tok")
    github_cache.set_file(k, "v1")
    # Forge an entry that expired 1 s ago.
    expires_at, value = github_cache._FILE_CACHE[k]
    github_cache._FILE_CACHE[k] = (time.time() - 1, value)
    assert github_cache.get_file(k) is None
    # The expired entry should have been popped on the miss.
    assert k not in github_cache._FILE_CACHE


def test_invalidate_repo_drops_file_and_tree():
    f1 = github_cache.file_key("o", "r1", "a.py", "main", "tok")
    f2 = github_cache.file_key("o", "r1", "b.py", "main", "tok")
    f3 = github_cache.file_key("o", "r2", "c.py", "main", "tok")  # other repo
    t1 = github_cache.tree_key("o", "r1", "main", "tok")
    t2 = github_cache.tree_key("o", "r2", "main", "tok")
    for k in (f1, f2, f3):
        github_cache.set_file(k, "x")
    github_cache.set_tree(t1, ([{"path": "x"}], False))
    github_cache.set_tree(t2, ([{"path": "y"}], False))

    dropped = github_cache.invalidate_repo("o", "r1")
    # 2 files + 1 tree = 3
    assert dropped == 3
    assert github_cache.get_file(f1) is None
    assert github_cache.get_file(f2) is None
    assert github_cache.get_file(f3) == "x"     # untouched
    assert github_cache.get_tree(t1) is None
    assert github_cache.get_tree(t2) is not None


def test_invalidate_repo_branch_scoped():
    f1 = github_cache.file_key("o", "r1", "a.py", "main", "tok")
    f2 = github_cache.file_key("o", "r1", "a.py", "dev", "tok")
    github_cache.set_file(f1, "main body")
    github_cache.set_file(f2, "dev body")
    dropped = github_cache.invalidate_repo("o", "r1", branch="main")
    assert dropped == 1
    assert github_cache.get_file(f1) is None
    assert github_cache.get_file(f2) == "dev body"


def test_lru_eviction_holds():
    # Force the bound to a small number for the test
    original_max = github_cache._MAX_ENTRIES
    github_cache._MAX_ENTRIES = 5
    try:
        for i in range(10):
            k = github_cache.file_key("o", "r", f"f{i}.py", "main", "tok")
            github_cache.set_file(k, f"content-{i}")
        # Only the last 5 should survive
        assert len(github_cache._FILE_CACHE) == 5
        # Oldest (f0..f4) evicted, newest (f5..f9) kept
        assert github_cache.get_file(github_cache.file_key("o", "r", "f0.py", "main", "tok")) is None
        assert github_cache.get_file(github_cache.file_key("o", "r", "f9.py", "main", "tok")) == "content-9"
    finally:
        github_cache._MAX_ENTRIES = original_max


# ── repo_context integration: cache hits skip the network ─────────


@pytest.mark.asyncio
async def test_fetch_file_uses_cache_on_second_call():
    github_cache.clear()

    # Mock the httpx.AsyncClient so we can count GitHub calls.
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "encoding": "base64",
        # base64("hello world")
        "content":  "aGVsbG8gd29ybGQ=",
    })
    mock_get = AsyncMock(return_value=mock_response)
    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        a = await repo_context._fetch_file("o", "r", "a.py", "main", "tok")
        b = await repo_context._fetch_file("o", "r", "a.py", "main", "tok")
    assert a == "hello world"
    assert b == "hello world"
    # Only ONE httpx GET should have happened.
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_fetch_tree_uses_cache_on_second_call():
    github_cache.clear()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "tree": [{"path": "x.py"}, {"path": "y.py"}],
        "truncated": False,
    })
    mock_get = AsyncMock(return_value=mock_response)
    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        a = await repo_context._fetch_tree("o", "r", "main", "tok")
        b = await repo_context._fetch_tree("o", "r", "main", "tok")
    assert a == b == ([{"path": "x.py"}, {"path": "y.py"}], False)
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_fetch_file_failure_does_not_poison_cache():
    """A network error must NOT leak into the cache — the next
    call should retry rather than silently return None."""
    github_cache.clear()

    # First call: explodes. Second call: succeeds.
    mock_response_ok = MagicMock()
    mock_response_ok.raise_for_status = MagicMock()
    mock_response_ok.json = MagicMock(return_value={
        "encoding": "base64",
        "content":  "aGVsbG8=",   # base64("hello")
    })
    mock_get = AsyncMock(side_effect=[
        RuntimeError("boom"),
        mock_response_ok,
    ])
    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        first = await repo_context._fetch_file("o", "r", "a.py", "main", "tok")
        second = await repo_context._fetch_file("o", "r", "a.py", "main", "tok")
    assert first is None
    assert second == "hello"
    assert mock_get.call_count == 2


# ── Stats endpoint contract ───────────────────────────────────────


def test_stats_shape():
    s = github_cache.stats()
    assert "file_entries" in s
    assert "tree_entries" in s
    assert "ttl_seconds" in s
    assert "max_entries" in s
    assert isinstance(s["file_entries"], int)
    assert isinstance(s["ttl_seconds"], (int, float))
