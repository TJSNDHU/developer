"""
test_truncation_guard.py — Iter 30 protection against silent AI
truncation bug.

The _looks_truncated() gate is what prevents commits like
  "// ... rest of file unchanged ..."
from ever reaching the user's GitHub repo. Pure unit test — no network.
"""
from __future__ import annotations

import pytest

from routers.cto_projects import _looks_truncated


@pytest.mark.parametrize("path,body", [
    ("src/App.jsx", "// ... rest of file unchanged ...\n"),
    ("backend/main.py", "from x import y\n# ... existing imports ...\n"),
    ("README.md", "Title\n\n... (truncated)\n"),
    ("server.go", "/* existing code */\nfunc main() {}\n"),
    ("a.ts", "<keep the rest of the file as is>\nexport {}\n"),
    ("b.py", "def foo():\n    # rest of file\n    pass\n"),
])
def test_rejects_placeholders(path, body):
    assert _looks_truncated(path, body) is not None


@pytest.mark.parametrize("path,body", [
    ("src/App.jsx", "import React from 'react'\nexport default function App(){\n  return <h1>hi</h1>\n}\n"),
    ("README.md", "# AUREM\nA tagline.\nLast line.\n"),
    ("config.json", '{\n  "a": 1\n}\n'),
])
def test_accepts_complete_files(path, body):
    assert _looks_truncated(path, body) is None


def test_rejects_empty_body():
    assert _looks_truncated("any.py", "") is not None
    assert _looks_truncated("any.py", "   \n   \n") is not None


def test_rejects_one_liner_code_file():
    # A .py file with only 1 non-blank line is almost certainly truncated
    assert _looks_truncated("server.py", "pass\n") is not None
    # But a .txt or .json or .md with 1 line is fine
    assert _looks_truncated("notes.txt", "single line\n") is None
