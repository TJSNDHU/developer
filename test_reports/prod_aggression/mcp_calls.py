import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev/mcp"
KEY = open("/app/test_reports/prod_aggression/mcp_key.txt").read().strip()
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
results = []

def rpc(method, params=None, session="qa-calls-1", rid=1, timeout=180):
    h = dict(H)
    h["Mcp-Session-Id"] = session
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    t0 = time.time()
    r = requests.post(BASE, headers=h, json=body, timeout=timeout)
    ms = round((time.time() - t0) * 1000)
    try:
        d = r.json()
    except Exception:
        d = {"raw": r.text[:800]}
    return d, ms, r.status_code

def call_tool(name, args, session="qa-calls-1", timeout=180):
    d, ms, sc = rpc("tools/call", {"name": name, "arguments": args}, session=session, timeout=timeout)
    err = d.get("error")
    content = ""
    if "result" in d:
        c = d["result"].get("content") or []
        content = "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return {"tool": name, "ms": ms, "http": sc, "error": err,
            "is_error": bool(err) or bool(d.get("result", {}).get("isError")),
            "content_head": content[:400], "content_len": len(content)}

# get schemas first
schemas = {}
for q in ["read the auth file", "fix the login bug", "run a security scan"]:
    d, ms, sc = rpc("tools/list", {"query": q}, session="qa-schema")
    for t in d.get("result", {}).get("tools", []):
        schemas[t["name"]] = t.get("inputSchema", {}).get("properties", {})
print("SCHEMAS:")
for k, v in schemas.items():
    print(" ", k, "->", list(v.keys()))

tests = [
    ("list_projects", {}),
    ("read_repo_file", {"project_id": PID, "path": "routers/auth.py"}),
    ("list_repo_files", {"project_id": PID, "path": ""}),
    ("search_repo", {"project_id": PID, "query": "get_current_user"}),
    ("get_repo_structure", {"project_id": PID}),
]
for name, args in tests:
    r = call_tool(name, args)
    results.append(r)
    print(json.dumps({k: r[k] for k in ("tool", "ms", "http", "is_error")}), "|", r["content_head"][:150].replace("\n", " "))

# vanguard async scan
r = call_tool("run_vanguard_scan", {"project_id": PID}, session="qa-scan-1")
results.append(r)
print("VANGUARD START:", json.dumps(r, indent=1)[:600])
scan_id = None
try:
    txt = r["content_head"]
    import re
    m = re.search(r'(scan_[A-Za-z0-9_-]+|"scan_id"\s*:\s*"([^"]+)")', txt)
    if m:
        scan_id = m.group(2) or m.group(1)
except Exception:
    pass
print("scan_id parsed:", scan_id)
json.dump({"results": results, "scan_id": scan_id, "schemas": {k: list(v.keys()) for k, v in schemas.items()}},
          open("/app/test_reports/prod_aggression/mcp_call_results.json", "w"), indent=1)
