"""Regression lock — Iter 339m (PROD P0, 2026-07-29).

/chat/stream returned 499 for ALL non-admin/free accounts. Root cause:
a function-LOCAL `from services.usage import is_founder_email` inside
`chat_stream` made the name function-scoped for the WHOLE function
body (Python scoping), so the earlier use at ~line 1121 raised
UnboundLocalError. Admin/founder accounts short-circuited before the
call, masking the crash in founder testing.

This lock parses routers/chat.py with ast and asserts `chat_stream`
never re-grows an unaliased local import of `is_founder_email`.
"""
import ast
from pathlib import Path

CHAT_PY = Path(__file__).resolve().parents[1] / "routers" / "chat.py"


def _functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def test_chat_stream_has_no_local_is_founder_email_import():
    tree = ast.parse(CHAT_PY.read_text())
    offenders = []
    for fn in _functions(tree):
        if fn.name != "chat_stream":
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.ImportFrom) and sub.module == "services.usage":
                for a in sub.names:
                    if a.name == "is_founder_email" and a.asname is None:
                        offenders.append(f"{fn.name}:{sub.lineno}")
    assert not offenders, (
        "chat_stream re-grew a function-local unaliased import of "
        f"is_founder_email ({offenders}) — this shadows the module-level "
        "import for the whole function scope and crashes free-tier "
        "accounts with UnboundLocalError (Iter 339m prod incident). "
        "Use the module-level import or an `as _alias` import."
    )


def test_module_level_is_founder_email_import_present():
    tree = ast.parse(CHAT_PY.read_text())
    top_level = [
        a.name for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "services.usage"
        for a in node.names
    ]
    assert "is_founder_email" in top_level
