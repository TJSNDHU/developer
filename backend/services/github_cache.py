"""Iter 212m-13 — GitHub API response cache (in-memory, per-process).

Closes the most common latency leak in repo-aware chat turns:

When the LLM plans a multi-step answer it routinely calls the same
`read_repo_file` (or `list_repo_files`) more than once in a single
turn (e.g. read auth.py to scope the problem → read auth.py again
to draft a patch). Without a cache each call costs a fresh
GitHub round-trip (~150-400 ms for files, ~300-800 ms for the
recursive tree). A 4-tool-call turn was eating ~2 s just on
duplicate fetches.

This module wraps the two hot functions in `repo_context._fetch_*`
with a tiny TTL+LRU cache. Cache is process-local — no Redis
dependency — because chat turns are short-lived (~30 s) and the
volume is low (a few hundred entries / minute / pod).

Cache contract:
  • Key includes a token hash so two users hitting the same public
    repo never share each other's data.
  • TTL = 90 s — long enough to cover the longest plausible chat
    turn, short enough that the founder pulling a new branch sees
    fresh content within a single "ask, see, ask" loop.
  • `invalidate_repo(...)` is called after every successful
    `write_repo_file` so an LLM that writes-then-reads the same
    file in one turn never sees a stale body.
  • `clear()` exposed for tests.

Negative results (None / errors) are NEVER cached so a 404 can be
retried as soon as the file appears.
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Optional

_TTL_SECONDS = 90.0
_MAX_ENTRIES = 500

# Two separate caches so a tree-list invalidation doesn't blow away
# every file body for the same repo.
_FILE_CACHE: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_TREE_CACHE: "OrderedDict[str, tuple[float, tuple[list[dict], bool]]]" = OrderedDict()


def _token_hash(token: Optional[str]) -> str:
    """Stable 12-char prefix of sha1 so we can shard the cache per
    user-token without storing the secret itself."""
    if not token:
        return "anon"
    return hashlib.sha1(token.encode("utf-8", "replace")).hexdigest()[:12]


def _trim(cache: OrderedDict) -> None:
    while len(cache) > _MAX_ENTRIES:
        cache.popitem(last=False)


def _now() -> float:
    return time.time()


# ── Public API ────────────────────────────────────────────────────


def file_key(owner: str, repo: str, path: str, branch: str,
             token: Optional[str]) -> str:
    return f"f::{owner}/{repo}@{branch}::{path}::{_token_hash(token)}"


def tree_key(owner: str, repo: str, branch: str,
             token: Optional[str]) -> str:
    return f"t::{owner}/{repo}@{branch}::{_token_hash(token)}"


def get_file(key: str) -> Optional[str]:
    """Return cached file content or None on miss/expiry."""
    hit = _FILE_CACHE.get(key)
    if not hit:
        return None
    expires_at, value = hit
    if expires_at < _now():
        _FILE_CACHE.pop(key, None)
        return None
    # Touch for LRU
    _FILE_CACHE.move_to_end(key)
    return value


def set_file(key: str, content: str) -> None:
    """Cache a successful file read."""
    if not content:
        return
    _FILE_CACHE[key] = (_now() + _TTL_SECONDS, content)
    _FILE_CACHE.move_to_end(key)
    _trim(_FILE_CACHE)


def get_tree(key: str) -> Optional[tuple[list[dict], bool]]:
    hit = _TREE_CACHE.get(key)
    if not hit:
        return None
    expires_at, value = hit
    if expires_at < _now():
        _TREE_CACHE.pop(key, None)
        return None
    _TREE_CACHE.move_to_end(key)
    return value


def set_tree(key: str, value: tuple[list[dict], bool]) -> None:
    tree, _truncated = value
    if not tree:
        return
    _TREE_CACHE[key] = (_now() + _TTL_SECONDS, value)
    _TREE_CACHE.move_to_end(key)
    _trim(_TREE_CACHE)


def invalidate_repo(owner: str, repo: str, branch: Optional[str] = None) -> int:
    """Drop every cached entry for a repo (file + tree) after a
    successful commit. Returns the count of dropped entries so
    tests can lock the contract.

    When `branch` is None, invalidates ALL branches for the repo
    so a commit on `main` also clears reads against `feat/x` if
    those happened to be aliased to the same SHA."""
    dropped = 0
    prefix_f = f"f::{owner}/{repo}"
    prefix_t = f"t::{owner}/{repo}"
    if branch:
        prefix_f = f"{prefix_f}@{branch}::"
        prefix_t = f"{prefix_t}@{branch}::"
    for cache in (_FILE_CACHE, _TREE_CACHE):
        bad = [k for k in cache if k.startswith(prefix_f) or k.startswith(prefix_t)]
        for k in bad:
            cache.pop(k, None)
            dropped += 1
    return dropped


def clear() -> None:
    """Test-only — drops every cache entry."""
    _FILE_CACHE.clear()
    _TREE_CACHE.clear()


def stats() -> dict:
    """Snapshot for the admin /vanguard or metrics dashboard."""
    return {
        "file_entries": len(_FILE_CACHE),
        "tree_entries": len(_TREE_CACHE),
        "ttl_seconds":  _TTL_SECONDS,
        "max_entries":  _MAX_ENTRIES,
    }


__all__ = [
    "file_key", "tree_key",
    "get_file", "set_file",
    "get_tree", "set_tree",
    "invalidate_repo", "clear", "stats",
]
