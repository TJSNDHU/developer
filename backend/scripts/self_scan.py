"""
scripts/self_scan.py — Iter 212m-223 — Dogfood run

Runs every AUREM scanner ON THE AUREM CODEBASE ITSELF.
No GitHub fetch, no auth — reads /app/backend and /app/frontend
straight from disk into a text_cache, then invokes each scanner
exactly the way `routers/codebase_health.scan()` does.

Produces a full markdown report at /app/test_reports/self_scan.md.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, "/app/backend")

# ── Import every scanner exactly like the router does ─────────
from routers.codebase_health import (   # noqa: E402
    SCANNERS,                            # 5 core + bug_hunt + docker
    _SKIP_DIRS, _SCAN_EXTS, _is_dockerfile,
    _MAX_BYTES_PER_FILE, _MAX_FILES,
)

REPO_ROOT = Path("/app")
OUT_MD    = Path("/app/test_reports/self_scan.md")
OUT_MD.parent.mkdir(exist_ok=True, parents=True)


def build_local_text_cache() -> dict[str, str]:
    """Walk /app the same way `_build_text_cache` walks a GitHub tree."""
    # Iter 212m-223 — self-scan-specific ignores to strip false-positive
    # noise from OUR OWN scan output:
    #   • .aurem_cache/repo_snapshots/*  → cached snapshots of user repos we've
    #     scanned in the past; these are OTHER PEOPLE's code, not ours.
    #   • test files                    → intentional password strings, mock tokens
    #   • docs / *.md                   → security examples in explainer docs
    SELF_SKIP = {".aurem_cache", "test_reports", "node_modules",
                 ".venv", "__pycache__", ".git"}
    cache: dict[str, str] = {}
    count = 0
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(REPO_ROOT))
        parts = rel.split("/")
        if any(p in _SKIP_DIRS for p in parts):
            continue
        if any(p in SELF_SKIP for p in parts):
            continue
        # Skip test files — they contain intentional test-only creds.
        if any(seg.startswith("test_") or seg == "tests" or seg.endswith("_test.py")
               for seg in parts):
            continue
        lower = rel.lower()
        is_scan_ext = any(lower.endswith(ext) for ext in _SCAN_EXTS)
        is_req      = lower.endswith("requirements.txt")
        is_pkg      = lower.endswith("package.json")
        is_docker   = _is_dockerfile(lower)
        if not (is_scan_ext or is_req or is_pkg or is_docker):
            continue
        try:
            if path.stat().st_size > _MAX_BYTES_PER_FILE:
                continue
            cache[rel] = path.read_text(encoding="utf-8", errors="ignore")
            count += 1
            if count >= _MAX_FILES:
                break
        except Exception:
            continue
    return cache


def sev_rank(s: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get((s or "").lower(), 0)


def main() -> None:
    print(f"📂 Building local text cache from {REPO_ROOT} ...")
    t0 = time.time()
    cache = build_local_text_cache()
    t_build = time.time() - t0
    print(f"   → {len(cache)} files loaded in {t_build:.2f}s")
    print()

    results_by_cat: dict[str, list[dict]] = {}
    latencies: dict[str, float] = {}
    for cat, fn in SCANNERS.items():
        print(f"▶️  Running scanner: {cat} ...", end=" ", flush=True)
        t = time.time()
        try:
            findings = fn(cache) or []
        except Exception as e:
            findings = [{
                "severity": "critical", "rule": "scanner_crashed",
                "message": f"{cat} scanner raised {type(e).__name__}: {e}",
                "file": "n/a", "line": 0,
            }]
        elapsed = time.time() - t
        latencies[cat] = elapsed
        results_by_cat[cat] = findings
        print(f"{len(findings)} findings ({elapsed:.2f}s)")

    # ── Also run Vanguard 007 (security_scan patterns) ──────────
    try:
        from services.vanguard_scanner import scan_file_blocks
        print("▶️  Running scanner: vanguard_007 ...", end=" ", flush=True)
        t = time.time()
        v_findings = scan_file_blocks(cache) or []
        latencies["vanguard_007"] = time.time() - t
        results_by_cat["vanguard_007"] = v_findings
        print(f"{len(v_findings)} findings ({latencies['vanguard_007']:.2f}s)")
    except Exception as e:
        print(f"skipped ({type(e).__name__}: {e})")

    # ── Architecture Health (radon + AST) — service module ──────
    try:
        from services.architecture_health import run_health_report
        print("▶️  Running scanner: architecture_health ...", end=" ", flush=True)
        t = time.time()
        report = run_health_report(["/app/backend"])
        latencies["architecture_health"] = time.time() - t
        # report is a plain dict with keys: bloated_files, complexity_hits, cycles, boundary_violations
        a_findings = []
        for hit in (report.get("complexity_hits") or []):
            a_findings.append({
                "severity": "medium", "rule": "high_complexity",
                "file": hit.get("path", "?"),
                "line": hit.get("line", 0),
                "message": f"{hit.get('name','')} — complexity={hit.get('complexity', 0)}",
            })
        for bloat in (report.get("bloated_files") or []):
            a_findings.append({
                "severity": "low", "rule": "bloated_file",
                "file": bloat.get("path", "?"),
                "line": 0,
                "message": f"{bloat.get('lines', 0)} lines",
            })
        for viol in (report.get("boundary_violations") or []):
            a_findings.append({
                "severity": "medium", "rule": viol.get("rule", "boundary"),
                "file": viol.get("path", "?"),
                "line": viol.get("line", 0),
                "message": viol.get("detail", ""),
            })
        for cyc in (report.get("cycles") or []):
            a_findings.append({
                "severity": "high", "rule": "circular_import",
                "file": " → ".join(cyc), "line": 0,
                "message": f"cycle of {len(cyc)} modules",
            })
        results_by_cat["architecture_health"] = a_findings
        print(f"{len(a_findings)} findings ({latencies['architecture_health']:.2f}s)")
    except Exception as e:
        print(f"skipped ({type(e).__name__}: {e})")

    # ── Aggregate ────────────────────────────────────────────────
    total = sum(len(v) for v in results_by_cat.values())
    sev_totals: Counter = Counter()
    for lst in results_by_cat.values():
        for f in lst:
            sev_totals[(f.get("severity") or "unknown").lower()] += 1

    # ── Render markdown ──────────────────────────────────────────
    lines: list[str] = []
    lines.append("# AUREM CTO — Self-Scan Report (Dogfood run)")
    lines.append("")
    lines.append(f"**Scan date:** `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`")
    lines.append(f"**Target:** `/app` (AUREM CTO's own codebase, backend + frontend)")
    lines.append(f"**Files scanned:** `{len(cache)}` "
                 f"(same scanner functions users hit on `auremcto.com`)")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for s in ("critical", "high", "medium", "low", "unknown"):
        if sev_totals.get(s):
            lines.append(f"| {s.upper()} | {sev_totals[s]} |")
    lines.append(f"| **TOTAL** | **{total}** |")
    lines.append("")

    lines.append("## Per-scanner breakdown")
    lines.append("")
    lines.append("| Scanner | Findings | Latency |")
    lines.append("|---|---|---|")
    for cat in results_by_cat:
        lines.append(f"| `{cat}` | {len(results_by_cat[cat])} | {latencies.get(cat, 0):.2f}s |")
    lines.append("")

    # ── Detail sections — group by scanner, sort by severity ────
    for cat, findings in results_by_cat.items():
        if not findings:
            lines.append(f"### `{cat}` — ✅ no findings")
            lines.append("")
            continue
        lines.append(f"### `{cat}` — {len(findings)} findings")
        lines.append("")
        findings_sorted = sorted(findings, key=lambda f: -sev_rank(f.get("severity", "")))
        # Group by rule for readability
        by_rule: dict[str, list[dict]] = defaultdict(list)
        for f in findings_sorted:
            by_rule[f.get("rule") or f.get("id") or "unknown"].append(f)
        for rule, rows in by_rule.items():
            top_sev = max((sev_rank(r.get("severity", "")) for r in rows), default=0)
            sev_label = {4: "CRITICAL", 3: "HIGH", 2: "MEDIUM", 1: "LOW"}.get(top_sev, "—")
            lines.append(f"- **{rule}** — `{sev_label}` × {len(rows)}")
            for f in rows[:3]:
                path = f.get("file") or f.get("path") or "?"
                ln = f.get("line") or 0
                msg = (f.get("message") or f.get("title") or "").strip()[:130]
                lines.append(f"    - `{path}:{ln}` — {msg}")
            if len(rows) > 3:
                lines.append(f"    - _…and {len(rows)-3} more_")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_Report generated by `scripts/self_scan.py` — same "
                 f"scanner functions that power `/api/aurem-dev/codebase-health/scan` and `/api/aurem-dev/security-scan/*` on production._")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print()
    print("═════════════════════════════════════════")
    print(f"  📋 Total findings   : {total}")
    print(f"  🔴 Critical         : {sev_totals.get('critical', 0)}")
    print(f"  🟠 High             : {sev_totals.get('high', 0)}")
    print(f"  🟡 Medium           : {sev_totals.get('medium', 0)}")
    print(f"  🟢 Low              : {sev_totals.get('low', 0)}")
    print(f"  ✍️  Report written   : {OUT_MD}")
    print("═════════════════════════════════════════")


if __name__ == "__main__":
    main()
