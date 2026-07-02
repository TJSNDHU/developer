import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
TASK = "Add a docstring to the get_current_user function in backend/utils/auth.py"
results = []

for mode, maxx in [("swift", False), ("pro", False), ("maxx", True)]:
    combo = f"prompt+{mode}"
    print(f"\n===== {combo} =====", flush=True)
    # chat layer — council/model/provider + latency
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/chat/send", headers=H, json={
            "prompt": TASK, "project_id": PID, "mode": mode,
            "session_id": f"qa-matrix-{mode}", "execution_mode": "prompt",
        }, timeout=300)
        chat_s = round(time.time() - t0, 1)
        cd = r.json()
    except Exception as e:
        chat_s = round(time.time() - t0, 1)
        cd = {"error": str(e)}
    chat_rec = {"http": r.status_code if 'r' in dir() else None, "time_s": chat_s,
                "provider": cd.get("provider"), "council": cd.get("council"),
                "task_type": cd.get("task_type"), "iterations": cd.get("iterations"),
                "content_head": (cd.get("content") or "")[:200]}
    print("chat:", json.dumps(chat_rec)[:400], flush=True)

    # ship vehicle — tasks/submit
    t0 = time.time()
    r2 = requests.post(f"{BASE}/cto/tasks/submit", headers=H, json={
        "project_id": PID, "task": TASK, "maxx_mode": maxx}, timeout=60)
    sub = r2.json()
    task_id = sub.get("task_id")
    print("submitted task:", task_id, r2.status_code, flush=True)
    final = None
    for i in range(120):
        time.sleep(5)
        st = requests.get(f"{BASE}/cto/tasks/{task_id}", headers=H, timeout=30).json()
        s = st.get("status") or st.get("task", {}).get("status")
        if s in ("done", "completed", "failed", "error", "shipped"):
            final = st
            break
    task_s = round(time.time() - t0, 1)
    doc = final.get("task", final) if final else {}
    task_rec = {"task_id": task_id, "time_s": task_s,
                "status": doc.get("status"), "commit_sha": doc.get("commit_sha"),
                "error": (str(doc.get("error"))[:300] if doc.get("error") else None),
                "maxx_mode": maxx}
    print("task:", json.dumps(task_rec)[:500], flush=True)
    results.append({"combo": combo, "chat": chat_rec, "task": task_rec})
    json.dump(results, open("/app/test_reports/prod_aggression/matrix_prompt_results.json", "w"), indent=1)

print("\nDONE")
