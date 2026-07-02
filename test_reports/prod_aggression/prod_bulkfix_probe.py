"""PROD empirical bulk-fix rate-limit probe — iter 212m-179.

Runs the REAL production fix pipeline on auremcto.com with the founder
account against TJSNDHU/Aurem. Escalating runs: 1 (PAT sanity), 5, 10,
20, 30 findings. Fixes land on aurem/fix-* branches + draft PRs (main
untouched), so the same findings can be reused across runs. Escalation
stops at the first run that shows GitHub 403 / rate-limit failures.
"""
import json
import time

import requests

BASE = "https://auremcto.com/api/aurem-dev"
EMAIL = "teji.ss1986@gmail.com"
PASSWORD = "Singh1986$"
PID = "p_c2b5b8a916"
RUNS = [1, 5, 10, 20, 30]
COOLDOWN_S = 120
OUT = "/app/test_reports/prod_aggression/prod_bulkfix_probe_results.json"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def login():
    r = requests.post(f"{BASE}/auth/login", json={
        "email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    d = r.json()
    tok = d.get("token") or d.get("access_token") or d.get("jwt")
    if not tok:
        raise RuntimeError(f"no token in login resp: {list(d.keys())}")
    return tok


def is_ratelimit_err(err: str) -> bool:
    e = (err or "").lower()
    return ("403" in e or "429" in e or "rate" in e.replace("generate", "")
            or "secondary" in e or "abuse" in e)


def collect_findings(H):
    # Fresh scan already ran — reuse /last when it has findings.
    r = requests.get(f"{BASE}/codebase-health/last?project_id={PID}",
                     headers=H, timeout=60)
    d = r.json() if r.status_code == 200 else {}
    if not (d.get("breakdown")):
        log("no cached scan — running health scan (may take ~20-60s)…")
        r = requests.post(f"{BASE}/codebase-health/scan", headers=H,
                          json={"project_id": PID}, timeout=600)
        r.raise_for_status()
        d = r.json()
    found = []
    for cat, blk in (d.get("breakdown") or {}).items():
        for f in (blk or {}).get("findings") or []:
            if isinstance(f, dict) and f.get("file"):
                f.setdefault("category", cat)
                found.append(f)
    # de-dup by (file, id/title, line)
    seen = set()
    uniq = []
    for f in found:
        k = (f.get("file"), f.get("id") or f.get("title"), f.get("line"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    # spread across distinct files first (one commit per file is the
    # realistic bulk pattern; duplicate files often no-op the LLM)
    by_file = {}
    for f in uniq:
        by_file.setdefault(f["file"], []).append(f)
    spread = [fs[0] for fs in by_file.values()]
    rest = [f for fs in by_file.values() for f in fs[1:]]
    ordered = spread + rest
    log(f"health scan → {len(ordered)} unique findings across "
        f"{len(by_file)} files (score={d.get('score')}, total={d.get('total')})")
    return ordered


def run_bulk(H, findings, n, results):
    batch = findings[:n]
    run = {"n": n, "requested": len(batch), "fixes_ok": 0, "fixes_failed": 0,
           "ratelimit_failures": [], "other_failures": [], "job_id": None}
    t0 = time.time()
    r = requests.post(f"{BASE}/fix-pipeline/bulk", headers=H,
                      json={"project_id": PID, "findings": batch}, timeout=60)
    if r.status_code != 200:
        run["start_error"] = f"HTTP_{r.status_code}: {r.text[:200]}"
        log(f"  bulk start FAILED: {run['start_error']}")
        results["runs"].append(run)
        return run
    job_id = r.json().get("job_id")
    run["job_id"] = job_id
    log(f"  job {job_id} started, polling…")
    deadline = time.time() + n * 120 + 180
    last_completed = -1
    summary = {}
    while time.time() < deadline:
        time.sleep(6)
        try:
            rs = requests.get(f"{BASE}/fix-pipeline/summary/{job_id}",
                              headers=H, timeout=30)
            summary = rs.json()
        except Exception as e:  # noqa: BLE001
            log(f"  poll err: {e!r}")
            continue
        comp = summary.get("completed") or 0
        fail = summary.get("failed") or 0
        status = summary.get("status") or "?"
        if comp + fail != last_completed:
            last_completed = comp + fail
            log(f"  … {comp} ok / {fail} failed / {n} total "
                f"(status={status}, {round(time.time()-t0)}s)")
        if status in ("done", "completed", "failed", "error") or \
                (comp + fail) >= len(batch):
            break
    run["duration_s"] = round(time.time() - t0, 1)
    run["final_status"] = summary.get("status")
    run["fixes_ok"] = summary.get("completed") or 0
    run["fixes_failed"] = summary.get("failed") or 0
    for res in (summary.get("results") or []):
        err = str(res.get("error") or "")
        if not res.get("ok") and err:
            entry = {"index": res.get("index"), "file": res.get("file"),
                     "error": err[:200]}
            if is_ratelimit_err(err):
                run["ratelimit_failures"].append(entry)
            else:
                run["other_failures"].append(entry)
    run["summary_raw"] = {k: summary.get(k) for k in
                          ("status", "completed", "failed", "total")}
    results["runs"].append(run)
    log(f"  RUN n={n} done in {run['duration_s']}s → ok={run['fixes_ok']} "
        f"failed={run['fixes_failed']} ratelimit_hits="
        f"{len(run['ratelimit_failures'])}")
    return run


def main():
    results = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": []}
    tok = login()
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    log("login OK")
    findings = collect_findings(H)
    json.dump(findings, open(
        "/app/test_reports/prod_aggression/prod_probe_findings.json", "w"),
        indent=1)
    if len(findings) < 30:
        log(f"WARNING: only {len(findings)} findings available; "
            "largest run will be capped")
    results["findings_available"] = len(findings)

    for n in RUNS:
        n_eff = min(n, len(findings))
        log(f"===== RUN n={n_eff} =====")
        run = run_bulk(H, findings, n_eff, results)
        json.dump(results, open(OUT, "w"), indent=2)
        if run.get("start_error") and "bulk_limit_exceeded" in str(
                run.get("start_error")):
            log("prod already enforces a bulk cap — stopping escalation")
            break
        if run["ratelimit_failures"]:
            log(f">>> RATE LIMIT HIT in run n={n_eff} — "
                f"first at fix #{run['ratelimit_failures'][0].get('index')}. "
                "Stopping escalation.")
            break
        if n != RUNS[-1]:
            log(f"cooldown {COOLDOWN_S}s…")
            time.sleep(COOLDOWN_S)

    results["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(results, open(OUT, "w"), indent=2)
    log("PROBE COMPLETE")


main()
