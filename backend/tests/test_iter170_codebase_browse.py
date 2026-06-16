"""
Iter 170c — codebase browsing endpoints test.

Covers:
  GET /cto/projects/{id}/tree   — happy path + filters + sort order
  GET /cto/projects/{id}/file   — happy path + path-traversal guard
                                   + truncation marker

The actual GitHub HTTP calls are mocked via monkey-patching httpx.AsyncClient
and the gh_api_fetch_file helper so we never hit github.com from tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# Make backend importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


def _client_with_seeded_project():
    """Boot the app with an in-memory project that has fake GitHub creds."""
    from main import app
    from routers import cto_projects as cp

    # Stub auth to a fixed user
    async def fake_current_dev(_authorization=None):
        return {"user_id": "u_test", "email": "t@t.t"}
    cp.current_dev = fake_current_dev

    # Stub DB to return a project with github connection
    project_doc = {
        "project_id": "p_test",
        "user_id": "u_test",
        "github_owner": "octo",
        "github_repo": "demo",
        "branch": "main",
        "github_token": "ghp_fakebutshort",   # not v1: prefix → passes through
        "name": "demo",
    }
    fake_db = MagicMock()
    fake_db.cto_projects = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value=project_doc)
    cp.require_db = lambda: fake_db
    cp.get_db = lambda: fake_db

    return TestClient(app), project_doc


class TestTreeEndpoint(unittest.TestCase):
    def test_tree_filters_and_sorts(self):
        client, _ = _client_with_seeded_project()

        # Fake GitHub /git/trees/{branch} payload
        fake_tree = {
            "tree": [
                {"type": "blob", "path": "node_modules/lib/x.js", "size": 100},
                {"type": "blob", "path": "README.md",            "size":  500},
                {"type": "blob", "path": "src/app.py",           "size": 1000},
                {"type": "blob", "path": "package.json",         "size":  300},
                {"type": "blob", "path": "deep/sub/file.ts",     "size":  200},
                {"type": "blob", "path": "image.png",            "size":  500},
                {"type": "blob", "path": "huge.txt",             "size": 999999},
                {"type": "tree", "path": "src", "size": 0},
            ],
            "truncated": False,
        }

        async def fake_get(self, url, headers=None):  # noqa: ARG001
            resp = MagicMock()
            resp.status_code = 200
            resp.json = lambda: fake_tree
            resp.raise_for_status = lambda: None
            return resp

        with patch("httpx.AsyncClient.get", new=fake_get):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/tree",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        paths = [f["path"] for f in data["files"]]
        # node_modules + image + huge filtered out
        self.assertNotIn("node_modules/lib/x.js", paths)
        self.assertNotIn("image.png", paths)
        self.assertNotIn("huge.txt", paths)
        # README first, then root package.json, then by depth/alpha
        self.assertEqual(paths[0], "README.md")
        self.assertEqual(paths[1], "package.json")
        # owner/repo/branch echoed back
        self.assertEqual(data["owner"], "octo")
        self.assertEqual(data["repo"], "demo")
        self.assertEqual(data["branch"], "main")


class TestFileEndpoint(unittest.TestCase):
    def test_file_happy_path(self):
        client, _ = _client_with_seeded_project()
        from routers import cto_projects as cp

        async def fake_fetch_file(_c, _o, _r, path, _b, _t):
            return f"# content of {path}\nprint('hi')\n"

        with patch.object(cp, "gh_api_fetch_file", side_effect=fake_fetch_file):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=src/app.py",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("content of src/app.py", data["content"])
        self.assertFalse(data["truncated"])

    def test_file_path_traversal_rejected(self):
        client, _ = _client_with_seeded_project()
        for bad in ["../etc/passwd", "/abs/path", "a/../b"]:
            r = client.get(
                f"/api/aurem-dev/cto/projects/p_test/file?path={bad}",
                headers={"Authorization": "Bearer x"},
            )
            self.assertEqual(r.status_code, 400, f"{bad}: {r.text}")

    def test_file_missing_returns_404(self):
        client, _ = _client_with_seeded_project()
        from routers import cto_projects as cp

        async def fake_fetch_file(*_a, **_k):
            return None

        with patch.object(cp, "gh_api_fetch_file", side_effect=fake_fetch_file):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=missing.md",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 404)

    def test_file_truncation_marker(self):
        client, _ = _client_with_seeded_project()
        from routers import cto_projects as cp

        # 300 KB of "a" → exceeds 200KB cap
        huge = "a" * (300 * 1024)

        async def fake_fetch_file(*_a, **_k):
            return huge

        with patch.object(cp, "gh_api_fetch_file", side_effect=fake_fetch_file):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=big.txt",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["truncated"])
        self.assertIn("# … (truncated)", data["content"])
        # Truncated payload must NOT exceed the budget (plus tail marker)
        self.assertLess(len(data["content"]), 210 * 1024)


if __name__ == "__main__":
    unittest.main()
