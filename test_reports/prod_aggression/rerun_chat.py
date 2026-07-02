import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
out = {}

# Council routing on PROD (P0-3 verify)
for label, prompt, expect in [
        ("councilC", "Write a short CODE_OF_CONDUCT.md for this repo", "C"),
        ("councilB", "Summarize recent commits", "B")]:
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/chat/send", headers=H, json={
            "prompt": prompt, "project_id": PID, "mode": "pro",
            "session_id": f"qa2-{label}"}, timeout=110)
        d = r.json()
        out[label] = {"council": d.get("council"), "task_type": d.get("task_type"),
                      "provider": d.get("provider"), "s": round(time.time() - t0, 1),
                      "expect": expect}
    except Exception as e:
        out[label] = {"error": str(e)[:120], "s": round(time.time() - t0, 1), "expect": expect}
    print(label, json.dumps(out[label]), flush=True)

# Review-mode SSE latency (D2)
def sse(label, payload, timeout=300):
    t0 = time.time()
    rec = {"tokens": 0}
    try:
        with requests.post(f"{BASE}/chat/stream", headers=H, json=payload,
                           stream=True, timeout=timeout) as r:
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:])
                except Exception:
                    continue
                if "token" in ev:
                    if rec["tokens"] == 0:
                        rec["ttft_s"] = round(time.time() - t0, 1)
                    rec["tokens"] += 1
                if ev.get("done"):
                    rec["provider"] = ev.get("provider")
                    rec["council"] = ev.get("council")
    except Exception as e:
        rec["error"] = str(e)[:100]
    rec["total_s"] = round(time.time() - t0, 1)
    return rec

Q = "How would you fix a missing docstring on get_current_user in backend/utils/auth.py? Show the docstring."
for mode in ["swift", "pro", "maxx"]:
    rec = sse(f"rm-{mode}", {"prompt": Q, "project_id": PID, "mode": mode,
                             "session_id": f"qa2-rm-{mode}"})
    out[f"review_{mode}"] = rec
    print("review", mode, json.dumps(rec)[:220], flush=True)

# Advisor + analyze-health (P1-6 verify — must stream frames, no zero-frame hang)
for label, payload in [
        ("advisor", {"prompt": "Review the parliament.py architecture", "project_id": PID,
                     "mode": "pro", "ora_panel": True, "session_id": "qa2-adv"}),
        ("analyze_health", {"prompt": "Analyze the health of this codebase", "project_id": PID,
                            "mode": "pro", "session_id": "qa2-ah"})]:
    t0 = time.time()
    rec = {"frames": 0, "first_frame_s": None}
    try:
        with requests.post(f"{BASE}/chat/stream", headers=H, json=payload,
                           stream=True, timeout=300) as r:
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if rec["first_frame_s"] is None:
                    rec["first_frame_s"] = round(time.time() - t0, 1)
                if line.startswith("data:"):
                    rec["frames"] += 1
    except Exception as e:
        rec["error"] = str(e)[:100]
    rec["total_s"] = round(time.time() - t0, 1)
    out[label] = rec
    print(label, json.dumps(rec)[:200], flush=True)

json.dump(out, open("/app/test_reports/prod_aggression/rerun_chat.json", "w"), indent=1)
print("DONE")
