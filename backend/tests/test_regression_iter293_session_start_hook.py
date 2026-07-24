"""
Iter 293 — session-start dashboard regression.

Locks:
  1. The script exists, is executable, and exits 0 on --no-net.
  2. Its default output is EXACTLY 3 lines (the founder-agreed
     "no ceremony" contract).
  3. --json mode produces well-formed JSON with the four required
     top-level keys.
  4. AGENTS.md references the script by exact path so a rename
     doesn't silently strand the discipline.
  5. docs/environments.md prod-DB row no longer says "likely" —
     the honesty upgrade from iter293 is locked.

# static-grep-ok: iter293 dashboard regression — the AGENTS.md and
# environments.md doc-shape assertions are inherently STATIC_GREP;
# subprocess-based behavioural checks are also included.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HOOK = "/app/backend/scripts/session_start_dashboard.py"


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def test_session_hook_exists_and_is_executable():
    assert os.path.isfile(_HOOK)
    assert os.access(_HOOK, os.X_OK), \
        "session_start_dashboard.py must be executable"


def test_session_hook_default_output_is_exactly_three_lines():
    """Behavioural — run the script, assert output shape."""
    proc = subprocess.run(
        [sys.executable, _HOOK, "--no-net"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 3, (
        f"expected exactly 3 lines, got {len(lines)}:\n{proc.stdout}"
    )
    assert lines[0].startswith("[static-vs-behavioural]")
    assert lines[1].startswith("[mock-reality-check]")
    assert lines[2].startswith("[environment-ledger]")


def test_session_hook_json_mode_shape():
    """Behavioural — --json emits parseable JSON with expected keys."""
    proc = subprocess.run(
        [sys.executable, _HOOK, "--json", "--no-net"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    for k in ("generated_at", "style", "probe", "env_ledger_verified"):
        assert k in data, f"missing key: {k}"
    style = data["style"]
    for k in ("static_grep", "total", "static_grep_pct", "baseline_pct"):
        assert k in style, f"style missing key: {k}"


def test_session_hook_never_blocks_on_network_failure():
    """--no-net path must succeed even if the outside internet is
    completely unreachable. This is critical because the hook runs
    at session start; a hang there degrades the whole discipline."""
    proc = subprocess.run(
        [sys.executable, _HOOK, "--no-net"],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0
    assert "SKIPPED" in proc.stdout


def test_agents_md_references_hook_by_exact_path():
    src = _read("/app/AGENTS.md")
    assert "backend/scripts/session_start_dashboard.py" in src, (
        "AGENTS.md must reference the session-start hook by exact "
        "path so a rename doesn't silently strand the discipline"
    )


def test_environments_md_no_longer_says_likely_for_prod_db():
    """iter293 upgraded the ledger — 'likely X' guess replaced with
    explicit 'UNVERIFIED from this pod' + the founder-runnable curl."""
    src = _read("/app/docs/environments.md")
    # Look for the specific mistake pattern in the Mongo table row.
    mongo_section = src.split("## 2.")[0]        # up to next section
    assert "Likely" not in mongo_section, (
        "environments.md still guesses prod db_name with 'Likely' — "
        "iter293 required an honest 'UNVERIFIED' + curl instructions"
    )
    assert "UNVERIFIED" in mongo_section, (
        "environments.md must explicitly mark prod db_name as "
        "UNVERIFIED from this pod"
    )
    # And the founder-runnable curl must be present so verification
    # is a copy-paste away, not a scavenger hunt.
    assert "/loop/_diagnostics" in src
    assert "Authorization: Bearer" in src


def test_environments_md_preview_db_is_confirmed():
    """The preview DB name IS verifiable from this pod, and iter293
    confirmed it. The row must say 'confirmed' so the honest signal
    is preserved."""
    src = _read("/app/docs/environments.md")
    assert "aurem_dev" in src
    assert "confirmed" in src.lower()
