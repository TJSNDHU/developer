"""
services/test_style_analyzer.py — Iter 290 (Track 1 Lane A follow-up)

The founder's insight (verbatim): "many regression tests are static
source-grep — they validate patterns but never execute the code."
Before writing any new behavioural tests, we need to know which
existing 'green' tests are grep-only, so their pass-signal can be
downgraded and the actual weak-security surfaces surfaced.

This module walks every `tests/test_*.py` under /app/backend, parses
it as Python AST, and classifies each test function by evidence:

  STATIC_GREP   — the test READS a source file (open(...).read(),
                  _read(path), pathlib.read_text(), etc.) AND its
                  assertions target a string derived from that read
                  (e.g. `assert "X" in src`). No `await`, no direct
                  invocation of a function under test.

  BEHAVIOURAL   — the test EXECUTES code paths: either
                    • has `await` calls, or
                    • has `asyncio.run(...)`, or
                    • calls a function that was `from services... import`ed
                      and asserts on its return value.

  HYBRID        — has both a file-read AND an execution call. Not
                  weak — surface it so a maintainer can decide
                  whether the assertion is the executed part or the
                  grep part.

  UNKNOWN       — no clear signal (e.g. asserts on a hard-coded
                  constant, or on an imported module attribute).
                  Rare; treated as inconclusive, not weak.

Deterministic. No LLM. No I/O outside /app/backend/tests. This is
the substrate `qa_static_vs_behavioural_ratio` returns to MCP.
"""
from __future__ import annotations

import ast
import os
import re
from typing import Any

_TESTS_DIR = "/app/backend/tests"

# AST-node markers per class. Each set is small + intentional; if the
# heuristic starts missing cases, extend here and add a regression.
_FILE_READ_ATTRS = {"read", "read_text", "read_bytes"}
_FILE_READ_NAMES = {"_read", "read_file"}                 # helpers
_ASYNC_RUN_NAMES = {"run"}                                # asyncio.run
_ASYNCIO_MODULES = {"asyncio"}


def _has_file_read(func_node: ast.FunctionDef) -> bool:
    """True when the test opens a source file for reading — the
    classical grep-style precondition."""
    for node in ast.walk(func_node):
        # open(...).read()
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in _FILE_READ_ATTRS:
                return True
            if isinstance(f, ast.Name) and f.id == "open":
                return True
            if isinstance(f, ast.Name) and f.id in _FILE_READ_NAMES:
                return True
    return False


