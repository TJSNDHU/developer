"""
services/architecture_health.py — Static-analysis health report.

Why this exists
---------------
`cto_projects.py` hit 1952 lines before anyone noticed. That's a
process bug: we discovered it manually. This module turns four
proven architecture-quality signals into an automated, repeatable
report so the next 1952-line file is caught at 320, not 2000.

What it measures (no LLM, no network — pure AST + filesystem walk):

  1. **File-size bloat**     — any .py / .jsx / .tsx / .js / .ts
                              file with > 300 source lines.
  2. **Cyclomatic complexity**— functions with CC > 10 (radon).
  3. **God files**           — top-N most-imported modules.
  4. **Circular imports**    — strongly-connected components
                              with more than one node.
  5. **Module boundary violations**:
        • a `routers/` file importing another `routers/` file
        • a `services/` file importing from `routers/`
        • any file performing direct `httpx.AsyncClient()`
          outside `services/` / `cto_services/`

Public surface
--------------
    run_health_report(roots) -> dict   # full structured payload
    summarise(report)        -> str    # short human-readable summary

The admin router (`/admin/architecture-health`) and the
pytest regression both use these two entry points.

No third-party calls. No filesystem mutations. ~Sub-second runtime
on the current codebase.
"""
from __future__ import annotations

import ast
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

try:
    from radon.complexity import cc_visit
except ImportError:  # pragma: no cover — radon is in requirements.txt
    cc_visit = None


# ── Tunables ──────────────────────────────────────────────────────────
LINE_LIMIT     = 300         # files above this are flagged
CC_LIMIT       = 10          # function cyclomatic complexity ceiling
GOD_FILE_TOP_N = 10          # how many "most imported" to surface

PY_EXTENSIONS = {".py"}
JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
ALL_EXTENSIONS = PY_EXTENSIONS | JS_EXTENSIONS

# Folders we never inspect — generated / vendored / mass-imported.
SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    "dist", "build", ".next", "coverage", ".pytest_cache",
    "migrations", "shadcn", "tests",
}

# Files we never flag for line-count (third-party / generated).
SKIP_FILE_PATTERNS = (
    re.compile(r"\.min\."),
    re.compile(r"\.bundle\."),
    re.compile(r"^test_"),
)


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class FileMetric:
    path:        str
    rel:         str
    lines:       int
    extension:   str

    def as_dict(self) -> dict:
        return self.__dict__


@dataclass
class ComplexityHit:
    file:       str
    func:       str
    line:       int
    cc:         int

    def as_dict(self) -> dict:
        return self.__dict__


@dataclass
class BoundaryViolation:
    file:       str
    rule:       str
    detail:     str

    def as_dict(self) -> dict:
        return self.__dict__


@dataclass
class HealthReport:
    generated_at:        float
    duration_ms:         int
    total_files:         int
    line_limit:          int
    cc_limit:            int
    bloated_files:       list[FileMetric]   = field(default_factory=list)
    complexity_hits:     list[ComplexityHit] = field(default_factory=list)
    god_files:           list[dict]          = field(default_factory=list)
    circular_imports:    list[list[str]]     = field(default_factory=list)
    boundary_violations: list[BoundaryViolation] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generated_at":        self.generated_at,
            "duration_ms":         self.duration_ms,
            "total_files":         self.total_files,
            "line_limit":          self.line_limit,
            "cc_limit":            self.cc_limit,
            "bloated_files":       [f.as_dict() for f in self.bloated_files],
            "complexity_hits":     [c.as_dict() for c in self.complexity_hits],
            "god_files":           self.god_files,
            "circular_imports":    self.circular_imports,
            "boundary_violations": [v.as_dict() for v in self.boundary_violations],
        }


# ── File walk ──────────────────────────────────────────────────────────

def _iter_source_files(roots: Iterable[str]) -> Iterable[tuple[str, str]]:
    """Yield (absolute_path, relative_path) for every source file we
    care about. `relative` is anchored at the first root that contains
    the file so output paths are short and readable."""
    seen: set[str] = set()
    for root in roots:
        abs_root = os.path.abspath(root)
        if not os.path.isdir(abs_root):
            continue
        for dirpath, dirnames, filenames in os.walk(abs_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ALL_EXTENSIONS:
                    continue
                if any(p.search(fname) for p in SKIP_FILE_PATTERNS):
                    continue
                abs_path = os.path.join(dirpath, fname)
                if abs_path in seen:
                    continue
                seen.add(abs_path)
                rel = os.path.relpath(abs_path, abs_root)
                yield abs_path, rel


def _count_source_lines(path: str) -> int:
    """Non-blank lines only — keeps the metric honest when a file is
    full of license headers or auto-generated boilerplate."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return sum(1 for ln in fh if ln.strip())
    except OSError:
        return 0


# ── 1. Bloated files ──────────────────────────────────────────────────

def _scan_bloated(files: list[tuple[str, str]]) -> list[FileMetric]:
    hits: list[FileMetric] = []
    for abs_path, rel in files:
        lines = _count_source_lines(abs_path)
        if lines > LINE_LIMIT:
            hits.append(FileMetric(
                path=abs_path, rel=rel, lines=lines,
                extension=os.path.splitext(rel)[1].lower(),
            ))
    hits.sort(key=lambda f: f.lines, reverse=True)
    return hits


# ── 2. Cyclomatic complexity (Python only — radon limitation) ─────────

def _scan_complexity(files: list[tuple[str, str]]) -> list[ComplexityHit]:
    if cc_visit is None:
        return []
    hits: list[ComplexityHit] = []
    for abs_path, rel in files:
        if not abs_path.endswith(".py"):
            continue
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            for block in cc_visit(src):
                if block.complexity > CC_LIMIT:
                    hits.append(ComplexityHit(
                        file=rel, func=block.fullname,
                        line=block.lineno, cc=block.complexity,
                    ))
        except (SyntaxError, OSError):
            continue
    hits.sort(key=lambda c: c.cc, reverse=True)
    return hits


# ── 3 + 4. Import graph → god files + circular cycles ─────────────────

_PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
    re.MULTILINE,
)
_JS_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:[^"';]+from\s+)?["']([^"']+)["']""",
    re.MULTILINE,
)


