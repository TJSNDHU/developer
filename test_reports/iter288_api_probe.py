#!/usr/bin/env python3
import os, sys, json, requests
BASE=os.environ.get("PREVIEW_URL", "https://launch-pad-237.preview.emergentagent.com")
API=f"{BASE}/api/aurem-dev"
EMAIL=os.environ.get("AUREM_TEST_EMAIL", "test@aurem.dev")
PASSWORD=os.environ.get("AUREM_TEST_PASSWORD", "AuremTest2026!")
s=requests.Session()
r=s.post(f"{API}/auth/login", json={"email":EMAIL,"password":PASSWORD}, timeout=30)
print("login", r.status_code, r.text[:300])
r.raise_for_status()
tok=r.json().get("token")
headers={"Authorization":f"Bearer {tok}"}
r=s.get(f"{API}/auth/me", headers=headers, timeout=30)
print("me", r.status_code, r.text[:300])
r=s.get(f"{API}/cto/projects/list", headers=headers, timeout=30)
print("projects", r.status_code, r.text[:500])
# probe loop start with no project; this can invoke real planner, so only if env set
if os.environ.get("RUN_LOOP_PROBE") == "1":
    r=s.post(f"{API}/loop/start", json={"project_id": None, "user_message":"QA terminal-state probe: create a tiny harmless change plan, no repo context."}, headers=headers, timeout=120)
    print("loop_start", r.status_code, r.text[:1000])
