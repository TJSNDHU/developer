"""
services/fixed_findings.py — Iter 212m-193

Persistent "this finding was fixed" ledger across ALL scan tools.

Problem it solves: fixes are committed to a dedicated `aurem/fix-*`
branch + draft PR, NOT to the scanned base branch — so a rescan of the
(unchanged) base branch re-detects every fixed issue and the health
score collapses back as if no fix ever happened.

Every successful fix (single or bulk, any tool) records a row here.
Scan endpoints then split their results into ACTIVE vs FIXED findings:
score / counts / totals are computed from ACTIVE only, and fixed
findings are returned separately with their commit link.

Key = rule_id|file|line. Safe because the base branch is unchanged
until the fix PR merges, so a deterministic rescan reports identical
lines; once the PR merges the tree changes and the finding disappears
from scan output naturally (the stale ledger row simply never matches
again).
"""
from __future__ import annotations
from datetime import datetime, timezone


def finding_key(f: dict) -> str:
    # Health-scan findings carry a deterministic `id`
    # ("sec::<path>:<line>:<rule>") — prefer it. Fall back to a
    # rule|file|line composite for vanguard/bug-hunt shapes.
    if f.get("id"):
        return str(f["id"])
    rule = (f.get("rule_id") or f.get("rule") or f.get("title")
            or f.get("vuln") or "unknown")
    path = f.get("file") or f.get("path") or ""
    line = f.get("line") or 0
    return f"{rule}|{path}|{line}"


async def record_fixed(db, *, user_id: str, project_id: str,
                       finding: dict, commit_sha: str = "",
                       html_url: str = "", tool: str = "") -> None:
    """Upsert a fixed-finding row. Call ONLY after a successful fix."""
    key = finding_key(finding)
    try:
        await db.fixed_findings.update_one(
            {"user_id": user_id, "project_id": project_id, "key": key},
            {"$set": {
                "rule_id":    finding.get("rule_id") or finding.get("rule") or "",
                "file":       finding.get("file") or finding.get("path") or "",
                "line":       int(finding.get("line") or 0),
                "severity":   finding.get("severity") or "",
                "commit_sha": commit_sha or "",
                "html_url":   html_url or "",
                "tool":       tool,
                "fixed_at":   datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception:
        pass


async def get_fixed_map(db, *, user_id: str, project_id: str) -> dict:
    """{key → {commit_sha, html_url, fixed_at}} for a project."""
    out: dict = {}
    try:
        cur = db.fixed_findings.find(
            {"user_id": user_id, "project_id": project_id},
            {"_id": 0, "key": 1, "commit_sha": 1, "html_url": 1, "fixed_at": 1},
        )
        async for row in cur:
            out[row["key"]] = {
                "commit_sha": row.get("commit_sha") or "",
                "html_url":   row.get("html_url") or "",
                "fixed_at":   row.get("fixed_at") or "",
            }
    except Exception:
        pass
    return out


def split_findings(findings: list[dict], fixed_map: dict) -> tuple[list, list]:
    """Partition scan output → (active, fixed). Fixed findings get
    `fixed: True` + commit metadata merged in."""
    active: list[dict] = []
    fixed: list[dict] = []
    for f in findings:
        meta = fixed_map.get(finding_key(f))
        if meta:
            fixed.append({**f, "fixed": True,
                          "fixed_commit": meta["commit_sha"],
                          "fixed_url": meta["html_url"],
                          "fixed_at": meta["fixed_at"]})
        else:
            active.append(f)
    return active, fixed
