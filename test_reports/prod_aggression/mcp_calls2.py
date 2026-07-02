import json, time, requests, re

BASE = "https://auremcto.com/api/aurem-dev/mcp"
KEY = open("/app/test_reports/prod_aggression/mcp_key.txt").read().strip()
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"

def call_tool(name, args, session="qa-calls-2", timeout=180):
    h = dict(H); h["Mcp-Session-Id"] = session
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args}}
    t0 = time.time()
    r = requests.post(BASE, headers=h, json=body, timeout=timeout)
    ms = round((time.time() - t0) * 1000)
    d = r.json()
    content = ""
    if "result" in d:
        c = d["result"].get("content") or []
        content = "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return {"tool": name, "args": args, "ms": ms, "http": r.status_code,
            "error": d.get("error"), "is_error": bool(d.get("error")) or bool(d.get("result", {}).get("isError")),
            "content": content}

out = []
for name, args in [
    ("read_repo_file", {"project_id": PID, "file_path": "routers/auth.py"}),
    ("search_repo", {"project_id": PID, "query": "get_current_user", "max_results": 5}),
    ("list_repo_files", {"project_id": PID, "path": "routers"}),
    ("get_repo_structure", {"project_id": PID, "max_depth": 2}),
    ("get_recent_commits", {"project_id": PID, "limit": 3}),
    ("get_repo_health", {"project_id": PID}),
]:
    r = call_tool(name, args)
    out.append(r)
    print(f"--- {name} ({r['ms']}ms, err={r['is_error']}) ---")
    print(r["content"][:500].replace(chr(10), " | "))

# poll scan
scan_id = "vg_NQ5WwSmw0Ngj1Pb8"
t0 = time.time()
final = None
for i in range(60):
    r = call_tool("get_scan_status", {"scan_id": scan_id}, session="qa-scan-1")
    try:
        d = json.loads(r["content"])
    except Exception:
        d = {"raw": r["content"][:300]}
    st = d.get("status")
    if st in ("complete", "completed", "done", "failed", "error"):
        final = d
        break
    time.sleep(5)
dur = round(time.time() - t0, 1)
print(f"\nSCAN status={final.get('status') if final else 'TIMEOUT'} poll_duration_s={dur}")
print(json.dumps(final, indent=1)[:900] if final else "no final")
out.append({"tool": "get_scan_status(poll)", "scan_id": scan_id, "poll_duration_s": dur, "final": final})
json.dump(out, open("/app/test_reports/prod_aggression/mcp_call_results2.json", "w"), indent=1)
