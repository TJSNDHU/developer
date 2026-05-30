"""
services/github_oauth.py — AUREM Dev
GitHub OAuth flow + read-only helpers (user info, repo list).
"""
from __future__ import annotations
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _env(k: str) -> str:
    return os.getenv(k, "")


def client_id() -> str:    return _env("GITHUB_OAUTH_CLIENT_ID")
def client_secret() -> str: return _env("GITHUB_OAUTH_CLIENT_SECRET")
def redirect_uri() -> str:  return _env("GITHUB_REDIRECT_URI")

SCOPES = "repo,read:user"


def auth_url(state: str) -> str:
    return (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id()}"
        f"&redirect_uri={redirect_uri()}"
        f"&scope={SCOPES}"
        f"&state={state}"
    )


async def exchange(code: str) -> str:
    """Exchange OAuth `code` for an access_token. Raises on error."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "code": code,
                "redirect_uri": redirect_uri(),
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            raise ValueError(d.get("error_description", "OAuth failed"))
        return d["access_token"]


async def gh_user(token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        r.raise_for_status()
        return r.json()


async def gh_repos(token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            params={"sort": "updated", "per_page": 30, "type": "owner"},
        )
        r.raise_for_status()
        return r.json()
