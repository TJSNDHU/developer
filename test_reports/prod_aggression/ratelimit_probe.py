"""Empirical GitHub secondary-rate-limit probe — iter 212m-179.

Mirrors the EXACT per-fix write pattern of the production bulk-fix
pipeline (branch + blob/tree/commit/ref + draft PR ~= 6 content-
generating calls) with the same 1.5s inter-fix pacing, in runs of
5 / 10 / 20 / 30 units against TJSNDHU/Aurem on throwaway branches.
PRs are closed and branches deleted after each run. Raw 403s are
recorded (no retry masking) so the safe hard cap is empirical.
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, "/app/backend")

import httpx  # noqa: E402
from services.github_api_writer import commit_files  # noqa: E402
from services.loop_safety import create_or_reuse_branch, open_draft_pr  # noqa: E402

TOKEN = json.load(open(
    "/app/test_reports/prod_aggression/matrix_loop_swift.json"
))["final"]["context"]["ship_pending"]["token"]
OWNER, REPO, BASE = "TJSNDHU", "Aurem", "main"
RUNS = [5, 10, 20, 30]
INTER_FIX_DELAY_S = 1.5
BREATHE_EVERY = 10
BREATHE_S = 1.5
COOLDOWN_S = 120
OUT = "/app/test_reports/prod_aggression/ratelimit_probe_results.json"

HEADERS = {"Authorization": f"token {TOKEN}",
           "Accept": "application/vnd.github+json",
           "User-Agent": "aurem-ratelimit-probe"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def fix_unit(i, run_id):
    """One production-shaped fix: branch + 1-file commit + draft PR."""
    branch = f"aurem/rltest-{run_id}-{i}"
    unit = {"i": i, "branch": branch}
    ok, err = await create_or_reuse_branch(
        owner=OWNER, repo=REPO, base_branch=BASE, new_branch=branch,
        token=TOKEN)
    if not ok:
        unit["failed_at"] = "branch"
        unit["error"] = err
        return unit, False
    try:
        await commit_files(
            owner=OWNER, repo=REPO, branch=branch, token=TOKEN,
            files={f"._aurem_rltest/probe_{i}.txt":
                   f"rate-limit probe {run_id} unit {i}\n"},
            commit_message=f"test: rate-limit probe {run_id} unit {i}")
        unit["commit"] = "ok"
    except httpx.HTTPStatusError as e:
        unit["failed_at"] = "commit"
        unit["error"] = f"HTTP_{e.response.status_code}"
        unit["retry_after"] = e.response.headers.get("retry-after")
        unit["ratelimit_remaining"] = e.response.headers.get(
            "x-ratelimit-remaining")
        unit["body"] = (e.response.text or "")[:200]
        return unit, False
    except Exception as e:  # noqa: BLE001
        unit["failed_at"] = "commit"
        unit["error"] = repr(e)[:200]
        return unit, False
    pr_url, pr_err = await open_draft_pr(
        owner=OWNER, repo=REPO, head_branch=branch, base_branch=BASE,
        title=f"[probe] rate-limit test {run_id} #{i}",
        body="Throwaway PR for the empirical GitHub secondary-rate-limit "
             "probe. Auto-closed by the probe script.", token=TOKEN)
    unit["pr"] = pr_url or pr_err
    if pr_err:
        unit["failed_at"] = "pr"
        unit["error"] = pr_err
        return unit, False
    return unit, True


async def cleanup(run_id, n):
    """Close probe PRs + delete probe branches. Best-effort, paced."""
    closed = 0
    deleted = 0
    async with httpx.AsyncClient(timeout=20.0) as c:
        try:
            r = await c.get(
                f"https://api.github.com/repos/{OWNER}/{REPO}/pulls",
                params={"state": "open", "per_page": 100}, headers=HEADERS)
            for pr in (r.json() if r.status_code == 200 else []):
                ref = (pr.get("head") or {}).get("ref") or ""
                if ref.startswith(f"aurem/rltest-{run_id}-"):
                    rr = await c.patch(pr["url"], json={"state": "closed"},
                                       headers=HEADERS)
                    closed += 1 if rr.status_code == 200 else 0
                    await asyncio.sleep(0.8)
        except Exception as e:  # noqa: BLE001
            log(f"cleanup PR pass error: {e!r}")
        for i in range(1, n + 1):
            try:
                rr = await c.delete(
                    f"https://api.github.com/repos/{OWNER}/{REPO}"
                    f"/git/refs/heads/aurem/rltest-{run_id}-{i}",
                    headers=HEADERS)
                deleted += 1 if rr.status_code == 204 else 0
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.8)
    log(f"cleanup {run_id}: closed {closed} PRs, deleted {deleted} branches")


async def main():
    results = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": []}
    for n in RUNS:
        run_id = f"{n}x{int(time.time())}"
        log(f"===== RUN n={n} (id={run_id}) =====")
        run = {"n": n, "run_id": run_id, "units": [], "ok": 0, "failed": 0,
               "first_403_at": None}
        t0 = time.monotonic()
        for i in range(1, n + 1):
            if i > 1:
                await asyncio.sleep(INTER_FIX_DELAY_S)
            if i > 1 and (i - 1) % BREATHE_EVERY == 0:
                await asyncio.sleep(BREATHE_S)
            unit, ok = await fix_unit(i, run_id)
            unit["elapsed_s"] = round(time.monotonic() - t0, 1)
            run["units"].append(unit)
            if ok:
                run["ok"] += 1
                log(f"  unit {i}/{n} OK ({unit['elapsed_s']}s)")
            else:
                run["failed"] += 1
                log(f"  unit {i}/{n} FAILED at {unit.get('failed_at')}: "
                    f"{unit.get('error')} "
                    f"retry_after={unit.get('retry_after')}")
                if "403" in str(unit.get("error", "")) or \
                        "429" in str(unit.get("error", "")):
                    run["first_403_at"] = i
                    log(f"  >>> SECONDARY LIMIT at unit {i} — stopping run")
                    break
        run["duration_s"] = round(time.monotonic() - t0, 1)
        results["runs"].append(run)
        json.dump(results, open(OUT, "w"), indent=2)
        await cleanup(run_id, n)
        if n != RUNS[-1]:
            log(f"cooldown {COOLDOWN_S}s ...")
            await asyncio.sleep(COOLDOWN_S)
    results["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(results, open(OUT, "w"), indent=2)
    log("PROBE COMPLETE")


asyncio.run(main())
