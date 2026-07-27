"""services/loop_ship_diff.py — Iter 328 · Deploy 2

Per-file line diff for ShipPendingCard. Pure helper — no I/O, no async.

Returns a compact list of {path, additions, deletions, is_new, delta_bytes}
suitable for embedding in the awaiting_ship event data + persisted
ship_pending context so the founder sees WHAT is about to ship BEFORE
approving.

Not a full diff renderer — additions/deletions are line-level counts
using the same naive walk as FileDiffPeek.jsx (line-by-line equality),
which is what the legacy ShipConfirmModal used pre-Loop-mode.
"""
from __future__ import annotations

from typing import Optional


def _line_delta(orig: str, new: str) -> tuple[int, int]:
    """Line-level (additions, deletions) between two file bodies.
    Same naive walk as the frontend FileDiffPeek — good enough for
    <2k-line files, which is the ship-preview target."""
    a = (orig or "").split("\n")
    b = (new or "").split("\n")
    add = 0
    dele = 0
    max_len = max(len(a), len(b))
    for i in range(max_len):
        oa = a[i] if i < len(a) else None
        ob = b[i] if i < len(b) else None
        if oa is None:
            add += 1
        elif ob is None:
            dele += 1
        elif oa != ob:
            add += 1
            dele += 1
    return add, dele


def compute_files_diff(
    orig_contents: dict[str, str],
    new_contents:  dict[str, str],
    orig_bytes_by_path: Optional[dict[str, int]] = None,
) -> list[dict]:
    """Build the per-file diff summary shipped in ship_pending payload.

    Args:
        orig_contents: {path -> pre-execution file body}. May be a subset
            of new_contents (rehydration, cache-miss).
        new_contents:  {path -> submitted (about-to-ship) file body}.
        orig_bytes_by_path: fallback byte counts when we don't have the
            original content string (e.g. after rehydration). Lets us
            still show a byte-delta even if line-level diff isn't
            available.

    Returns list of dicts, one per file in new_contents:
        {
          "path":         "backend/foo.py",
          "additions":    int,       # lines added (0 if unknown)
          "deletions":    int,       # lines deleted (0 if unknown)
          "is_new":       bool,      # file didn't exist before ship
          "delta_bytes":  int,       # signed byte delta (+/-)
          "diff_source":  "line" | "bytes" | "unknown",
        }
    """
    obc = orig_bytes_by_path or {}
    out: list[dict] = []
    for path, new_body in (new_contents or {}).items():
        new_body = new_body or ""
        new_bytes = len(new_body)
        orig_body = (orig_contents or {}).get(path)
        # Was the file pre-existing? Prefer content presence; fall back
        # to byte-count map. Both must be missing to declare NEW.
        pre_bytes = int(obc.get(path) or 0)
        is_new = (orig_body is None) and (pre_bytes == 0)
        if orig_body is not None:
            add, dele = _line_delta(orig_body, new_body)
            delta_bytes = new_bytes - len(orig_body)
            src = "line"
        elif pre_bytes > 0:
            # We know the old byte count but not the content — cannot
            # do line-level, but can still show a byte delta so the
            # founder sees "40 KB → 12 KB" style shrink at a glance.
            add = 0
            dele = 0
            delta_bytes = new_bytes - pre_bytes
            src = "bytes"
        elif is_new:
            # Brand-new file: everything is an addition.
            add = new_body.count("\n") + (1 if new_body else 0)
            dele = 0
            delta_bytes = new_bytes
            src = "line"
        else:
            add = 0
            dele = 0
            delta_bytes = 0
            src = "unknown"
        out.append({
            "path":         path,
            "additions":    add,
            "deletions":    dele,
            "is_new":       is_new,
            "delta_bytes":  delta_bytes,
            "diff_source":  src,
        })
    return out
