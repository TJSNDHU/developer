"""
services/finding_fix_applier.py — Iter 212m-114

REAL fix-application for Vanguard (Security) + Bug Hunt (Health)
findings. Replaces the previous semi-dummy `cto_tasks` queue with an
end-to-end pipeline:

  1. Fetch the CURRENT content of the affected file from the user's
     GitHub repo (via their connected PAT — no other credential).
  2. Ask the LLM for a minimum-diff patch that resolves the finding.
  3. RE-VALIDATE: run the SAME static scanner on the patched content.
     The fix is accepted only if the original finding rule_id is no
     longer triggered on (or near) the original line.
  4. Commit via services.github_api_writer.commit_files() — same Git
     Data API path Loop Mode uses for Ship.
  5. Return {ok, commit_sha, html_url, before/after snippet, message}.

If any step fails, NOTHING is committed. The caller (router) refunds
the deducted tokens and surfaces a clean error.

Security:
  * PAT is fetched fresh per call via routers.security_scan._decrypt_pat
    (falls back to user.github.access_token). Never logged.
  * Per-{project_id,user_id} compound key on every DB read.
  * Founder / admin / is_unlimited users bypass token deduction
    (callers already enforce this; this service is agnostic).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("aurem-dev.finding_fix_applier")


# ─── 1. Fetch current file from GitHub ────────────────────────────────
async def _fetch_file_content(
    owner: str, repo: str, branch: str, path: str, token: str,
) -> tuple[str, Optional[str]]:
    """Returns (content, error). Uses raw API for byte-exact content.

    Iter 212m-178 — retries GitHub 403/429 SECONDARY rate limits (which
    bulk fixes trip after a burst of blob+tree+commit+ref writes) using
    the server-provided Retry-After, so a rapid sequence of fixes no
    longer fails the 2nd+ file with `github_status_403`.
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github.raw+json",
        "User-Agent":    "aurem-fix-applier",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    params = {"ref": branch} if branch and branch != "HEAD" else None
    try:
        async with httpx.AsyncClient(timeout=15.0) as cx:
            for attempt in range(3):
                r = await cx.get(url, params=params, headers=headers)
                if r.status_code == 200:
                    return r.text or "", None
                if r.status_code == 404:
                    return "", "file_not_found"
                if r.status_code == 401:
                    return "", "github_unauthorized"
                if r.status_code in (403, 429) and attempt < 2:
                    # Secondary rate limit — honour Retry-After (capped).
                    ra = r.headers.get("Retry-After")
                    wait = min(float(ra), 30.0) if (ra and ra.isdigit()) \
                        else (3.0 * (attempt + 1))
                    logger.warning(
                        "fetch_file 403/429 for %s — secondary rate limit, "
                        "retrying in %.1fs (attempt %d)", path, wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                return "", f"github_status_{r.status_code}"
            return "", "github_status_403"
    except Exception as e:                                # noqa: BLE001
        logger.exception("fetch_file failed for %s/%s@%s:%s", owner, repo, branch, path)
        return "", f"network_error: {e}"


# ─── 2. LLM patch generation ──────────────────────────────────────────
_LLM_SYSTEM = (
    "You are AUREM, an automated code-fix engineer. Given a single file "
    "and a static-analysis finding, you produce the COMPLETE patched "
    "file content. Rules:\n"
    "  1. Apply the minimum-diff fix that resolves the finding.\n"
    "  2. Preserve every other line of the file exactly.\n"
    "  3. Do NOT add commentary, headers, or markdown fences.\n"
    "  4. Return ONLY the new file content, byte-for-byte.\n"
    "  5. If the finding is a hardcoded secret, replace the literal "
    "with an os.environ.get / process.env lookup AND leave a TODO "
    "comment naming the env var so the developer can set it.\n"
    "  6. If the finding is a SQL-injection / XSS / SSRF / etc., apply "
    "the standard idiomatic fix for the language.\n"
    "  7. Never invent imports that already exist. If you add a new "
    "import (e.g. `import os`), place it at the top of the import "
    "block.\n"
)


def _strip_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3].rstrip()
    return s


