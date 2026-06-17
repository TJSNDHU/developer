"""
Iter 173 — MCP (Model Context Protocol) server endpoint.

Covers:
  GET  /mcp                — manifest + tool catalogue
  POST /mcp initialize     — JSON-RPC server info (no auth required)
  POST /mcp tools/list     — requires auth
  POST /mcp tools/call     — dispatch to 4 tools, error paths, batches
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


def _client(seed_projects=None, seed_task=None):
    """Boot the app with stubbed auth + Mongo so we never touch a real
    DB or hit GitHub from tests."""
    from main import app
    from routers import mcp as mcp_mod

    async def fake_current_dev(_authorization=None):
        if not _authorization:
            raise PermissionError("no auth")
        return {"user_id": "u_test", "email": "t@t.t"}
    mcp_mod.current_dev = fake_current_dev

    fake_db = MagicMock()
    # Projects cursor
    fake_cursor = MagicMock()
    fake_cursor.sort.return_value = fake_cursor
    fake_cursor.limit.return_value = fake_cursor

    async def _async_iter(_self):
        for p in (seed_projects or []):
            yield p
    fake_cursor.__aiter__ = _async_iter
    fake_db.cto_projects = MagicMock()
    fake_db.cto_projects.find = MagicMock(return_value=fake_cursor)
    fake_db.cto_projects.find_one = AsyncMock(return_value=(seed_projects or [None])[0])

    fake_db.cto_tasks = MagicMock()
    fake_db.cto_tasks.find_one = AsyncMock(return_value=seed_task)

    mcp_mod.get_db = lambda: fake_db
    return TestClient(app), fake_db


# ─────────────────────────────────────────────────────────────────────
# GET /mcp manifest
# ─────────────────────────────────────────────────────────────────────
class TestManifest(unittest.TestCase):
    def test_get_returns_manifest_with_tools(self):
        client, _ = _client()
        r = client.get("/api/aurem-dev/mcp")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["protocolVersion"], "2025-03-26")
        self.assertEqual(data["serverInfo"]["name"], "aurem-cto")
        self.assertEqual(data["transport"], "streamable-http")
        names = sorted(t["name"] for t in data["tools"])
        self.assertEqual(names, sorted([
            "list_projects", "ship_code", "get_task_status", "get_recent_commits",
        ]))
        for t in data["tools"]:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertEqual(t["inputSchema"]["type"], "object")


# ─────────────────────────────────────────────────────────────────────
# JSON-RPC behaviour
# ─────────────────────────────────────────────────────────────────────
class TestInitialize(unittest.TestCase):
    def test_initialize_does_not_require_auth(self):
        client, _ = _client()
        r = client.post("/api/aurem-dev/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
        })
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["jsonrpc"], "2.0")
        self.assertEqual(d["id"], 1)
        self.assertEqual(d["result"]["protocolVersion"], "2025-03-26")
        self.assertNotIn("error", d)


class TestAuthGate(unittest.TestCase):
    def test_tools_list_without_auth_returns_unauthorized(self):
        client, _ = _client()
        r = client.post("/api/aurem-dev/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        })
        self.assertEqual(r.status_code, 200)   # JSON-RPC errors are still 200
        d = r.json()
        self.assertEqual(d["error"]["code"], -32001)
        self.assertIn("Authorization", d["error"]["message"])

    def test_tools_list_with_auth(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        )
        d = r.json()
        self.assertNotIn("error", d)
        self.assertEqual(len(d["result"]["tools"]), 4)


# ─────────────────────────────────────────────────────────────────────
# tools/call dispatch
# ─────────────────────────────────────────────────────────────────────
class TestToolCalls(unittest.TestCase):
    def test_list_projects(self):
        client, _ = _client(seed_projects=[{
            "project_id": "p1", "name": "demo", "user_id": "u_test",
            "github_owner": "o", "github_repo": "r", "branch": "main",
            "tasks_count": 3, "last_task": 1.0, "created_at": 0.5,
        }])
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "list_projects", "arguments": {"limit": 5}}},
        )
        d = r.json()
        self.assertNotIn("error", d)
        self.assertFalse(d["result"]["isError"])
        self.assertEqual(d["result"]["data"]["count"], 1)
        self.assertEqual(d["result"]["data"]["projects"][0]["project_id"], "p1")
        # `content` array MCP-spec shape
        self.assertEqual(d["result"]["content"][0]["type"], "text")
        parsed = json.loads(d["result"]["content"][0]["text"])
        self.assertEqual(parsed["count"], 1)

    def test_get_task_status_found(self):
        seed = {
            "task_id": "t_abc", "user_id": "u_test", "project_id": "p1",
            "status": "done", "commit_sha": "deadbeef",
            "task": "x", "error": None, "created_at": 1.0,
            "steps": [{"kind": "ok", "msg": "done"}],
        }
        client, _ = _client(seed_task=seed)
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "get_task_status",
                             "arguments": {"task_id": "t_abc"}}},
        )
        d = r.json()
        self.assertEqual(d["result"]["data"]["task_id"], "t_abc")
        self.assertEqual(d["result"]["data"]["status"], "done")
        self.assertEqual(d["result"]["data"]["commit_sha"], "deadbeef")

    def test_get_task_status_missing(self):
        client, _ = _client(seed_task=None)
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "get_task_status",
                             "arguments": {"task_id": "t_missing"}}},
        )
        d = r.json()
        self.assertEqual(d["error"]["code"], -32002)
        self.assertIn("Task not found", d["error"]["message"])

    def test_ship_code_too_short(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "ship_code",
                             "arguments": {"task": "short"}}},
        )
        d = r.json()
        # `task` is < 10 chars → ValueError → -32602 invalid params
        self.assertEqual(d["error"]["code"], -32602)
        self.assertIn("10 characters", d["error"]["message"])

    def test_ship_code_dispatches_to_enqueue(self):
        client, _ = _client()

        async def fake_enqueue(**kwargs):
            return {"ok": True, "task_id": "t_x", "project_id": "p1"}

        with patch("routers.cto_projects._enqueue_cto_task", side_effect=fake_enqueue):
            r = client.post(
                "/api/aurem-dev/mcp",
                headers={"Authorization": "Bearer x"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "ship_code",
                                 "arguments": {"task":
                                    "Add /api/health route to backend/main.py"}}},
            )
        d = r.json()
        self.assertNotIn("error", d)
        self.assertEqual(d["result"]["data"]["task_id"], "t_x")
        self.assertEqual(d["result"]["data"]["status"], "queued")

    def test_unknown_tool_returns_method_not_found(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "bogus", "arguments": {}}},
        )
        d = r.json()
        self.assertEqual(d["error"]["code"], -32601)

    def test_unknown_method(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json={"jsonrpc": "2.0", "id": 1, "method": "garbage"},
        )
        d = r.json()
        self.assertEqual(d["error"]["code"], -32601)


# ─────────────────────────────────────────────────────────────────────
# Batch + parse-error
# ─────────────────────────────────────────────────────────────────────
class TestBatchAndParse(unittest.TestCase):
    def test_batch_returns_array(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Authorization": "Bearer x"},
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ],
        )
        d = r.json()
        self.assertIsInstance(d, list)
        self.assertEqual(len(d), 2)
        self.assertEqual([x["id"] for x in d], [1, 2])
        self.assertEqual(len(d[1]["result"]["tools"]), 4)

    def test_invalid_json_returns_parse_error(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            headers={"Content-Type": "application/json"},
            content=b"{not json",
        )
        # JSON-RPC parse error → wraps in -32700, status 400
        self.assertEqual(r.status_code, 400)
        d = r.json()
        self.assertEqual(d["error"]["code"], -32700)

    def test_wrong_jsonrpc_version(self):
        client, _ = _client()
        r = client.post(
            "/api/aurem-dev/mcp",
            json={"jsonrpc": "1.0", "id": 1, "method": "initialize"},
        )
        d = r.json()
        self.assertEqual(d["error"]["code"], -32600)


if __name__ == "__main__":
    unittest.main()
