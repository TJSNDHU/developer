import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
KEY = open("/app/test_reports/prod_aggression/mcp_key.txt").read().strip()
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
MH = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
out = {"tools": [], "scoping": [], "scan": {}, "health": {}}

def rpc(method, params, session, timeout=150):
    h = dict(MH); h["Mcp-Session-Id"] = session
    t0 = time.time()
    r = requests.post(f"{BASE}/mcp", headers=h,
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                      timeout=timeout)
    ms = round((time.time() - t0) * 1000)
    d = r.json()
    c = "".join(x.get("text", "") for x in d.get("result", {}).get("content", []) if isinstance(x, dict))
    return d, c, ms

# scoping re-check
for q, expect in [("read the auth file", "read"), ("fix the login bug", "write"),
                  ("run a security scan", "security")]:
    d, _, ms = rpc("tools/list", {"query": q}, f"qa2-scope-{expect}")
    tools = [t["name"] for t in d.get("result", {}).get("tools", [])]
    out["scoping"].append({"q": q, "ms": ms, "count": len(tools), "tools": tools,
                           "cap_ok": len(tools) <= 7})
    print("scope", expect, ms, "ms |", len(tools), "tools", flush=True)

# 7 tools
calls = [
    ("list_projects", {}),
    ("read_repo_file", {"project_id": PID, "file_path": "backend/utils/auth.py"}),
    ("list_repo_files", {"project_id": PID, "path": ""}),
    ("search_repo", {"project_id": PID, "query": "get_current_user"}),
    ("get_repo_structure", {"project_id": PID}),
    ("get_task_status", {"task_id": "t_08ef809ea6d5"}),
]
for name, args in calls:
    try:
        d, c, ms = rpc("tools/call", {"name": name, "arguments": args}, "qa2-tools")
        err = d.get("error")
        rec = {"tool": name, "ms": ms, "err": (err or {}).get("message", "")[:100] if err else None,
               "head": c[:150].replace("\n", " ")}
    except Exception as e:
        rec = {"tool": name, "err": str(e)[:100]}
    out["tools"].append(rec)
    print(json.dumps(rec)[:250], flush=True)

# vanguard async scan (fixed worker)
d, c, ms = rpc("tools/call", {"name": "run_vanguard_scan",
                              "arguments": {"project_id": PID}}, "qa2-scan")
print("vanguard start:", ms, "ms |", c[:150], flush=True)
scan_id = None
try:
    scan_id = json.loads(c).get("scan_id")
except Exception:
    import re
    m = re.search(r"vg_[A-Za-z0-9]+", c)
    scan_id = m.group(0) if m else None
out["scan"]["start_ms"] = ms
out["scan"]["scan_id"] = scan_id
final = None
t0 = time.time()
for i in range(48):
    time.sleep(5)
    _, c2, _ = rpc("tools/call", {"name": "get_scan_status",
                                  "arguments": {"scan_id": scan_id}}, "qa2-scan")
    try:
        s = json.loads(c2)
    except Exception:
        s = {"raw": c2[:200]}
    if s.get("status") in ("complete", "completed", "done", "failed", "error"):
        final = s
        break
out["scan"]["duration_s"] = round(time.time() - t0, 1)
out["scan"]["final"] = final
print("vanguard final:", json.dumps(final)[:400] if final else "TIMEOUT",
      "| dur:", out["scan"]["duration_s"], flush=True)

# health scan + /last consistency + MCP get_repo_health consistency
t0 = time.time()
r = requests.post(f"{BASE}/codebase-health/scan", headers=H,
                  json={"project_id": PID}, timeout=300)
hs = r.json()
out["health"]["scan_s"] = round(time.time() - t0, 1)
out["health"]["score"] = hs.get("score")
out["health"]["total"] = hs.get("total")
r2 = requests.get(f"{BASE}/codebase-health/last?project_id={PID}", headers=H, timeout=60)
last = r2.json()
out["health"]["last_score"] = last.get("score")
out["health"]["last_has_breakdown"] = bool(last.get("breakdown"))
_, c3, ms3 = rpc("tools/call", {"name": "get_repo_health",
                                "arguments": {"project_id": PID}}, "qa2-health")
try:
    grh = json.loads(c3)
except Exception:
    grh = {"raw": c3[:200]}
out["health"]["mcp_score"] = grh.get("score")
print("health:", json.dumps(out["health"]), flush=True)
json.dump(out, open("/app/test_reports/prod_aggression/rerun_mcp_scan.json", "w"), indent=1)
print("DONE")
