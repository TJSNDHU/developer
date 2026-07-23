#!/usr/bin/env python3
import os, json, requests
BASE=os.environ.get("PREVIEW_URL", "https://launch-pad-237.preview.emergentagent.com")
API=f"{BASE}/api/aurem-dev"
EMAIL="test@aurem.dev"; PASSWORD="AuremTest2026!"
s=requests.Session()
r=s.post(f"{API}/auth/login", json={"email":EMAIL,"password":PASSWORD}, timeout=30)
r.raise_for_status(); tok=r.json()["token"]; h={"Authorization":f"Bearer {tok}"}
for lid in ["loop_539b5eddae2145"]:
    r=s.get(f"{API}/loop/{lid}/status", headers=h, timeout=30)
    print("status", lid, r.status_code, r.text[:2000])
