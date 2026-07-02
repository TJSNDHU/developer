import json, time, sys, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
TASK = "Add a docstring to the get_current_user function in backend/utils/auth.py"

label = sys.argv[1] if len(sys.argv) > 1 else "loop"
if len(sys.argv) > 2:
    TASK = sys.argv[2]
rec = {"combo": label, "events": []}

t0 = time.time()
r = requests.post(f"{BASE}/loop/start", headers=H,
                  json={"project_id": PID, "user_message": TASK}, timeout=300)
start_s = round(time.time() - t0, 1)
d = r.json()
loop_id = d.get("loop_id")
rec["start"] = {"http": r.status_code, "time_s": start_s, "loop_id": loop_id,
                "state": d.get("state"), "phase": d.get("phase"),
                "plan_head": json.dumps(d.get("plan"))[:400] if d.get("plan") else None,
                "raw_keys": list(d.keys())}
print("start:", json.dumps(rec["start"])[:600], flush=True)
if not loop_id:
    print("NO LOOP ID — raw:", json.dumps(d)[:800])
    json.dump(rec, open(f"/app/test_reports/prod_aggression/matrix_{label}.json", "w"), indent=1)
    sys.exit(1)

def status():
    return requests.get(f"{BASE}/loop/{loop_id}/status", headers=H, timeout=30).json()

# wait for plan pause then confirm
confirmed_plan = False
ship_confirmed = False
final = None
for i in range(180):  # up to 15 min
    time.sleep(5)
    st = status()
    state = (st.get("state") or "").lower()
    phase = st.get("phase")
    ctx = st.get("context") or {}
    ev = {"t": round(time.time() - t0, 1), "state": state, "phase": phase}
    if not rec["events"] or rec["events"][-1]["state"] != state or rec["events"][-1]["phase"] != phase:
        rec["events"].append(ev)
        print("status:", json.dumps(ev), flush=True)
    if state in ("completed", "failed", "aborted"):
        final = st
        break
    if state in ("paused_for_user", "awaiting_confirmation"):
        if phase in ("plan", "planning") and not confirmed_plan:
            c = requests.post(f"{BASE}/loop/{loop_id}/confirm", headers=H,
                              json={"approved": True}, timeout=60)
            confirmed_plan = True
            print("confirmed plan:", c.status_code, c.text[:200], flush=True)
        elif phase == "ship" and not ship_confirmed:
            c = requests.post(f"{BASE}/loop/{loop_id}/confirm-ship", headers=H,
                              json={"approved": True}, timeout=60)
            ship_confirmed = True
            print("confirmed ship:", c.status_code, c.text[:200], flush=True)
        elif not confirmed_plan:
            c = requests.post(f"{BASE}/loop/{loop_id}/confirm", headers=H,
                              json={"approved": True}, timeout=60)
            confirmed_plan = True
            print("confirmed (generic):", c.status_code, c.text[:200], flush=True)

total_s = round(time.time() - t0, 1)
rec["total_s"] = total_s
rec["final_state"] = (final or {}).get("state")
rec["final"] = final
print(f"\nFINAL state={rec['final_state']} total_s={total_s}", flush=True)
if final:
    fj = json.dumps(final)
    import re
    shas = re.findall(r'"(?:commit_sha|sha)"\s*:\s*"([0-9a-f]{7,40})"', fj)
    rec["commit_shas"] = list(dict.fromkeys(shas))
    print("commit shas found:", rec["commit_shas"], flush=True)
json.dump(rec, open(f"/app/test_reports/prod_aggression/matrix_{label}.json", "w"), indent=1)
print("DONE")
