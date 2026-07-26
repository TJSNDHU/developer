"""
test_iter309_batch2_item8_item9_contracts.py — Batch 2 Items 8 + 9

Item 8 — Contract that no NEW inline admin check gets added outside
         the shared `require_admin` helper. Any regression re-adds
         a `is_founder`+`is_admin` OR pattern in a router file gets
         flagged.

Item 9 — Smoke that /admin/loop-metrics now returns the new
         `sse_buffer` block (active_loops / total_buffered / max_seq).
"""
from __future__ import annotations
import re
from pathlib import Path

# ── Item 8 pattern contract ────────────────────────────────────────
def test_no_inline_admin_check_outside_require_admin():
    """Iter 309 · Batch-2 Item 8 — every router file must delegate
    admin-gating to `cto_services.auth.require_admin`. A new inline
    `is_founder`/`is_admin` OR pattern is a regression.

    Exceptions (documented in-code): supabase.py's founder-only
    (NOT admin) force-delete endpoint keeps its narrower gate; the
    line carries the marker `# inline: founder-only, not admin` so
    grep-based contract can allow it explicitly.
    """
    routers = Path("/app/backend/routers")
    offenders: list[str] = []
    pat = re.compile(
        r"is_founder.*(?:and\s+not\s+user\.get|or\s+user\.get).*is_admin"
        r"|is_admin.*(?:and\s+not\s+user\.get|or\s+user\.get).*is_founder",
    )
    for f in routers.glob("*.py"):
        # Skip the shared admin router — its `_require_admin`
        # helper IS the canonical implementation.
        if f.name == "admin.py":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if pat.search(line):
                if "# inline:" in line:
                    continue      # documented exception
                offenders.append(f"{f.name}:{i}: {line.strip()}")
    assert offenders == [], (
        "New inline admin checks outside require_admin:\n" +
        "\n".join(offenders)
    )


def test_scaffold_and_supabase_use_require_admin():
    """Positive assertion — the migration landed. Both files import
    require_admin at least once."""
    for name in ("scaffold.py", "supabase.py"):
        text = Path(f"/app/backend/routers/{name}").read_text()
        assert "from cto_services.auth import require_admin" in text, (
            f"{name} does not import require_admin — Item 8 migration missing"
        )


# ── Item 9 smoke ───────────────────────────────────────────────────
def test_loop_metrics_source_exposes_sse_buffer_block():
    """/admin/loop-metrics response contains an `sse_buffer` key
    with active_loops + total_buffered + max_seq fields. Verified
    at the source level to avoid needing a live admin token."""
    text = Path("/app/backend/routers/admin.py").read_text()
    assert '"sse_buffer": sse_summary' in text, (
        "loop_metrics response missing sse_buffer field"
    )
    assert '"active_loops":' in text
    assert '"total_buffered":' in text
    assert '"max_seq":' in text
    assert "from services.sse_replay_buffer import buffer_stats" in text
