import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
out = {}

# health scan
t0 = time.time()
r = requests.post(f"{BASE}/codebase-health/scan", headers=H,
                  json={"project_id": PID}, timeout=600)
scan_s = round(time.time() - t0, 1)
d = r.json()
out["health_scan"] = {"http": r.status_code, "time_s": scan_s,
                      "keys": list(d.keys()),
                      "score": d.get("score") or (d.get("result") or {}).get("score"),
                      "head": json.dumps(d)[:700]}
print("health_scan:", json.dumps(out["health_scan"], indent=1), flush=True)

# /last — should match
t0 = time.time()
r2 = requests.get(f"{BASE}/codebase-health/last?project_id={PID}", headers=H, timeout=60)
d2 = r2.json()
out["health_last"] = {"http": r2.status_code, "time_s": round(time.time() - t0, 1),
                      "score": d2.get("score") or (d2.get("result") or {}).get("score"),
                      "head": json.dumps(d2)[:500]}
print("health_last:", json.dumps(out["health_last"], indent=1), flush=True)
json.dump(out, open("/app/test_reports/prod_aggression/health_results.json", "w"), indent=1)
