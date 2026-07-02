import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
results = []

tasks = [
    ("council-B-analyze", "Analyze the health of this codebase", "B"),
    ("council-C-contributing", "Write a short CONTRIBUTING.md for this repo", "C"),
    ("council-A-validation", "Check auth routes for missing input validation", "A"),
]
for label, prompt, expect in tasks:
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/chat/send", headers=H, json={
            "prompt": prompt, "project_id": PID, "mode": "pro",
            "session_id": f"qa-council-{label}",
        }, timeout=420)
        el = round(time.time() - t0, 1)
        d = r.json()
        rec = {"label": label, "expect_council": expect, "http": r.status_code,
               "time_s": el, "council": d.get("council"), "task_type": d.get("task_type"),
               "provider": d.get("provider"), "iterations": d.get("iterations"),
               "content_head": (d.get("content") or "")[:200]}
    except Exception as e:
        rec = {"label": label, "expect_council": expect, "error": str(e),
               "time_s": round(time.time() - t0, 1)}
    results.append(rec)
    print(json.dumps(rec)[:600], flush=True)

# Ask Advisor
t0 = time.time()
try:
    r = requests.post(f"{BASE}/chat/send", headers=H, json={
        "prompt": "Review the parliament.py architecture",
        "project_id": PID, "mode": "pro", "ora_panel": True,
        "session_id": "qa-advisor-1",
    }, timeout=420)
    el = round(time.time() - t0, 1)
    d = r.json()
    rec = {"label": "ask-advisor", "http": r.status_code, "time_s": el,
           "council": d.get("council"), "provider": d.get("provider"),
           "iterations": d.get("iterations"),
           "content_head": (d.get("content") or "")[:300]}
except Exception as e:
    rec = {"label": "ask-advisor", "error": str(e), "time_s": round(time.time() - t0, 1)}
results.append(rec)
print(json.dumps(rec)[:700], flush=True)
json.dump(results, open("/app/test_reports/prod_aggression/council_results.json", "w"), indent=1)
print("DONE")
