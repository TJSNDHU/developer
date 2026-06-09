"""
services/task_diff.py
=====================
Iter 114 — Compute file-level diffs + collect popup data for live task UI.

Public helpers:
  build_files_read(contents)           → [{name, lines_count}]
  build_files_changed(before, after)   → [{name, lines_added, lines_removed,
                                           line_number, old_value, new_value}]
  shape_vanguard_findings(findings)    → [{rule, file, line, severity, status}]
"""
from __future__ import annotations


def _lines(s: str | None) -> list[str]:
    if not s:
        return []
    return s.splitlines()


def build_files_read(contents: dict[str, str | None]) -> list[dict]:
    """Build the live-tape "files read" list. Only counts files we
    actually received (non-None body)."""
    out: list[dict] = []
    for name, body in (contents or {}).items():
        if body is None:
            continue
        out.append({
            "name":        name,
            "lines_count": len(_lines(body)),
        })
    return out


def build_files_changed(
    before: dict[str, str | None],
    after:  dict[str, str | None],
) -> list[dict]:
    """For each file in `after`, compare line-by-line with `before` and
    emit:
        - lines_added   : count of lines added
        - lines_removed : count of lines removed
        - line_number   : 1-based line of the first differing line
                          (None when the file is brand-new)
        - old_value     : the old line at line_number  (None when new file)
        - new_value     : the new line at line_number  (None when deleted)
    """
    rows: list[dict] = []
    for name, new_body in (after or {}).items():
        new_lines = _lines(new_body)
        old_body  = (before or {}).get(name)
        old_lines = _lines(old_body)

        # Quick raw add/remove counts (independent of first-diff line)
        # — use set semantics within a line index to keep this O(n).
        added   = max(0, len(new_lines) - len(old_lines))
        removed = max(0, len(old_lines) - len(new_lines))
        # For matching-length region, refine: count lines that actually changed
        common = min(len(old_lines), len(new_lines))
        for i in range(common):
            if old_lines[i] != new_lines[i]:
                added   += 1
                removed += 1

        first_diff_idx = None
        old_value     = None
        new_value     = None
        if old_body is None:
            # Brand-new file → no "line that changed" — use first line
            if new_lines:
                first_diff_idx = 1
                new_value      = new_lines[0]
        else:
            for i in range(max(len(old_lines), len(new_lines))):
                a = old_lines[i] if i < len(old_lines) else None
                b = new_lines[i] if i < len(new_lines) else None
                if a != b:
                    first_diff_idx = i + 1
                    old_value      = a
                    new_value      = b
                    break

        rows.append({
            "name":          name,
            "lines_added":   added,
            "lines_removed": removed,
            "line_number":   first_diff_idx,
            "old_value":     (old_value[:240] if old_value else None),
            "new_value":     (new_value[:240] if new_value else None),
        })
    return rows


def shape_vanguard_findings(findings, status: str = "blocked") -> list[dict]:
    """Slim each Vanguard finding to the popup contract.
    `status` is "blocked" when verify_patch failed, "fixed" when a
    later reviewer rewrite resolved the issue, "clean" for synthetics."""
    out: list[dict] = []
    for f in (findings or []):
        out.append({
            "rule":     (f.get("rule") or f.get("name") or f.get("type") or "unknown")[:64],
            "file":     (f.get("file") or "")[:120],
            "line":     f.get("line"),
            "severity": (f.get("severity") or "MEDIUM").upper()[:12],
            "status":   status,
        })
    return out
