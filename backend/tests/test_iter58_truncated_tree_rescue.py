"""
tests/test_iter58_truncated_tree_rescue.py
============================================

Iter 58 — Route fix for "AUREM repo properly scan kyon nahi karta"
when the user's repo is large enough that GitHub silently truncates
its recursive Trees API response.

GitHub's `git/trees/{sha}?recursive=1` endpoint sets `"truncated":
true` and returns only a *partial* tree for any repo over ~7MB or
~100K entries. Before this iter, `list_repo_files`, `search_repo`, and
the initial repo context build all read the truncated tree and never
checked the flag — so deep folders (like the user's
`backend/pillars/`) silently vanished, and ORA correctly concluded
"the folder doesn't exist" based on the partial data it had.

The fix is a per-folder Contents-API walk fallback that rescues the
missing subtree on demand.
"""
from __future__ import annotations
import os
import inspect


def _read(rel: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", rel)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ─── Helpers in place ───────────────────────────────────────────────────

def test_local_tools_has_subtree_fallback_helper():
    """The Contents-API BFS walker must exist and be async."""
    from services.local_tools import _fetch_subtree_contents
    assert inspect.iscoroutinefunction(_fetch_subtree_contents)
    sig = inspect.signature(_fetch_subtree_contents)
    for kw in ("owner", "repo", "branch", "token", "path"):
        assert kw in sig.parameters, f"helper missing kwarg {kw}"


def test_repo_context_has_subtree_fallback_helper():
    """Mirror helper must exist in repo_context.py to avoid a circular
    import with local_tools."""
    from services.repo_context import _fetch_subtree_contents
    assert inspect.iscoroutinefunction(_fetch_subtree_contents)


# ─── _fetch_tree now returns (tree, gh_truncated) ──────────────────────

def test_fetch_tree_returns_truncation_flag():
    """The internal fetch must expose GitHub's `truncated` flag so the
    caller can rescue the missing subtree."""
    from services.repo_context import _fetch_tree
    assert inspect.iscoroutinefunction(_fetch_tree)
    # We can't easily call GitHub from a unit test — but the body must
    # be returning a tuple now, not a bare list.
    src = inspect.getsource(_fetch_tree)
    assert "return (data.get(\"tree\") or []), bool(data.get(\"truncated\"))" in src, (
        "regression — _fetch_tree must return (tree, gh_truncated) "
        "so callers can trigger the per-folder rescue walk."
    )


# ─── list_repo_files surfaces truncation + falls back on path ──────────

def test_list_repo_files_handles_gh_truncated_with_path():
    """Source-level pin: when GitHub returns truncated=True AND the
    caller passed a `path`, the function must call the Contents-API
    walker for that subtree."""
    src = _read("services/local_tools.py")
    # Smoking-gun: the rescue branch must call _fetch_subtree_contents
    # inside list_repo_files when gh_truncated and not filtered.
    assert "gh_truncated and not filtered" in src
    assert "_fetch_subtree_contents(" in src
    # And the response must include the new flag so ORA can see it.
    assert '"gh_truncated"' in src or "'gh_truncated'" in src


def test_search_repo_also_uses_truncation_rescue():
    """Same rescue must apply to search_repo so a `pattern=...` lookup
    inside a deep subtree on a large repo doesn't return zero hits."""
    src = _read("services/local_tools.py")
    # The search_repo function must reference the helper too. Count
    # ensures it's wired in BOTH list_repo_files AND search_repo.
    assert src.count("_fetch_subtree_contents(") >= 3, (
        "_fetch_subtree_contents must be wired in (1) its own definition "
        "+ (2) list_repo_files + (3) search_repo. Found only "
        f"{src.count('_fetch_subtree_contents(')} references."
    )


# ─── repo_context_build_blob rescues truncated initial tree ────────────

def test_repo_context_build_blob_rescues_on_truncation():
    """The initial system-prompt repo briefing also rescues missing
    folders so users with large repos don't get a 'half-empty' tree."""
    src = _read("services/repo_context.py")
    # Both pieces of the rescue must be present:
    assert "gh_truncated" in src
    # Iterates the top-level dirs we DID see and walks them.
    assert "_fetch_subtree_contents(" in src
    # Surfaces a note so ORA tells the user the tree was reconstructed.
    assert "auto-rescued" in src


# ─── Backwards-compat: small repos still work without rescue ───────────

def test_small_repo_does_not_trigger_rescue():
    """When `truncated: False`, the rescue branch must NOT run — small
    repos must not pay for an unnecessary BFS walk."""
    src = _read("services/repo_context.py")
    # The rescue is guarded by `if gh_truncated:` — if that guard
    # disappears, every chat will spam GitHub Contents calls.
    assert "if gh_truncated:" in src


# ─── Note surfaced to ORA so it knows the data is partial ──────────────

def test_list_repo_files_response_warns_about_truncation():
    src = _read("services/local_tools.py")
    # ORA needs to see this warning so it knows to re-call with a `path`
    # arg instead of concluding "folder doesn't exist".
    assert "GitHub truncated this recursive tree response" in src
    assert "re-call with `path=" in src
