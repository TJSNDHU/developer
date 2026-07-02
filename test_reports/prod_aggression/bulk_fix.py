import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
findings = json.load(open("/app/test_reports/prod_aggression/findings.json"))[1:]  # remaining 3
for f in findings:
    f["category"] = "vanguard"
out = {}

t0 = time.time()
r = requests.post(f"{BASE}/fix-pipeline/preview", headers=H,
                  json={"project_id": PID, "findings": findings}, timeout=60)
out["preview"] = {"s": round(time.time() - t0, 2), "resp": r.json()}
print("preview:", json.dumps(out["preview"])[:300], flush=True)

t0 = time.time()
r = requests.post(f"{BASE}/fix-pipeline/bulk", headers=H,
                  json={"project_id": PID, "findings": findings}, timeout=60)
d = r.json()
job_id = d.get("job_id")
out["bulk_start"] = {"s": round(time.time() - t0, 2), "job_id": job_id, "resp": d}
print("bulk started:", json.dumps(out["bulk_start"])[:300], flush=True)

# watch SSE progress stream (logs check)
events = []
t0 = time.time()
try:
    with requests.get(f"{BASE}/fix-pipeline/stream/{job_id}", headers=H,
                      stream=True, timeout=600) as rs:
        for line in rs.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except Exception:
                continue
            events.append(ev)
            print("EV:", json.dumps(ev)[:220], flush=True)
            if ev.get("type") in ("job_done", "done", "complete") or ev.get("status") in ("done", "completed", "failed"):
                break
except Exception as e:
    print("stream err:", str(e)[:150], flush=True)
out["stream_s"] = round(time.time() - t0, 1)
out["events_n"] = len(events)

r = requests.get(f"{BASE}/fix-pipeline/summary/{job_id}", headers=H, timeout=60)
out["summary"] = r.json()
print("summary:", json.dumps(out["summary"], indent=1)[:1200], flush=True)
json.dump(out, open("/app/test_reports/prod_aggression/bulk_fix.json", "w"), indent=1, default=str)
print("DONE")