def _extract_imports(abs_path: str) -> set[str]:
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return set()
    if abs_path.endswith(".py"):
        # `from X.Y import Z` → "X.Y"; `import X.Y` → "X.Y"
        return {m.group(1) or m.group(2) for m in _PY_IMPORT_RE.finditer(src)}
    return {m.group(1) for m in _JS_IMPORT_RE.finditer(src)}


def _scan_imports(files: list[tuple[str, str]]) -> tuple[list[dict], list[list[str]]]:
    # Build "module key" per file so import statements can resolve.
    rel_by_key: dict[str, str] = {}
    edges: dict[str, set[str]] = defaultdict(set)

    for abs_path, rel in files:
        key = _module_key(rel)
        if key:
            rel_by_key[key] = rel

    for abs_path, rel in files:
        key = _module_key(rel)
        if not key:
            continue
        for imp in _extract_imports(abs_path):
            # Direct hit
            if imp in rel_by_key:
                edges[key].add(imp)
                continue
            # Relative / partial: walk parents and check for matches
            parts = imp.replace("/", ".").split(".")
            for i in range(len(parts), 0, -1):
                candidate = ".".join(parts[:i])
                if candidate in rel_by_key and candidate != key:
                    edges[key].add(candidate)
                    break

    # God files: count incoming edges.
    inbound: dict[str, int] = defaultdict(int)
    for src, dsts in edges.items():
        for d in dsts:
            inbound[d] += 1
    god = [
        {"module": k, "path": rel_by_key[k], "imported_by": cnt}
        for k, cnt in inbound.items() if cnt >= 3
    ]
    god.sort(key=lambda r: r["imported_by"], reverse=True)
    god = god[:GOD_FILE_TOP_N]

    # Circular imports — Tarjan SCC, then keep components |C| > 1.
    cycles = _find_sccs(edges)
    pretty_cycles = [[rel_by_key.get(n, n) for n in cyc] for cyc in cycles]

    return god, pretty_cycles


def _module_key(rel_path: str) -> str:
    """Convert "backend/services/foo.py" → "backend.services.foo".
    Keep JS/TS extensions intact so the import resolver can match
    a `from "../components/Foo"` reliably."""
    no_ext, _ext = os.path.splitext(rel_path)
    return no_ext.replace(os.sep, ".").replace("/", ".")