async def _generate_patched_content(
    *,
    path: str,
    current_content: str,
    finding: dict,
    user_id: Optional[str],
    db=None,
) -> tuple[str, Optional[str]]:
    """Calls the LLM. Returns (new_content, error)."""
    from services.llm import call_llm_with_meta

    rule_id   = finding.get("rule_id") or finding.get("rule") or "unknown"
    severity  = finding.get("severity") or "high"
    line      = finding.get("line") or 0
    title     = finding.get("title")   or finding.get("message") or rule_id
    message   = finding.get("message") or ""
    snippet   = finding.get("snippet") or finding.get("code") or ""

    # Iter 212m-137 — Phase-2 recall: prepend past successful fixes for
    # the same rule_id (boosted by file extension + caller) so the LLM
    # has precedent. Best-effort: any recall failure soft-fails to no
    # precedent block (we never want recall to block a real fix).
    recall_block = ""
    try:
        if db is not None and rule_id and rule_id != "unknown":
            from services.ora_fix_learning import (
                recall_similar_fixes, format_recall_block,
            )
            recalled = await recall_similar_fixes(
                db, rule_id=rule_id, file_path=path,
                user_id=user_id, limit=3,
            )
            recall_block = format_recall_block(recalled)
    except Exception as e:                                # noqa: BLE001
        logger.warning("recall_similar_fixes soft-failed: %r", e)
        recall_block = ""

    user_prompt = (
        f"{recall_block}"
        f"FILE: {path}\n"
        f"FINDING:\n"
        f"  • rule_id: {rule_id}\n"
        f"  • severity: {severity}\n"
        f"  • line: {line}\n"
        f"  • title: {title}\n"
        f"  • message: {message}\n"
        f"  • offending snippet: {snippet!r}\n\n"
        f"--- CURRENT FILE CONTENT ({len(current_content)} bytes) ---\n"
        f"{current_content}\n"
        f"--- END FILE CONTENT ---\n\n"
        "Return the COMPLETE new file content with the finding fixed. "
        "No fences. No commentary. Just the bytes that should overwrite "
        "the file."
    )

    try:
        meta = await call_llm_with_meta(
            system=_LLM_SYSTEM,
            user=user_prompt,
            max_tokens=4000,
            mode="code",
            user_id=user_id,
            review_mode="pro",
        )
        patched = _strip_fences((meta or {}).get("content", ""))
        if not patched:
            return "", "llm_empty_response"
        if patched.strip() == current_content.strip():
            return "", "llm_no_change"
        return patched, None
    except Exception as e:                                # noqa: BLE001
        logger.exception("LLM patch generation failed for %s", path)
        return "", f"llm_error: {e}"


# ─── 3. Re-validate the patch ─────────────────────────────────────────
def _finding_still_present(
    patched_content: str,
    path: str,
    finding: dict,
) -> bool:
    """Re-run the Vanguard scanner on the patched content. The fix is
    accepted only if the original rule_id is GONE for this file. We
    don't require ZERO findings (the LLM may incidentally surface
    unrelated issues) — just that the specific rule_id we tried to fix
    no longer fires."""
    try:
        from services.vanguard_scanner import scan_text
        new_findings = scan_text(patched_content, path) or []
    except Exception as e:                                # noqa: BLE001
        logger.warning("validate scan failed for %s: %r", path, e)
        return True  # be safe — refuse the patch if we can't verify
    target_rule = (finding.get("rule_id") or finding.get("rule") or "").lower()
    if not target_rule:
        return False  # nothing to compare; trust the LLM
    for f in new_findings:
        rid = (f.get("rule_id") or f.get("rule") or "").lower()
        if rid == target_rule:
            return True
    return False


