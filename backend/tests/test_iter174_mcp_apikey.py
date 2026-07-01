"""
Iter 174 — MCP well-known discovery + API-key auth.

Covers:
  GET /.well-known/mcp (via router)         — discovery doc
  GET /.well-known/mcp (root alias)         — same doc
  POST /mcp/keys                            — mint sk-aurem-… key
  GET  /mcp/keys                            — list with masking
  DELETE /mcp/keys/{tail}                   — revoke by tail
  Auth via `sk-aurem-…` → tools work
  Revoked / bogus keys → JSON-RPC -32001
  API-key holders cannot mint NEW keys (403)
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


def _client(seed_api_key_doc=None):
    """Boot the app with stubbed auth + Mongo. The api_keys collection
    is wired against an in-memory dict so we can exercise mint / list /
    revoke without a real Mongo."""
    from main import app
    from routers import mcp as mcp_mod

    async def fake_current_dev(authorization=None):
        if not authorization:
            raise PermissionError("no auth")
        # JWT path — return a fixed user
        return {"user_id": "u_test", "email": "t@t.t"}
    mcp_mod.current_dev = fake_current_dev

    store: dict[str, dict] = {}
    if seed_api_key_doc:
        store[seed_api_key_doc["key"]] = seed_api_key_doc

    fake_db = MagicMock()

    async def find_one(q, *_a, **_kw):
        # Match by key + optional active filter
        key = q.get("key")
        active_q = q.get("active")
        rec = store.get(key) if key else None
        if rec and active_q is not None and bool(rec.get("active")) != active_q:
            return None
        return rec

    async def insert_one(doc):
        store[doc["key"]] = doc
        return MagicMock(inserted_id="x")

    async def update_one(q, upd):
        key = q.get("key")
        if key in store:
            store[key].update(upd.get("$set", {}))
            return MagicMock(modified_count=1)
        return MagicMock(modified_count=0)

    async def update_many(q, upd):
        # Cheap: iterate
        n = 0
        # Real regex emulation: trailing match
        rx = q.get("key", {})
        suffix = ""
        if isinstance(rx, dict):
            pat = rx.get("$regex") or ""
            suffix = pat.rstrip("$")
        for k, v in store.items():
            if (v.get("user_id") == q.get("user_id")
                    and v.get("active") == q.get("active")
                    and k.endswith(suffix)):
                v.update(upd.get("$set", {}))
                n += 1
        return MagicMock(modified_count=n)

    def find(q, _proj=None):
        # Return an async iterator
        items = [v for v in store.values() if v.get("user_id") == q.get("user_id")]

        class C:
            def __aiter__(self):
                self._it = iter(items)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration
        return C()

    fake_db.api_keys = MagicMock()
    fake_db.api_keys.find_one = AsyncMock(side_effect=find_one)
    fake_db.api_keys.insert_one = AsyncMock(side_effect=insert_one)
    fake_db.api_keys.update_one = AsyncMock(side_effect=update_one)
    fake_db.api_keys.update_many = AsyncMock(side_effect=update_many)
    fake_db.api_keys.find = find

    # cto_projects (needed by list_projects tool)
    cur = MagicMock()
    cur.sort.return_value = cur
    cur.limit.return_value = cur

    async def _empty(_self):
        return
        yield
    cur.__aiter__ = _empty
    fake_db.cto_projects = MagicMock()
    fake_db.cto_projects.find = MagicMock(return_value=cur)

    mcp_mod.get_db = lambda: fake_db
    return TestClient(app), fake_db, store


# ─────────────────────────────────────────────────────────────────────
# Discovery endpoint
# ─────────────────────────────────────────────────────────────────────
class TestWellKnown(unittest.TestCase):
    def test_well_known_under_router(self):
        client, *_ = _client()
        r = client.get("/api/aurem-dev/mcp/.well-known/mcp")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["protocol_version"], "2025-03-26")
        self.assertEqual(d["server_name"], "ORA by Aurem CTO")
        self.assertEqual(d["auth"]["type"], "bearer")
        self.assertIn("api_key", d["auth"]["formats"])
        self.assertEqual(d["auth"]["api_key_prefix"], "sk-aurem-")
        self.assertIn("auremcto.com", d["mcp_endpoint"])

    def test_well_known_root_alias(self):
        client, *_ = _client()
        r = client.get("/.well-known/mcp")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["protocol_version"], "2025-03-26")
        self.assertEqual(d["server_name"], "ORA by Aurem CTO")


# ─────────────────────────────────────────────────────────────────────
# Key creation / listing / revocation
# ─────────────────────────────────────────────────────────────────────
class TestKeyLifecycle(unittest.TestCase):
    def test_mint_key_with_jwt(self):
        client, _db, store = _client()
        r = client.post(
            "/api/aurem-dev/mcp/keys",
            headers={"Authorization": "Bearer jwt-token-here"},
        )
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["key"].startswith("sk-aurem-"))
        # Persisted with active=true, user_id=u_test
        self.assertIn(d["key"], store)
        self.assertTrue(store[d["key"]]["active"])
        self.assertEqual(store[d["key"]]["user_id"], "u_test")

    def test_mint_without_auth_returns_401(self):
        client, *_ = _client()
        r = client.post("/api/aurem-dev/mcp/keys")
        self.assertEqual(r.status_code, 401)

    def test_api_key_cannot_mint_new_keys(self):
        # Seed an existing key so the bearer is valid for tools but
        # the /keys endpoint must still refuse.
        key = "sk-aurem-existingkey"
        client, _db, _store = _client(seed_api_key_doc={
            "key": key, "user_id": "u_test", "active": True,
        })
        r = client.post(
            "/api/aurem-dev/mcp/keys",
            headers={"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("cannot mint", r.json()["detail"])

    def test_list_keys_returns_masked(self):
        key = "sk-aurem-abcdefghijklmnop"
        client, *_ = _client(seed_api_key_doc={
            "key": key, "user_id": "u_test", "active": True,
            "label": "test", "created_at": 1.0,
        })
        r = client.get(
            "/api/aurem-dev/mcp/keys",
            headers={"Authorization": "Bearer jwt"},
        )
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["count"], 1)
        masked = d["keys"][0]["key_masked"]
        # Must NOT contain the full secret tail body
        self.assertNotIn("efghij", masked)
        # Should preserve the prefix
        self.assertTrue(masked.startswith("sk-aurem-"))

    def test_revoke_by_tail(self):
        key = "sk-aurem-abcdefghijklmnopABCD"
        client, _db, store = _client(seed_api_key_doc={
            "key": key, "user_id": "u_test", "active": True,
        })
        r = client.delete(
            "/api/aurem-dev/mcp/keys/ABCD",
            headers={"Authorization": "Bearer jwt"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["revoked"], 1)
        self.assertFalse(store[key]["active"])


# ─────────────────────────────────────────────────────────────────────
# Auth resolution: API key path
# ─────────────────────────────────────────────────────────────────────
class TestApiKeyAuth(unittest.TestCase):
    def test_active_key_authorizes_tools_list(self):
        key = "sk-aurem-livekey"
        client, *_ = _client(seed_api_key_doc={
            "key": key, "user_id": "u_test", "active": True,
        })
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": f"Bearer {key}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        d = r.json()
        self.assertNotIn("error", d)
        # Iter 212m-174 — 12 tools now (was 4).
        self.assertGreaterEqual(len(d["result"]["tools"]), 12)

    def test_revoked_key_rejected_with_rpc_unauthorized(self):
        key = "sk-aurem-revokedkey"
        client, *_ = _client(seed_api_key_doc={
            "key": key, "user_id": "u_test", "active": False,
        })
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": f"Bearer {key}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        d = r.json()
        self.assertEqual(d["error"]["code"], -32001)
        self.assertIn("invalid or revoked", d["error"]["message"])

    def test_bogus_key_rejected(self):
        client, *_ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer sk-aurem-doesnotexist"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        d = r.json()
        self.assertEqual(d["error"]["code"], -32001)

    def test_jwt_path_still_works(self):
        # Non-`sk-aurem-` token falls through to current_dev (stubbed)
        client, *_ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer some-jwt-token"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        d = r.json()
        self.assertNotIn("error", d)


if __name__ == "__main__":
    unittest.main()