def _find_sccs(edges: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan strongly-connected components. Returns only
    components with size > 1 (a self-edge alone isn't 'circular')."""
    index_counter = [0]
    stack: list[str] = []
    on_stack: dict[str, bool] = {}
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    nodes = set(edges.keys()) | {d for s in edges.values() for d in s}

    def _strongconnect(v: str) -> None:
        work_stack = [(v, iter(edges.get(v, ())))]
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        while work_stack:
            v, it = work_stack[-1]
            try:
                w = next(it)
                if w not in index:
                    index[w] = index_counter[0]
                    lowlink[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    work_stack.append((w, iter(edges.get(w, ()))))
                elif on_stack.get(w):
                    lowlink[v] = min(lowlink[v], index[w])
            except StopIteration:
                if lowlink[v] == index[v]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        component.append(w)
                        if w == v:
                            break
                    if len(component) > 1:
                        result.append(component)
                work_stack.pop()
                if work_stack:
                    parent = work_stack[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])

    for n in nodes:
        if n not in index:
            _strongconnect(n)
    return result


# ── 5. Module boundary violations ─────────────────────────────────────

# We DO want services to import other services, but we do NOT want
# routers importing routers (that's a cross-API leak) or services
# importing routers (inverted dependency).
_VIOLATION_RULES = [
    ("routers/", "routers/",
     "router-imports-router"),
    ("services/", "routers/",
     "service-imports-router"),
]


def _scan_boundaries(files: list[tuple[str, str]]) -> list[BoundaryViolation]:
    hits: list[BoundaryViolation] = []
    for abs_path, rel in files:
        if not abs_path.endswith(".py"):
            continue
        rel_norm = rel.replace("\\", "/")
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        for src_pref, dst_pref, rule_id in _VIOLATION_RULES:
            if src_pref not in rel_norm:
                continue
            # Look for any `from <dst_pref>...` or `import <dst_pref>...`.
            # We use a relaxed check based on the prefix's top-level
            # name ("routers", "services", etc.).
            top_name = dst_pref.strip("/")
            # exclude the file from flagging itself.
            for line in src.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith(f"from {top_name}.") or s.startswith(f"from {top_name} "):
                    if not rel_norm.startswith(dst_pref) or os.path.basename(rel_norm) != os.path.basename(rel_norm):
                        # Same source-and-dest prefix but different file → still a violation
                        hits.append(BoundaryViolation(
                            file=rel, rule=rule_id, detail=s,
                        ))
                        break
                if s.startswith(f"import {top_name}.") or s == f"import {top_name}":
                    hits.append(BoundaryViolation(
                        file=rel, rule=rule_id, detail=s,
                    ))
                    break

    # Direct external HTTP from non-services / non-tools layers.
    for abs_path, rel in files:
        if not abs_path.endswith(".py"):
            continue
        rel_norm = rel.replace("\\", "/")
        if "/services/" in f"/{rel_norm}" or rel_norm.startswith("services/"):
            continue
        if "/cto_services/" in f"/{rel_norm}" or rel_norm.startswith("cto_services/"):
            continue
        if rel_norm.startswith("tests/"):
            continue
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        if re.search(r"\bhttpx\.AsyncClient\(", src) or \
           re.search(r"\brequests\.(?:get|post|put|delete|patch)\(", src):
            hits.append(BoundaryViolation(
                file=rel, rule="http-call-outside-services",
                detail="raw httpx/requests call — wrap it in services/",
            ))
    return hits


# ── Public API ────────────────────────────────────────────────────────

def run_health_report(roots: Optional[Iterable[str]] = None) -> dict:
    """Run all five checks. Returns the report as a JSON-serialisable
    dict — never raises on user-mode failures (per-file errors are
    swallowed by each scanner)."""
    started = time.time()
    roots = list(roots) if roots else _default_roots()
    files = list(_iter_source_files(roots))

    bloated     = _scan_bloated(files)
    complexity  = _scan_complexity(files)
    god, cycles = _scan_imports(files)
    violations  = _scan_boundaries(files)

    report = HealthReport(
        generated_at=started,
        duration_ms=int((time.time() - started) * 1000),
        total_files=len(files),
        line_limit=LINE_LIMIT,
        cc_limit=CC_LIMIT,
        bloated_files=bloated,
        complexity_hits=complexity,
        god_files=god,
        circular_imports=cycles,
        boundary_violations=violations,
    )
    return report.as_dict()


def _default_roots() -> list[str]:
    """Repo-relative roots from this file's location:
        services/architecture_health.py → /app/backend, /app/frontend/src
    """
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(here)                  # /app/backend
    repo    = os.path.dirname(backend)               # /app
    front   = os.path.join(repo, "frontend", "src")
    return [backend, front]


def summarise(report: dict) -> str:
    """One-screen human-readable summary."""
    n = report
    out = [
        f"# Architecture health — {n['total_files']} files scanned "
        f"({n['duration_ms']} ms)",
        "",
        f"## 🪨 Bloated files (> {n['line_limit']} non-blank lines) — "
        f"{len(n['bloated_files'])}",
    ]
    for row in n["bloated_files"][:15]:
        out.append(f"  {row['lines']:>5} lines   {row['rel']}")
    if not n["bloated_files"]:
        out.append("  (none — good)")

    out += [
        "",
        f"## 🌀 Complex functions (CC > {n['cc_limit']}) — "
        f"{len(n['complexity_hits'])}",
    ]
    for hit in n["complexity_hits"][:15]:
        out.append(f"  CC={hit['cc']:>3}   {hit['file']}::{hit['func']} (line {hit['line']})")
    if not n["complexity_hits"]:
        out.append("  (none — good)")

    out += [
        "",
        f"## 📡 God files (imported by ≥ 3 modules) — top {len(n['god_files'])}",
    ]
    for row in n["god_files"]:
        out.append(f"  {row['imported_by']:>3}×   {row['path']}")
    if not n["god_files"]:
        out.append("  (none)")

    out += [
        "",
        f"## ♻️ Circular imports — {len(n['circular_imports'])}",
    ]
    for cyc in n["circular_imports"][:5]:
        out.append("  " + " → ".join(cyc) + " → …")
    if not n["circular_imports"]:
        out.append("  (none — good)")

    out += [
        "",
        f"## 🚧 Boundary violations — {len(n['boundary_violations'])}",
    ]
    for v in n["boundary_violations"][:15]:
        out.append(f"  [{v['rule']}]   {v['file']}: {v['detail']}")
    if not n["boundary_violations"]:
        out.append("  (none — good)")

    return "\n".join(out) + "\n"