def _has_execution(func_node: ast.FunctionDef,
                   imported_symbols: set[str]) -> bool:
    """True when the test actually EXECUTES code — awaits, asyncio.run,
    or calls one of the imported symbols (from services/routers/etc)."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Await):
            return True
        if isinstance(node, ast.Call):
            f = node.func
            # asyncio.run(...)
            if (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id in _ASYNCIO_MODULES
                    and f.attr in _ASYNC_RUN_NAMES):
                return True
            # Direct call to an imported symbol from
            # services / routers / cto_services / core / scripts.
            if isinstance(f, ast.Name) and f.id in imported_symbols:
                return True
            # Attribute call where the base module was imported (e.g.
            # `qa_matrix.matrix_coverage_gap(...)`).
            if (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id in imported_symbols):
                return True
    return False


def _collect_imported_symbols(tree: ast.Module) -> set[str]:
    """Symbols imported from any code-under-test module. We treat
    imports like `from services.foo import bar` as evidence that a
    later `bar(...)` call is executing production code."""
    prod_prefixes = ("services", "routers", "cto_services", "core",
                     "scripts")
    syms: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in prod_prefixes:
                for a in node.names:
                    syms.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in prod_prefixes:
                    syms.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.FunctionDef):
            # Some tests do `from services.qa_matrix import load_matrix`
            # INSIDE the test function — the ast.walk above catches
            # those since we walk the whole tree.
            pass
    return syms


def classify_test_function(func_node: ast.FunctionDef,
                           imported_symbols: set[str]) -> str:
    has_read = _has_file_read(func_node)
    has_exec = _has_execution(func_node, imported_symbols)
    if has_read and has_exec:
        return "HYBRID"
    if has_read:
        return "STATIC_GREP"
    if has_exec:
        return "BEHAVIOURAL"
    return "UNKNOWN"


def analyze_file(path: str) -> dict:
    """Return per-function classifications for one test file.

    Iter 294 — JSX/TS support. For `.test.jsx` / `.test.js` /
    `.test.tsx` / `.test.ts` files we can't use Python's `ast` (it
    doesn't parse JSX). Instead we run a regex heuristic that mirrors
    the Python classifier's contract:
      STATIC_GREP — file-reads (`readFileSync`, `fs.readFile`, or
                    string-includes on file content) AND no RTL/
                    userEvent evidence.
      BEHAVIOURAL — presence of RTL/userEvent/fireEvent/waitFor,
                    which are the observable-DOM assertion helpers
                    that CANNOT be faked from a source-string grep.
      HYBRID / UNKNOWN — same rules as the Python path.
    """
    if path.endswith((".test.jsx", ".test.js", ".test.tsx", ".test.ts")):
        return _analyze_js_file(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except Exception as e:                                       # noqa: BLE001
        return {"path": path, "ok": False, "reason": repr(e)[:200]}

    imported = _collect_imported_symbols(tree)
    per_test: list[dict] = []
    for node in ast.walk(tree):
        # Both `def test_...` and `async def test_...` — the latter is
        # an AsyncFunctionDef in ast, so walk covers both when we
        # treat AsyncFunctionDef as functionally equivalent for our
        # purposes.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("test_"):
                continue
            per_test.append({
                "test":  node.name,
                "line":  node.lineno,
                "kind":  classify_test_function(node, imported),
            })
    return {"path": path, "ok": True, "tests": per_test}


# ── Iter 294 (Frontend Layer 1) — JSX/TS test classifier ─────────────
# Regex-based since Python's AST cannot parse JSX. Matches the Python
# classifier's contract; every rule here has a paired regression test
# in test_regression_iter294_frontend_layer1.py.

_JS_TEST_BLOCK_RE = re.compile(
    # Match `it(` or `test(` followed by a quoted name. The name may
    # contain the OTHER quote type (e.g. apostrophes inside a "..."
    # name) — use a backreference to require the same quote closes.
    # `.+?` is lazy so we stop at the FIRST matching close quote of
    # the same type.
    r'(?:^|\s|;)(?:it|test)\s*\(\s*'
    r'(?P<q>["\'`])(?P<name>.+?)(?P=q)\s*,',
    re.M,
)

# Observable-DOM markers — presence of ANY = BEHAVIOURAL.
_JS_BEHAVIOURAL_TOKENS = (
    "render(", "screen.", "fireEvent", "userEvent", "waitFor(",
    "rerender(", "act(", "toHaveTextContent", "toBeVisible",
    "getByRole", "getByText", "queryByText", "findByText",
    "getByTestId", "queryByTestId",
)
# File-read markers — presence + no behavioural tokens = STATIC_GREP.
_JS_STATIC_TOKENS = (
    "readFileSync", "fs.readFile", "readFile(", "readFileSync(",
    "path.resolve",
)


def _analyze_js_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception as e:                                       # noqa: BLE001
        return {"path": path, "ok": False, "reason": repr(e)[:200]}
    per_test: list[dict] = []
    # We classify at file granularity (JS test bodies rarely differ
    # in kind within one file); every `it(...)`/`test(...)` gets the
    # same kind derived from file-level evidence.
    has_behavioural = any(tok in src for tok in _JS_BEHAVIOURAL_TOKENS)
    has_static      = any(tok in src for tok in _JS_STATIC_TOKENS)
    if has_behavioural and has_static:
        kind = "HYBRID"
    elif has_behavioural:
        kind = "BEHAVIOURAL"
    elif has_static:
        kind = "STATIC_GREP"
    else:
        kind = "UNKNOWN"
    for m in _JS_TEST_BLOCK_RE.finditer(src):
        line = src[:m.start()].count("\n") + 1
        per_test.append({"test": m.group("name"),
                          "line": line, "kind": kind})
    return {"path": path, "ok": True, "tests": per_test}


def analyze_suite(tests_dir: str = _TESTS_DIR,
                  file_pattern: str | None = None) -> dict:
    """Walk `tests_dir` and classify every test in every file.

    `file_pattern` — optional regex applied to the file basename to
    restrict scope (e.g. r'test_regression_iter28[6-9]').
    """
    if not os.path.isdir(tests_dir):
        return {"ok": False, "reason": "tests_dir_missing"}
    pat = re.compile(file_pattern) if file_pattern else None
    files: list[dict] = []
    counts: dict[str, int] = {"STATIC_GREP": 0, "BEHAVIOURAL": 0,
                              "HYBRID": 0, "UNKNOWN": 0}
    weak_p0: list[dict] = []
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if pat and not pat.search(name):
            continue
        rep = analyze_file(os.path.join(tests_dir, name))
        files.append(rep)
        for t in (rep.get("tests") or []):
            counts[t["kind"]] = counts.get(t["kind"], 0) + 1
            # A test is "weak-P0" when it names a security-critical
            # concern in its identifier AND its kind is STATIC_GREP.
            # Case-insensitive matches on tokens we CURRENTLY treat
            # as p0 in the traceability matrix.
            p0_tokens = ("test_file_lock", "verifier_verdict",
                         "scope_drift", "held_out", "test-file-lock",
                         "bulkhead", "ttl", "sse_stream",
                         "cancel", "verdict", "override",
                         "grounding", "pi_shield", "pat_leak")
            if t["kind"] == "STATIC_GREP":
                lower = t["test"].lower()
                if any(tok in lower for tok in p0_tokens):
                    weak_p0.append({
                        "file":  rep["path"].split("/")[-1],
                        "test":  t["test"],
                        "line":  t["line"],
                    })
    total = sum(counts.values()) or 1
    return {
        "ok":       True,
        "tests_dir": tests_dir,
        "counts":   counts,
        "ratio": {
            "static_grep_pct":  round(100.0 * counts["STATIC_GREP"] / total, 1),
            "behavioural_pct":  round(100.0 * counts["BEHAVIOURAL"] / total, 1),
            "hybrid_pct":       round(100.0 * counts["HYBRID"] / total, 1),
            "unknown_pct":      round(100.0 * counts["UNKNOWN"] / total, 1),
        },
        "total_tests": total,
        "weak_p0":     weak_p0,
        "files":       files,
    }
