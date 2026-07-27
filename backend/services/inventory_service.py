"""services/inventory_service.py — Iter 328 · SYSTEM_INVENTORY auto-append.

Fire-and-forget helper that keeps SYSTEM_INVENTORY.md in sync with the
codebase. Called from the loop's ship-completion path so every shipped
change gets a chance to update the inventory.

Design constraints (per Ripple-Update Rule):
  • MUST be fail-open — a broken inventory scan MUST NOT block a ship
    or slow down any user task. All exceptions are swallowed with a
    debug log.
  • MUST be idempotent — re-running on the same diff never appends
    the same entry twice. Piggybacks on
    scripts/inventory_append.py's HTML-comment marker system.
  • MUST NOT invent entries — only records what the diff actually
    proves added (a new file at routers/, a new `os.environ.get(...)`
    at a well-known callsite, etc.).

Public surface:
  scan_git_range(base_ref, head_ref) -> list[dict]
      Return the list of inventory-worthy change dicts discovered.

  record_from_git(base_ref, head_ref, iter_num) -> dict
      Scan + append. Returns the append report.

  record_from_git_async(base_ref, head_ref, iter_num) -> None
      Fire-and-forget wrapper for use inside async paths.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Reuse the append module — its formatter + idempotence markers are
# our single source of truth for inventory writes.
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "_inv_append",
        Path(__file__).parent.parent / "scripts" / "inventory_append.py",
    )
    _inv_append = importlib.util.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(_inv_append)  # type: ignore
except Exception as _e:  # noqa: BLE001
    logger.warning("inventory_service: could not import append helper: %r", _e)
    _inv_append = None


# ────────────────────────────────────────────────────────────────────────
# Git diff helpers
# ────────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path("/app")


def _git(args: list[str], timeout: float = 5.0) -> Optional[str]:
    """Run a git command in the repo root. Returns stdout or None on
    failure. Never raises — this whole module must be fail-open."""
    try:
        out = subprocess.check_output(
            ["git"] + args, cwd=str(_REPO_ROOT),
            stderr=subprocess.DEVNULL, timeout=timeout,
        )
        return out.decode("utf-8", errors="replace")
    except Exception:
        return None


def _changed_files(base_ref: str, head_ref: str) -> list[tuple[str, str]]:
    """Return [(status, path)] for files changed between base_ref..head_ref.
    Status is git's A/M/D/R100 etc. Empty on error."""
    raw = _git(["diff", "--name-status", f"{base_ref}..{head_ref}"])
    if not raw:
        return []
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append((parts[0], parts[-1]))
    return out


def _file_added_lines(path: str, base_ref: str, head_ref: str) -> str:
    """Concatenated added-line content (lines starting with '+') for a
    single file between two refs. Used to detect new env vars, new
    collection names, etc. Empty string on error."""
    raw = _git(["diff", f"{base_ref}..{head_ref}", "--", path])
    if not raw:
        return ""
    added = [ln[1:] for ln in raw.splitlines() if ln.startswith("+")
             and not ln.startswith("+++")]
    return "\n".join(added)


# ────────────────────────────────────────────────────────────────────────
# Kind detectors
# ────────────────────────────────────────────────────────────────────────

# Pattern for a new router file: backend/routers/foo.py
_ROUTER_PATH = re.compile(r"^backend/(routers/[a-z_][a-z0-9_]*\.py)$")
# Pattern for a new service file
_SERVICE_PATH = re.compile(r"^backend/(services/[a-z_][a-z0-9_]*\.py)$")
# Pattern for router prefix declaration inside a router file
_ROUTER_PREFIX = re.compile(r'APIRouter\s*\(\s*prefix\s*=\s*["\']([^"\']+)["\']')
# Pattern for env var reads (only capture NEW ones with novel names)
_ENV_READ = re.compile(r'os\.environ(?:\.get)?\(\s*["\']([A-Z][A-Z0-9_]+)["\']')
# Pattern for loop_run_log kind values
_LOOP_KIND = re.compile(r'"kind"\s*:\s*"([a-z_][a-z0-9_]*)"')
# Pattern for db.<collection>. or db["<collection>"]
_COLLECTION = re.compile(r'db\.([a-z_][a-z0-9_]*)|db\[["\']([a-z_][a-z0-9_]*)["\']')


def scan_git_range(base_ref: str, head_ref: str) -> list[dict]:
    """Return inventory-worthy change dicts for what actually landed
    between base_ref..head_ref. Never raises."""
    try:
        changes: list[dict] = []
        seen_env: set[str] = set()
        seen_kinds: set[str] = set()

        for status, path in _changed_files(base_ref, head_ref):
            # New router file (A = added).
            if status == "A":
                m = _ROUTER_PATH.match(path)
                if m:
                    rel = m.group(1)
                    added = _file_added_lines(path, base_ref, head_ref)
                    pm = _ROUTER_PREFIX.search(added)
                    prefix = pm.group(1) if pm else "(none)"
                    routes = len(re.findall(r"^@router\.", added, re.M))
                    changes.append({
                        "kind": "router", "path": rel,
                        "prefix": prefix, "routes": routes,
                        "purpose": "auto-detected new router (verify)",
                    })
                else:
                    # New service file.
                    sm = _SERVICE_PATH.match(path)
                    if sm:
                        changes.append({
                            "kind": "service", "path": sm.group(1),
                            "purpose": "auto-detected new service (verify)",
                            "status": "wired",
                        })

            # For all changed backend python files (added OR modified):
            # sniff for new env vars + new loop_run_log kinds. Only reads
            # ADDED lines so we never falsely flag pre-existing names.
            if path.startswith("backend/") and path.endswith(".py"):
                added = _file_added_lines(path, base_ref, head_ref)
                if not added:
                    continue
                for m in _ENV_READ.finditer(added):
                    name = m.group(1)
                    if name in seen_env:
                        continue
                    seen_env.add(name)
                    changes.append({
                        "kind": "envvar", "name": name,
                        "purpose": f"auto-detected in {path} (verify)",
                        "default": "unset",
                    })
                for m in _LOOP_KIND.finditer(added):
                    v = m.group(1)
                    if v in seen_kinds:
                        continue
                    seen_kinds.add(v)
                    changes.append({
                        "kind": "loop_run_log_kind", "value": v,
                        "purpose": f"auto-detected in {path} (verify)",
                    })
        return changes
    except Exception as e:  # noqa: BLE001
        logger.debug("inventory_service.scan_git_range failed: %r", e)
        return []


# ────────────────────────────────────────────────────────────────────────
# Public entry points
# ────────────────────────────────────────────────────────────────────────


def record_from_git(base_ref: str, head_ref: str, iter_num: int) -> dict:
    """Scan git range + append discovered entries. Returns append report.
    Never raises."""
    if _inv_append is None:
        return {"error": "append module unavailable"}
    try:
        changes = scan_git_range(base_ref, head_ref)
        if not changes:
            return {"appended": [], "skipped": [], "message": "no new entries detected"}
        return _inv_append.append(changes, iter_num)
    except Exception as e:  # noqa: BLE001
        logger.debug("inventory_service.record_from_git failed: %r", e)
        return {"error": repr(e)}


async def record_from_git_async(
    base_ref: str, head_ref: str, iter_num: int,
) -> None:
    """Fire-and-forget async wrapper. Runs the sync git+append work in a
    thread so it never blocks the caller. Swallows every error."""
    try:
        await asyncio.to_thread(record_from_git, base_ref, head_ref, iter_num)
    except Exception as e:  # noqa: BLE001
        logger.debug("inventory_service.record_from_git_async failed: %r", e)
