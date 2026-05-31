"""
test_vault_and_indexer.py — Iter 31 smoke tests for the imported
modules.

vault.py tests run only when AUREM_MASTER_KEY is set; otherwise we
assert the fail-closed contract.
"""
from __future__ import annotations

import os

import pytest

from services.vault import (
    encrypt, decrypt, is_vault_available, _derive_customer_key,
)
from services.codebase_indexer import (
    _detect_lang, _detect_role, _parse_repo_url, _format_context_block,
)


# ─── codebase_indexer (pure unit) ────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("backend/routers/auth.py",      "routes"),
    ("backend/main.py",               "other"),
    ("backend/server.py",             "routes"),
    ("app/models/user.py",            "models"),
    ("app/schemas/order.py",          "models"),
    ("frontend/src/components/x.jsx", "components"),
    ("frontend/src/pages/y.tsx",      "components"),
    ("requirements.txt",              "deps"),
    ("package.json",                  "deps"),
    ("README.md",                     "other"),
])
def test_role_detection(path, expected):
    assert _detect_role(path) == expected


@pytest.mark.parametrize("path,expected", [
    ("a.py",     "python"),
    ("b.jsx",    "js"),
    ("c.tsx",    "js"),
    ("d.json",   "json"),
    ("e.md",     "md"),
    ("f.yaml",   "yaml"),
    ("g.txt",    "other"),
])
def test_lang_detection(path, expected):
    assert _detect_lang(path) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/me/repo",          ("me", "repo")),
    ("https://github.com/me/repo.git",      ("me", "repo")),
    ("git@github.com:me/repo.git",          ("me", "repo")),
    ("github.com/teji/aurem",               ("teji", "aurem")),
])
def test_parse_repo_url(url, expected):
    assert _parse_repo_url(url) == expected


def test_parse_repo_url_invalid_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _parse_repo_url("not a url")
    assert ei.value.status_code == 400


def test_format_context_block_trims_and_groups():
    doc = {
        "repo_owner": "me", "repo_name": "demo",
        "default_branch": "main", "file_count": 3,
        "deps": {"python": ["fastapi", "pydantic"], "node": []},
        "files": [
            {"path": "routers/auth.py", "role": "routes", "lang": "python", "snippet": "def login(): pass"},
            {"path": "models/user.py", "role": "models", "lang": "python", "snippet": "class User: pass"},
            {"path": "components/Btn.jsx", "role": "components", "lang": "js", "snippet": "export const Btn=()=>null"},
        ],
    }
    block = _format_context_block(doc, max_chars=4000)
    assert "routers/auth.py" in block
    assert "models/user.py" in block
    assert "components/Btn.jsx" in block
    assert "ROUTES" in block
    assert "MODELS" in block
    assert "COMPONENTS" in block
    # Hard cap honoured
    short = _format_context_block(doc, max_chars=100)
    assert len(short) <= 100 + len("\n…(context trimmed)")


# ─── vault — fail-closed + roundtrip ─────────────────────────────────

def test_is_vault_available_returns_bool():
    assert isinstance(is_vault_available(), bool)


@pytest.mark.skipif(is_vault_available(),
                    reason="vault is configured — skip fail-closed test")
@pytest.mark.asyncio
async def test_encrypt_raises_when_no_master_key():
    with pytest.raises(RuntimeError, match="AUREM_MASTER_KEY"):
        await encrypt("user-1", "secret value")


@pytest.mark.skipif(not is_vault_available(),
                    reason="vault not configured")
def test_per_user_keys_differ():
    """HKDF must give two users genuinely different keys."""
    k1 = _derive_customer_key("alice")
    k2 = _derive_customer_key("bob")
    assert k1 != k2


@pytest.mark.skipif(not is_vault_available(),
                    reason="vault not configured")
@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip():
    ct = await encrypt("user-1", "my-github-pat-abc")
    assert ct.startswith("v1:")
    pt = await decrypt("user-1", ct)
    assert pt == "my-github-pat-abc"


@pytest.mark.skipif(not is_vault_available(),
                    reason="vault not configured")
@pytest.mark.asyncio
async def test_decrypt_with_wrong_user_fails():
    """A leaked ciphertext from user-1 must not decrypt under user-2."""
    from cryptography.fernet import InvalidToken
    ct = await encrypt("user-1", "secret")
    with pytest.raises(InvalidToken):
        await decrypt("user-2", ct)