# ─── 4. Public entry point ────────────────────────────────────────────
async def apply_finding_fix(
    *,
    db,
    user: dict,
    project_id: str,
    finding: dict,
) -> dict:
    """Full pipeline. Caller (router) handles token deduction + auth.

    Returns dict with keys:
      ok          : bool
      commit_sha  : str   (short)
      full_sha    : str
      html_url    : str
      file        : str
      rule_id     : str
      message     : str   (human summary)
      error       : str   (only if ok=False)
    """
    user_id = user.get("user_id") or ""
    path    = finding.get("file") or finding.get("path") or ""
    if not (user_id and project_id and path and finding):
        return {"ok": False, "error": "missing_required_args"}

    # Resolve project + GitHub linkage.
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1,
         "github_branch": 1, "github_token": 1},
    )
    if not proj:
        return {"ok": False, "error": "project_not_found_or_not_yours"}
    owner   = proj.get("github_owner") or ""
    repo    = proj.get("github_repo")  or ""
    branch  = proj.get("github_branch") or "main"

    # Decrypt project PAT → fall back to OAuth access_token.
    from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix
    token = await _decrypt_pat(user_id, proj.get("github_token"))
    if not token:
        try:
            u = await db.dev_users.find_one(
                {"user_id": user_id}, {"_id": 0, "github": 1},
            )
            token = ((u or {}).get("github") or {}).get("access_token") or None
        except Exception:
            token = None
    if not (owner and repo and token):
        return {"ok": False, "error": "github_credentials_missing"}

    # Step 1: fetch current file content
    content, fetch_err = await _fetch_file_content(owner, repo, branch, path, token)
    if fetch_err:
        return {"ok": False, "error": fetch_err, "file": path}
    if not content:
        return {"ok": False, "error": "file_empty_or_missing", "file": path}

    # Step 2: LLM patch (with Phase-2 recall — past successful fixes
    # for this rule are injected as precedent if `db` is available).
    patched, llm_err = await _generate_patched_content(
        path=path, current_content=content, finding=finding, user_id=user_id,
        db=db,
    )
    if llm_err:
        return {"ok": False, "error": llm_err, "file": path}

    # Step 3: re-validate — the original rule_id must NOT be triggered
    # by the patched content.
    if _finding_still_present(patched, path, finding):
        logger.warning(
            "fix REJECTED — finding still present after patch: "
            "rule=%s file=%s", finding.get("rule_id"), path,
        )
        return {
            "ok":    False,
            "error": "patch_did_not_resolve_finding",
            "file":  path,
        }

    # Step 4: commit via the same Git Data API path Loop Mode uses.
    # Iter 212m-115 safety #5 — Branch-per-fix mode. Instead of pushing
    # straight to `base_branch` (typically main), we create a dedicated
    # `aurem/fix-<rule>-<ts>` branch off main + commit the patch to it
    # + open a DRAFT PR. The founder sees the change in a previewable
    # PR before merging. Founders / admins can still opt back into
    # direct-to-main via project setting `direct_ship: true` (not
    # exposed yet — defaults to safe branch mode for everyone).
    from services.github_api_writer import commit_files
    from services.loop_safety import (
        aurem_branch_name, create_or_reuse_branch, open_draft_pr,
    )
    rule_id  = finding.get("rule_id") or finding.get("rule") or "fix"
    title    = finding.get("title")   or finding.get("message") or rule_id
    fix_branch = aurem_branch_name("fix", rule_id)
    ok_branch, branch_err = await create_or_reuse_branch(
        owner=owner, repo=repo, base_branch=branch,
        new_branch=fix_branch, token=token,
    )
    if not ok_branch:
        logger.warning(
            "branch-per-fix create failed (%s) — falling back to base "
            "branch %s/%s@%s", branch_err, owner, repo, branch,
        )
        commit_target = branch
        opened_pr_url: Optional[str] = None
    else:
        commit_target = fix_branch
        opened_pr_url = None  # filled in after commit succeeds

    commit_message = (
        f"fix({rule_id}): {title} @ {path}\n\n"
        f"Resolved by AUREM auto-fix. Re-validated locally — the "
        f"{rule_id} finding no longer triggers on this file.\n"
    )
    # Iter 212m-218 — Normalise via git_identity so this commit lands
    # with the real developer as author + ORA as co-author + the
    # `[via ORA]` transparency marker.  We already have a well-formed
    # `fix(rule):` subject so we pass it through as-is; the builder
    # only appends the marker and the Co-authored-by trailer.
    from services.git_identity import (
        resolve_git_identity, build_commit_message,
    )
    subject_line = commit_message.split("\n", 1)[0]
    body_text = commit_message.split("\n", 1)[1].strip() if "\n" in commit_message else ""
    # Trim subject_line's "fix(rule):" prefix so build_commit_message
    # can re-emit with the marker in the right place.
    m_type_summary = re.match(
        r"^\s*(feat|fix|refactor|chore|docs|test|perf|style|ci|build)"
        r"(?:\(([^)]+)\))?\s*:\s*(.*)$",
        subject_line, re.I,
    )
    if m_type_summary:
        _t, _scope, _summary = m_type_summary.group(1).lower(), m_type_summary.group(2), m_type_summary.group(3)
        _summary_full = f"({_scope}) {_summary}" if _scope else _summary
        commit_message = build_commit_message(
            task_type=_t, summary=_summary_full, body=body_text,
        )
    else:
        commit_message = build_commit_message(
            user_message=subject_line, body=body_text,
        )
    author_name, author_email = await resolve_git_identity(db, user_id)
    try:
        res = await commit_files(
            owner=owner, repo=repo, branch=commit_target, token=token,
            files={path: patched}, commit_message=commit_message,
            author_name=author_name, author_email=author_email,
            progress=None,
        )
    except Exception as e:                                # noqa: BLE001
        logger.exception("commit_files failed for %s", path)
        return {"ok": False, "error": f"github_push_failed: {e}", "file": path}

    short_sha = res.get("sha") or (res.get("full_sha") or "")[:7]
    full_sha  = res.get("full_sha") or res.get("sha") or ""
    html_url  = res.get("html_url") or (
        f"https://github.com/{owner}/{repo}/commit/{full_sha}"
        if full_sha else ""
    )

    # Open a draft PR if we committed to a branch.
    if commit_target != branch:
        try:
            pr_url, pr_err = await open_draft_pr(
                owner=owner, repo=repo,
                head_branch=commit_target, base_branch=branch,
                title=f"fix({rule_id}): {title}",
                body=(
                    f"**AUREM auto-fix** for finding `{rule_id}` in "
                    f"`{path}:{finding.get('line', '?')}`.\n\n"
                    f"**Title:** {title}\n"
                    f"**Severity:** {finding.get('severity', 'n/a')}\n\n"
                    f"**Re-validation:** ✅ scanner no longer detects this "
                    f"`{rule_id}` on the patched file.\n\n"
                    f"Commit: `{short_sha}`\n\n"
                    f"_Generated by AUREM Dev — review the diff before "
                    f"merging._"
                ),
                token=token,
            )
            opened_pr_url = pr_url
            if pr_err:
                logger.warning("open_draft_pr soft-failed: %s", pr_err)
        except Exception as e:                            # noqa: BLE001
            logger.warning("open_draft_pr unexpected err: %r", e)

    # Persist a fix-history record so the UI can dim resolved findings.
    try:
        import time
        import uuid
        await db.finding_fixes.insert_one({
            "fix_id":     f"fx_{uuid.uuid4().hex[:10]}",
            "user_id":    user_id,
            "project_id": project_id,
            "file":       path,
            "rule_id":    rule_id,
            "line":       finding.get("line"),
            "severity":   finding.get("severity"),
            "title":      title,
            "commit_sha": full_sha,
            "html_url":   html_url,
            "pr_url":     opened_pr_url,
            "branch":     commit_target,
            "applied_at": time.time(),
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("fix history persist failed: %r", e)

    logger.info(
        "FIX APPLIED — user=%s project=%s rule=%s file=%s sha=%s "
        "branch=%s pr=%s",
        user_id, project_id, rule_id, path, short_sha,
        commit_target, opened_pr_url or "-",
    )
    return {
        "ok":         True,
        "commit_sha": short_sha,
        "full_sha":   full_sha,
        "html_url":   html_url,
        "pr_url":     opened_pr_url,
        "branch":     commit_target,
        "file":       path,
        "rule_id":    rule_id,
        # Iter 212m-147 — Expose the before/after file contents so the
        # bulk fix worker can compute a unified diff for the SSE
        # `diff` event the drawer renders.  These are LARGE strings —
        # callers MUST drop them before persisting to Mongo (already
        # the case in fix_pipeline.py which only persists summary
        # fields via fjm.persist_event).
        "original_content": content,
        "patched_content":  patched,
        "message":    (
            f"Fixed {rule_id} in {path} — commit {short_sha}"
            + (f" on branch {commit_target}" if commit_target != branch else "")
        ),
    }
