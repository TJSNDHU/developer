"""Iter 212m-192 — /cto/projects/connection-status must probe the
GitHub *contents* endpoint, not the *metadata* endpoint.

Rationale: Ask Advisor tools (`read_repo_file`, `list_repo_files`,
`search_repo`) call `GET /repos/{owner}/{repo}/contents/{path}`. The
old health-check hit `GET /repos/{owner}/{repo}` which is a metadata
endpoint — it returns 200 even for tokens that lack the `Contents:
Read` scope for private repos. Result: sidebar showed a green
"connected" dot while every actual tool call returned 401 in chat.

This test locks the new behaviour so future refactors can't
regress the fix (users get lied to → founder-visibility bug).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import repo_status  # noqa: E402


def test_check_one_hits_contents_endpoint_not_metadata():
    """The probe URL must include `/contents/` so the health-check
    validates the same scope the tools rely on."""
    src = Path(repo_status.__file__).read_text(encoding="utf-8")
    # Locate the probe URL template inside `_check_one`.
    idx = src.find("async def _check_one(")
    assert idx != -1, "_check_one function should exist"
    block = src[idx: idx + 2500]

    assert "/contents/" in block, (
        "_check_one must probe the contents endpoint so a green dot "
        "reflects real file-read permission, not just metadata access."
    )
    # Guard against a partial fix that keeps BOTH endpoints — the
    # metadata form (`/repos/{owner}/{repo}"`) without the trailing
    # `/contents/` should not appear as the probe URL inside the
    # function body.
    assert 'f"https://api.github.com/repos/{owner}/{repo}"' not in block, (
        "The metadata-only probe URL must not remain — it lies about "
        "connectivity when the token lacks Contents:Read scope."
    )


def test_docstring_documents_the_fix():
    """Guard: the fix rationale must live in the code so a future
    reader understands WHY we chose contents/ over the metadata
    endpoint (and doesn't 'optimise' back to the cheaper call)."""
    src = Path(repo_status.__file__).read_text(encoding="utf-8")
    assert "212m-192" in src, "Iter tag must be preserved for context"
    assert "contents" in src.lower()
    assert "Ask Advisor" in src or "read_repo_file" in src
