"""
services/personal_track_smoke.py — Iter 212m-242

Founder-only infra smoke test for the Personal Track pipeline.
Runs each step independently and reports pass/fail/not_configured
PER STEP so a bad token points at exactly one step, not a vague
"pipeline failed".

Steps:
  0. preflight        — which of the 3 token groups are configured
  1. draft            — insert a minimal static draft (no LLM — LLM has
                        its own canary at /scaffold/admin/llm-health)
  2. github_repo      — create org repo + push files
  3. vercel_deploy    — create Vercel project linked to that repo
  4. managed_db       — (a) shared-Mongo write/read/delete roundtrip
                        (b) Supabase Management API token validation
                        (GET /v1/organizations — no project provisioned,
                        that takes minutes and bills real money)
  5. cleanup          — best-effort: delete smoke repo, vercel project,
                        draft doc. Never fails the run.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import httpx

logger = logging.getLogger("aurem.smoke")

_SMOKE_FILES = [
    {"path": "index.html",
     "content": ("<!doctype html><html><head><title>AUREM smoke</title></head>"
                 "<body><h1>AUREM Personal Track smoke test</h1>"
                 "<p>Safe to delete.</p></body></html>")},
    {"path": "README.md",
     "content": "# AUREM smoke-test repo\nAuto-created by the founder smoke test. Safe to delete.\n"},
]


def _step(name: str, status: str, detail, started: float) -> dict:
    return {
        "name":       name,
        "status":     status,          # pass | fail | not_configured | skipped
        "detail":     detail,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _preflight() -> dict:
    """Exact env-var-level report per token group."""
    groups = {
        "github_org": ["AUREM_ORG_NAME", "AUREM_ORG_GITHUB_APP_TOKEN"],
        "vercel":     ["AUREM_VERCEL_PLATFORM_TOKEN", "VERCEL_PLATFORM_TEAM_ID"],
        "supabase":   ["SUPABASE_MANAGEMENT_TOKEN", "SUPABASE_ORG_ID"],
    }
    out = {}
    for group, keys in groups.items():
        missing = [k for k in keys if not (os.environ.get(k) or "").strip()]
        out[group] = {"configured": not missing, "missing": missing}
    return out


async def run_smoke(db, user: dict, cleanup: bool = True) -> dict:
    steps: list[dict] = []
    run_id = uuid.uuid4().hex[:8]
    user_id = user["user_id"]

    # ── Step 0: preflight ─────────────────────────────────────────
    t0 = time.time()
    pre = _preflight()
    all_configured = all(g["configured"] for g in pre.values())
    steps.append(_step(
        "preflight",
        "pass" if all_configured else "not_configured",
        pre, t0,
    ))

    # ── Step 1: draft ─────────────────────────────────────────────
    t0 = time.time()
    draft_id = f"smoke{run_id}"
    draft_ok = False
    try:
        now = time.time()
        await db.scaffold_drafts.insert_one({
            "draft_id":       draft_id,
            "user_id":        user_id,
            "brief":          "AUREM infra smoke test — safe to delete",
            "stack_detected": "plain-html",
            "files":          _SMOKE_FILES,
            "status":         "draft",
            "smoke_test":     True,
            "created_at":     now,
            "updated_at":     now,
        })
        draft_ok = True
        steps.append(_step("draft", "pass",
                           {"draft_id": draft_id, "files": len(_SMOKE_FILES),
                            "note": "static files — LLM covered by /admin/llm-health canary"},
                           t0))
    except Exception as e:                                    # noqa: BLE001
        steps.append(_step("draft", "fail", f"mongo insert failed: {e!r:.200}", t0))

    # ── Step 2: GitHub repo ───────────────────────────────────────
    from services import github_org_client as gh
    t0 = time.time()
    repo_name = None
    repo_full_name = None
    if not pre["github_org"]["configured"]:
        steps.append(_step("github_repo", "not_configured",
                           {"missing": pre["github_org"]["missing"]}, t0))
    elif not draft_ok:
        steps.append(_step("github_repo", "skipped", "draft step failed", t0))
    else:
        try:
            created = await gh.create_org_repo(
                name=f"aurem-smoke-{run_id}",
                description="AUREM founder smoke test — safe to delete",
                private=True,
            )
            if not created.get("ok"):
                steps.append(_step("github_repo", "fail", created, t0))
            else:
                repo_name = created["name"] if "name" in created else f"aurem-smoke-{run_id}"
                repo_full_name = created["full_name"]
                push = await gh.push_files_bulk(
                    repo_name=repo_name,
                    files=_SMOKE_FILES,
                    commit_message="[AUREM smoke] initial files",
                    branch=created.get("default_branch") or "main",
                )
                if push.get("ok"):
                    steps.append(_step("github_repo", "pass",
                                       {"repo": repo_full_name,
                                        "html_url": created.get("html_url"),
                                        "files_pushed": push.get("pushed")}, t0))
                else:
                    steps.append(_step("github_repo", "fail",
                                       {"stage": "push", "push": push}, t0))
        except Exception as e:                                # noqa: BLE001
            steps.append(_step("github_repo", "fail", f"{type(e).__name__}: {e}"[:300], t0))

    repo_step_passed = steps[-1]["status"] == "pass"

    # ── Step 3: Vercel deploy ─────────────────────────────────────
    from services import vercel_platform_deploy as vc
    t0 = time.time()
    vercel_project_id = None
    if not pre["vercel"]["configured"]:
        steps.append(_step("vercel_deploy", "not_configured",
                           {"missing": pre["vercel"]["missing"]}, t0))
    elif not repo_step_passed:
        steps.append(_step("vercel_deploy", "skipped",
                           "github_repo step did not pass", t0))
    else:
        try:
            dep = await vc.deploy_personal_track(
                user_id=user_id,
                project_id=f"smoke-{run_id}",
                github_full_name=repo_full_name,
                framework=None,
                display_name=f"smoke-{run_id}",
            )
            if dep.get("ok"):
                vercel_project_id = dep.get("vercel_project_id")
                steps.append(_step("vercel_deploy", "pass",
                                   {"vercel_project_id": vercel_project_id,
                                    "live_url": dep.get("live_url"),
                                    "note": "Vercel builds main automatically via webhook"}, t0))
            else:
                steps.append(_step("vercel_deploy", "fail", dep, t0))
        except Exception as e:                                # noqa: BLE001
            steps.append(_step("vercel_deploy", "fail", f"{type(e).__name__}: {e}"[:300], t0))

    # ── Step 4a: shared managed-DB (Mongo) write/read roundtrip ──
    t0 = time.time()
    try:
        probe = {"_smoke_id": run_id, "value": "ping", "ts": time.time()}
        await db.smoke_test_kv.insert_one(dict(probe))
        got = await db.smoke_test_kv.find_one({"_smoke_id": run_id}, {"_id": 0})
        await db.smoke_test_kv.delete_many({"_smoke_id": run_id})
        if got and got.get("value") == "ping":
            steps.append(_step("managed_db_shared", "pass",
                               "write → read → delete roundtrip OK on shared Mongo", t0))
        else:
            steps.append(_step("managed_db_shared", "fail",
                               f"read-back mismatch: {got}", t0))
    except Exception as e:                                    # noqa: BLE001
        steps.append(_step("managed_db_shared", "fail", f"{type(e).__name__}: {e}"[:300], t0))

    # ── Step 4b: Supabase Management API token check ──────────────
    t0 = time.time()
    if not pre["supabase"]["configured"]:
        steps.append(_step("supabase_mgmt_token", "not_configured",
                           {"missing": pre["supabase"]["missing"]}, t0))
    else:
        token = os.environ["SUPABASE_MANAGEMENT_TOKEN"].strip()
        org_id = os.environ["SUPABASE_ORG_ID"].strip()
        try:
            async with httpx.AsyncClient(timeout=20.0) as cli:
                r = await cli.get(
                    "https://api.supabase.com/v1/organizations",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code == 200:
                orgs = r.json()
                ids = [o.get("id") for o in orgs if isinstance(o, dict)]
                if org_id in ids:
                    steps.append(_step("supabase_mgmt_token", "pass",
                                       {"orgs_visible": len(ids),
                                        "org_id_found": True,
                                        "note": "token valid — full project provisioning not "
                                                "run in smoke (takes minutes, bills money)"}, t0))
                else:
                    steps.append(_step("supabase_mgmt_token", "fail",
                                       {"reason": "org_id_not_in_token_scope",
                                        "configured_org_id": org_id,
                                        "orgs_visible": ids}, t0))
            elif r.status_code == 401:
                steps.append(_step("supabase_mgmt_token", "fail",
                                   "401 — SUPABASE_MANAGEMENT_TOKEN invalid/expired", t0))
            else:
                steps.append(_step("supabase_mgmt_token", "fail",
                                   f"supabase_{r.status_code}: {r.text[:200]}", t0))
        except Exception as e:                                # noqa: BLE001
            steps.append(_step("supabase_mgmt_token", "fail",
                               f"{type(e).__name__}: {e}"[:300], t0))

    # ── Step 5: cleanup (best-effort, never fails the run) ────────
    t0 = time.time()
    if cleanup:
        cleaned = {}
        if repo_name:
            try:
                res = await gh.delete_org_repo(repo_name)
                cleaned["repo"] = "deleted" if res.get("ok") else f"delete_failed: {res}"
            except Exception as e:                            # noqa: BLE001
                cleaned["repo"] = f"delete_error: {e!r:.150}"
        if vercel_project_id:
            try:
                async with httpx.AsyncClient(timeout=20.0) as cli:
                    r = await cli.delete(
                        f"https://api.vercel.com/v9/projects/{vercel_project_id}"
                        f"?teamId={os.environ.get('VERCEL_PLATFORM_TEAM_ID', '').strip()}",
                        headers={"Authorization":
                                 f"Bearer {os.environ.get('AUREM_VERCEL_PLATFORM_TOKEN', '').strip()}"},
                    )
                cleaned["vercel"] = "deleted" if r.status_code in (200, 204) \
                    else f"delete_failed_{r.status_code}"
            except Exception as e:                            # noqa: BLE001
                cleaned["vercel"] = f"delete_error: {e!r:.150}"
        if draft_ok:
            try:
                await db.scaffold_drafts.delete_one({"draft_id": draft_id})
                cleaned["draft"] = "deleted"
            except Exception as e:                            # noqa: BLE001
                cleaned["draft"] = f"delete_error: {e!r:.150}"
        steps.append(_step("cleanup", "pass", cleaned or "nothing to clean", t0))
    else:
        steps.append(_step("cleanup", "skipped", "cleanup=false requested", t0))

    infra_steps = [s for s in steps if s["name"] not in ("cleanup",)]
    failed = [s["name"] for s in infra_steps if s["status"] == "fail"]
    not_conf = [s["name"] for s in infra_steps if s["status"] == "not_configured"]
    return {
        "ok":              not failed and not not_conf,
        "run_id":          run_id,
        "failed_steps":    failed,
        "not_configured":  not_conf,
        "steps":           steps,
        "ran_at":          time.time(),
    }
