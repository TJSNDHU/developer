import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
KEY = open("/app/test_reports/prod_aggression/mcp_key.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
MH = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Mcp-Session-Id": "qa-ship"}
PID = "p_c2b5b8a916"
out = {}

# 1. ship_code via MCP
t0 = time.time()
r = requests.post(f"{BASE}/mcp", headers=MH, json={
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "ship_code", "arguments": {
        "project_id": PID,
        "task": "Add a docstring to the verify_password function in backend/utils/auth.py"}}}, timeout=120)
ms = round((time.time() - t0) * 1000)
d = r.json()
c = "".join(x.get("text", "") for x in d.get("result", {}).get("content", []) if isinstance(x, dict))
print("ship_code:", ms, "ms |", c[:250], flush=True)
tid = None
try:
    tid = json.loads(c).get("task_id")
except Exception:
    pass
out["ship_code"] = {"ms": ms, "task_id": tid, "resp": c[:400]}

# 2. prompt combos re-run (tasks/submit)
combos = [
    ("prompt+swift", "Add a docstring to the require_auth function in backend/utils/auth.py", False),
    ("prompt+pro",   "Add a docstring to the require_admin function in backend/utils/auth.py", False),
    ("prompt+maxx",  "Add a docstring to the averify_password function in backend/utils/auth.py", True),
]
task_ids = []
for label, task, maxx in combos:
    r = requests.post(f"{BASE}/cto/tasks/submit", headers=H,
                      json={"project_id": PID, "task": task, "maxx_mode": maxx}, timeout=60)
    j = r.json()
    task_ids.append((label, j.get("task_id"), time.time()))
    print("submitted", label, j.get("task_id"), flush=True)
    time.sleep(3)

# poll all (incl mcp ship task)
if tid:
    task_ids.append(("mcp ship_code", tid, t0))
pending = dict((t, {"label": l, "t0": s}) for l, t, s in task_ids if t)
results = {}
deadline = time.time() + 600
while pending and time.time() < deadline:
    time.sleep(8)
    for t in list(pending):
        st = requests.get(f"{BASE}/cto/tasks/{t}", headers=H, timeout=30).json()
        doc = st.get("task", st)
        s = doc.get("status")
        if s in ("done", "completed", "failed", "error", "shipped"):
            results[t] = {"label": pending[t]["label"], "status": s,
                          "time_s": round(time.time() - pending[t]["t0"], 1),
                          "commit_sha": doc.get("commit_sha"),
                          "error": str(doc.get("error"))[:250] if doc.get("error") else None}
            print(json.dumps(results[t]), flush=True)
            del pending[t]
for t, info in pending.items():
    results[t] = {"label": info["label"], "status": "TIMEOUT(600s)"}
    print(json.dumps(results[t]), flush=True)
out["tasks"] = results
json.dump(out, open("/app/test_reports/prod_aggression/ship_prompt_results.json", "w"), indent=1)
print("DONE")
