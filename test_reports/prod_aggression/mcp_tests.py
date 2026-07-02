import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev/mcp"
KEY = open("/app/test_reports/prod_aggression/mcp_key.txt").read().strip()
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
results = []

def rpc(method, params=None, session=None, rid=1):
    h = dict(H)
    if session:
        h["Mcp-Session-Id"] = session
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    t0 = time.time()
    r = requests.post(BASE, headers=h, json=body, timeout=120)
    ms = round((time.time() - t0) * 1000)
    try:
        d = r.json()
    except Exception:
        d = {"raw": r.text[:500]}
    return d, ms, r.status_code

# 1. initialize
d, ms, sc = rpc("initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": "qa", "version": "1"}})
results.append({"test": "initialize", "ms": ms, "http": sc, "ok": "result" in d,
                "server": d.get("result", {}).get("serverInfo")})

# 2. tools/list baseline (fresh session, no query)
d, ms, sc = rpc("tools/list", {}, session="qa-baseline-1")
tools = [t["name"] for t in d.get("result", {}).get("tools", [])]
meta = d.get("result", {}).get("_meta") or d.get("result", {}).get("meta") or {}
results.append({"test": "tools/list baseline", "ms": ms, "http": sc,
                "count": len(tools), "tools": tools, "meta": meta})

# 3-5. scoped queries
for q, expect in [("read the auth file", "read"),
                  ("fix the login bug", "write"),
                  ("run a security scan", "security")]:
    sess = "qa-scope-" + expect
    d, ms, sc = rpc("tools/list", {"query": q}, session=sess)
    tools = [t["name"] for t in d.get("result", {}).get("tools", [])]
    meta = d.get("result", {}).get("_meta") or {}
    results.append({"test": f"tools/list scoped: '{q}'", "expect_group": expect,
                    "ms": ms, "http": sc, "count": len(tools), "tools": tools,
                    "meta": meta, "cap_ok": len(tools) <= 7})

print(json.dumps(results, indent=1))
json.dump(results, open("/app/test_reports/prod_aggression/mcp_scoping_results.json", "w"), indent=1)
