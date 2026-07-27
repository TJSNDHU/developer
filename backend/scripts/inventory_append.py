"""
scripts/inventory_append.py — Iter 328 · SYSTEM_INVENTORY auto-append

Mechanical helper that appends new inventory entries at the end of each
iteration. Idempotent — never appends the same line twice. Reads current
inventory, appends only NEW entries. Callable from the ship step OR ad-hoc
from bash.

Usage:
    python scripts/inventory_append.py \\
      --iter 328 \\
      --changes '[{"kind":"router","path":"routers/dev_sse_probe.py","routes":3,"prefix":"/aurem-dev/_iter309_probe","purpose":"test-only synthetic SSE probe"}]'

Kinds supported: router, envvar, collection, loop_run_log_kind,
frontend_route, service, bg_job.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

INV = Path("/app/memory/SYSTEM_INVENTORY.md")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _already_present(inventory_text: str, marker: str) -> bool:
    """Idempotence gate — never append the same entry twice."""
    return marker in inventory_text


def _marker_key(change: dict) -> str:
    """Stable idempotence key per change. Written into the entry as an
    HTML comment so `_already_present` can exact-match it. Iter-agnostic
    so appending the same envvar again in a later iteration is a no-op."""
    kind = change.get("kind", "")
    if kind == "router":
        return f"router:{change.get('path','')}"
    if kind == "envvar":
        return f"envvar:{change.get('name','')}"
    if kind == "collection":
        return f"collection:{change.get('name','')}"
    if kind == "loop_run_log_kind":
        return f"loop_run_log_kind:{change.get('value','')}"
    if kind == "frontend_route":
        return f"frontend_route:{change.get('path','')}"
    if kind == "service":
        return f"service:{change.get('path','')}"
    if kind == "bg_job":
        return f"bg_job:{change.get('name','')}"
    return f"unknown:{json.dumps(change, sort_keys=True)}"


def _format_entry(change: dict, iter_num: int) -> tuple[str, str]:
    """Return (marker, formatted_entry). Marker is an HTML-comment
    idempotence key that we embed into every entry so re-runs are
    exact-string matches."""
    kind = change.get("kind", "")
    ts = _iso_utc()
    key = _marker_key(change)
    marker = f"<!-- inv:{key} -->"
    if kind == "router":
        path = change["path"]
        prefix = change.get("prefix", "(none)")
        routes = change.get("routes", "?")
        purpose = change.get("purpose", "")
        entry = (
            f"| `{path}` | `{prefix}` | {routes} | {purpose} "
            f"(Iter {iter_num}, {ts}) | {marker}"
        )
        return marker, entry
    if kind == "envvar":
        name = change["name"]
        purpose = change.get("purpose", "")
        default = change.get("default", "unset")
        entry = (
            f"- `{name}` — {purpose} (default: {default}) "
            f"[Iter {iter_num}, {ts}] {marker}"
        )
        return marker, entry
    if kind == "collection":
        name = change["name"]
        purpose = change.get("purpose", "")
        entry = f"- `{name}` — {purpose} [Iter {iter_num}, {ts}] {marker}"
        return marker, entry
    if kind == "loop_run_log_kind":
        val = change["value"]
        purpose = change.get("purpose", "")
        entry = (
            f"- `loop_run_log kind='{val}'` — {purpose} "
            f"[Iter {iter_num}, {ts}] {marker}"
        )
        return marker, entry
    if kind == "frontend_route":
        path = change["path"]
        component = change.get("component", "")
        purpose = change.get("purpose", "")
        entry = (
            f"- `{path}` → `{component}` — {purpose} "
            f"[Iter {iter_num}, {ts}] {marker}"
        )
        return marker, entry
    if kind == "service":
        path = change["path"]
        purpose = change.get("purpose", "")
        status = change.get("status", "wired")
        entry = (
            f"- `{path}` — {purpose} · status={status} "
            f"[Iter {iter_num}, {ts}] {marker}"
        )
        return marker, entry
    if kind == "bg_job":
        name = change["name"]
        purpose = change.get("purpose", "")
        cadence = change.get("cadence", "?")
        gate = change.get("gate", "always on")
        entry = (
            f"| `{name}` | {purpose} | {cadence} | {gate} · "
            f"[Iter {iter_num}, {ts}] | {marker}"
        )
        return marker, entry
    entry = (
        f"- (UNKNOWN kind={kind}) {json.dumps(change)} "
        f"[Iter {iter_num}, {ts}] {marker}"
    )
    return marker, entry


def append(changes: list[dict], iter_num: int) -> dict:
    """Append changes. Returns a report dict for the caller."""
    if not INV.exists():
        return {"error": "SYSTEM_INVENTORY.md does not exist",
                "path": str(INV)}
    text = INV.read_text()
    appended: list[str] = []
    skipped: list[str] = []
    lines_to_add: list[str] = []
    for ch in changes:
        marker, entry = _format_entry(ch, iter_num)
        if _already_present(text, marker):
            skipped.append(marker)
            continue
        appended.append(marker)
        lines_to_add.append(entry)
    if not lines_to_add:
        return {"appended": [], "skipped": skipped,
                "message": "all entries already present — nothing appended"}
    # Append to a "Ripple appends" section at the very bottom so the
    # main body stays stable. Section is auto-created on first append.
    SECTION_HEADER = "## 🔁 Ripple appends (auto-generated by scripts/inventory_append.py)"
    if SECTION_HEADER not in text:
        text += f"\n\n{SECTION_HEADER}\n\n"
    # Group entries by iteration for readability.
    subhdr = f"### Iter {iter_num} · {_today()}"
    if subhdr not in text:
        text += f"\n{subhdr}\n\n"
    text += "\n".join(lines_to_add) + "\n"
    INV.write_text(text)
    return {
        "appended": appended,
        "skipped":  skipped,
        "message":  f"appended {len(appended)} entries to {INV}",
        "path":     str(INV),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iter", type=int, required=True)
    ap.add_argument("--changes", type=str, required=True,
                    help="JSON list of change dicts")
    args = ap.parse_args()
    try:
        changes = json.loads(args.changes)
    except json.JSONDecodeError as e:
        print(f"ERROR: --changes is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(changes, list):
        print("ERROR: --changes must be a JSON list", file=sys.stderr)
        return 2
    result = append(changes, args.iter)
    print(json.dumps(result, indent=2))
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
