import json, time, sys, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
results = []

def stream(label, payload, timeout=400):
    t0 = time.time()
    rec = {"label": label, "steps": 0, "tokens_chars": 0}
    try:
        with requests.post(f"{BASE}/chat/stream", headers=H, json=payload,
                           stream=True, timeout=timeout) as r:
            rec["http"] = r.status_code
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                if "token" in ev:
                    if rec["tokens_chars"] == 0:
                        rec["ttft_s"] = round(time.time() - t0, 1)
                    rec["tokens_chars"] += len(ev["token"])
                elif ev.get("type") == "step":
                    rec["steps"] += 1
                elif ev.get("type") == "council" or "council_id" in ev:
                    rec["council_frame"] = {k: ev.get(k) for k in
                        ("council", "council_id", "model", "task_type") if k in ev}
                elif ev.get("done"):
                    rec["done"] = {k: ev.get(k) for k in
                        ("provider", "council", "fallback_chain", "free_mode",
                         "tokens_remaining", "task_type") if k in ev}
                elif ev.get("provider") and "mode" in ev:
                    rec["meta"] = {k: ev.get(k) for k in
                        ("provider", "mode", "thinking_s", "tool_calls_run",
                         "timed_out", "council", "task_type") if k in ev}
    except Exception as e:
        rec["error"] = str(e)[:200]
    rec["total_s"] = round(time.time() - t0, 1)
    return rec

FIX = "How would you fix a missing docstring on get_current_user in backend/utils/auth.py? Show the exact docstring."
suites = [
    ("review-swift", {"prompt": FIX, "project_id": PID, "mode": "swift", "session_id": "qa-rm-swift"}),
    ("review-pro",   {"prompt": FIX, "project_id": PID, "mode": "pro",   "session_id": "qa-rm-pro"}),
    ("review-maxx",  {"prompt": FIX, "project_id": PID, "mode": "maxx",  "session_id": "qa-rm-maxx"}),
    ("advisor-parliament", {"prompt": "Review the parliament.py architecture", "project_id": PID,
                            "mode": "pro", "ora_panel": True, "session_id": "qa-adv2"}),
    ("council-B-health", {"prompt": "Analyze the health of this codebase", "project_id": PID,
                          "mode": "pro", "session_id": "qa-cb2"}),
]
for label, payload in suites:
    rec = stream(label, payload)
    results.append(rec)
    print(json.dumps(rec)[:800], flush=True)
    json.dump(results, open("/app/test_reports/prod_aggression/review_mode_results.json", "w"), indent=1)
print("DONE")
