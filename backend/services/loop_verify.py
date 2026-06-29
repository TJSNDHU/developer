"""
services/loop_verify.py — Iter 212m-62 (Loop Mode Phase C)

Real static-analysis verifier for the LoopEngine.  Runs ruff against
Python files and eslint against JS/TS/JSX/TSX, in a sandboxed temp
directory so user code never leaves /tmp.

API:
    verify_files([{"path": "...", "content": "..."}, ...]) -> dict
        {
          "ok":      bool,
          "results": [{
              "path": str, "ok": bool, "linter": "ruff" | "eslint" | "skip",
              "stdout": str, "stderr": str,
          }, ...],
          "errors":  [<flattened error strings>],
        }

The verifier is INTENDED for the engine's verify phase — Phase A's
prompt-suffix path is untouched.  Returns within ~2 s for repos of
<25 files (the loop cap).  Subprocess timeouts are 8 s each.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# Map file extensions → linter command + JSON output flag.
# stderr/stdout parsed by linter.
_LINTERS: dict[str, tuple[str, list[str]]] = {
    ".py":   ("ruff",   ["check", "--no-fix", "--output-format=concise"]),
    ".pyi":  ("ruff",   ["check", "--no-fix", "--output-format=concise"]),
    ".js":   ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--rule", "no-undef:error",
                         "--rule", "no-unused-vars:warn",
                         "--rule", "no-unreachable:error",
                         "--no-color", "--format", "compact"]),
    ".jsx":  ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--parser-options=ecmaVersion:latest,ecmaFeatures:{jsx:true}",
                         "--rule", "no-undef:error",
                         "--rule", "no-unreachable:error",
                         "--no-color", "--format", "compact"]),
    ".ts":   ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--rule", "no-undef:error",
                         "--no-color", "--format", "compact"]),
    ".tsx":  ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--parser-options=ecmaVersion:latest,ecmaFeatures:{jsx:true}",
                         "--rule", "no-undef:error",
                         "--no-color", "--format", "compact"]),
}
_SUBPROCESS_TIMEOUT_S = 8


def _ext(path: str) -> str:
    return os.path.splitext(path or "")[1].lower()


async def _run(cmd: list[str], cwd: str, timeout: float = _SUBPROCESS_TIMEOUT_S):
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(),
                                                 timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib_suppress():
            proc.kill()
        return 124, b"", b"linter timed out"
    return proc.returncode, stdout, stderr


def contextlib_suppress():
    """asyncio.create_subprocess_exec returns a Process whose kill()
    raises if the child has already exited.  Tiny inline suppressor
    avoids the import dance."""
    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return True
    return _S()


async def verify_files(files: list[dict]) -> dict:
    """Lint each file in a sandboxed temp dir.  Each file gets its own
    subdir so eslint/ruff can't cross-contaminate.  Returns a structured
    report — never raises.

    Iter 212m-131 — Bug #5 fix: linters now run CONCURRENTLY behind a
    semaphore (default cap = 4) instead of one file at a time.  Old
    serial loop took up to ~40 s on a 5-file batch (subprocess timeout
    8 s each); new parallel path is bounded by the slowest single file.
    The `_run()` subprocess call already has its own timeout so a hung
    linter can't take down the whole batch.
    """
    files = [f for f in (files or [])
             if isinstance(f, dict) and f.get("path") and f.get("content") is not None]
    if not files:
        return {"ok": True, "results": [], "errors": []}

    sandbox = tempfile.mkdtemp(prefix="loop_verify_")
    # Cap concurrent linter subprocesses.  4 is generous for our pod
    # sizes while still cutting wall-clock dramatically on bigger
    # batches; one stuck linter only blocks itself, not its siblings.
    sem = asyncio.Semaphore(4)

    async def _lint_one(f: dict) -> tuple[dict, Optional[str]]:
        """Returns (result_row, error_line_or_None)."""
        rel = f["path"]
        ext = _ext(rel)
        linter = _LINTERS.get(ext)
        if not linter:
            return ({
                "path":   rel,
                "ok":     True,
                "linter": "skip",
                "stdout": "",
                "stderr": f"no linter mapping for {ext or '(no ext)'}",
            }, None)
        tool, flags = linter
        safe_rel = rel.replace("..", "_").lstrip("/")
        file_dir = os.path.join(sandbox, os.path.dirname(safe_rel) or ".")
        os.makedirs(file_dir, exist_ok=True)
        disk_path = os.path.join(sandbox, safe_rel)
        try:
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(f["content"])
        except OSError as e:
            return ({"path": rel, "ok": False, "linter": tool,
                     "stdout": "",
                     "stderr": f"write failed: {e}"},
                    f"{rel}: write failed: {e}")
        async with sem:
            rc, so, se = await _run([tool, *flags, disk_path], cwd=sandbox)
        ok = (rc == 0)
        stdout = so.decode(errors="ignore")
        stderr = se.decode(errors="ignore")
        stdout = stdout.replace(disk_path, rel).replace(sandbox + "/", "")
        stderr = stderr.replace(disk_path, rel).replace(sandbox + "/", "")
        row = {"path": rel, "ok": ok, "linter": tool,
               "stdout": stdout, "stderr": stderr}
        if ok:
            return (row, None)
        # First non-empty diagnostic line — keeps the aggregated
        # error list tight even on noisy linters.
        first_err = ""
        for line in (stdout or stderr).strip().splitlines():
            line = line.strip()
            if line:
                first_err = line
                break
        return (row, f"{rel}: {first_err}" if first_err else None)

    try:
        rows = await asyncio.gather(*(_lint_one(f) for f in files))
        results = [r for r, _ in rows]
        errors  = [e for _, e in rows if e]
    finally:
        try:
            shutil.rmtree(sandbox, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("sandbox cleanup failed: %r", e)

    return {
        "ok":      all(r["ok"] for r in results),
        "results": results,
        "errors":  errors,
    }


async def self_heal(file_obj: dict, lint_errors: list[str],
                    user_request: str,
                    user_id: Optional[str] = None) -> Optional[str]:
    """Ask the LLM to rewrite `file_obj['content']` to fix the lint
    errors.  Returns the new content string, or None if the model
    refuses / can't parse.  The engine calls this up to 2 times before
    surfacing to the user (G1)."""
    from services.llm import call_llm_with_meta
    sys_msg = (
        "You are ORA, an AI engineer in self-heal mode.  A file you "
        "wrote failed static analysis.  Rewrite ONLY the file content "
        "to fix the reported errors.  Do not add commentary.  Do not "
        "wrap in code fences.  Preserve all existing functionality "
        "that wasn't responsible for the failure."
    )
    user_msg = (
        f"Original user request:\n{user_request}\n\n"
        f"File path: {file_obj['path']}\n\n"
        f"--- CURRENT CONTENT ---\n{file_obj['content']}\n"
        f"--- END CONTENT ---\n\n"
        f"--- LINT ERRORS ---\n" + "\n".join(lint_errors[:25]) +
        "\n--- END ERRORS ---\n\nReturn the corrected file content only."
    )
    try:
        meta = await call_llm_with_meta(
            system=sys_msg, user=user_msg,
            max_tokens=2500, mode="code",
            user_id=user_id, review_mode="pro",
        )
        out = (meta or {}).get("content", "").strip()
        # Strip stray fences.
        if out.startswith("```"):
            first_nl = out.find("\n")
            if first_nl != -1:
                out = out[first_nl + 1:]
            if out.endswith("```"):
                out = out[:-3].rstrip()
        return out if out else None
    except Exception as e:                              # noqa: BLE001
        logger.warning("self_heal failed for %s: %r", file_obj.get("path"), e)
        return None
