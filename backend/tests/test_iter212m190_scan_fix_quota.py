"""
Iter 212m-190 — Task-quota gating for Developer-Tool scan fixes.

Independent re-verification of the endpoints:
  GET  /api/aurem-dev/fix-pipeline/quota
  POST /api/aurem-dev/fix-pipeline/bulk    (gating matrix only — no real fixes)
  POST /api/aurem-dev/fix-pipeline/preview

Plus regressions:
  POST /api/aurem-dev/auth/login    (case-insensitive email)
  GET  /api/aurem-dev/auth/robot-guide  (public)

Tests mutate the regular test user's `tier` field in dev_users across
free/starter/pro/team and manipulate `scan_fix_usage` for shortfall
scenarios.  Cleanup: tier reset to 'free' and all scan_fix_usage docs
for the user are removed after the run (module-scoped teardown).
"""
from __future__ import annotations
import os
import datetime as dt
import pytest
import requests
import pymongo

BASE_URL   = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Iter 309 · Phase 0.2 · Round 4 — try/except so a missing
    # /app/frontend/.env in CI doesn't abort pytest collection.
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                    break
    except FileNotFoundError:
        pass
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set — skipping live-URL smoke tests",
                allow_module_level=True)
API = f"{BASE_URL}/api/aurem-dev"

REG_EMAIL = "scope.test.regular@aurem.dev"
REG_PASS  = "ScopeReg2026!"
FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PASS  = "AuremTest2026!"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME", "aurem_dev")

_client = pymongo.MongoClient(MONGO_URL)
_db     = _client[DB_NAME]


def _ensure_seed_users():
    """Iter 344/347 — idempotent seed, CI-safe. Mongo cleanup is
    best-effort (3s timeout — on CI runners there is no pod-local
    Mongo; the preview API enforces lockouts against ITS OWN db and
    CI runner IPs start with a fresh window anyway). If the preview
    API itself is unreachable, the whole live-server module skips."""
    try:
        _quick = pymongo.MongoClient(MONGO_URL,
                                     serverSelectionTimeoutMS=3000)
        _quick[DB_NAME].login_attempts.delete_many({})
    except Exception:
        pass
    try:
        r = requests.post(f"{API}/auth/login",
                          json={"email": REG_EMAIL, "password": REG_PASS},
                          timeout=15)
        if r.status_code == 200:
            return
        try:
            _quick[DB_NAME].dev_users.delete_one({"email": REG_EMAIL})
            _quick[DB_NAME].login_attempts.delete_many({})
        except Exception:
            pass
        r = requests.post(f"{API}/auth/signup",
                          json={"email": REG_EMAIL, "password": REG_PASS,
                                "name": "Scope Reg"},
                          timeout=15)
        assert r.status_code in (200, 201), f"seed signup failed: {r.status_code} {r.text[:200]}"
    except requests.ConnectionError:
        pytest.skip(f"preview API unreachable at {API} — live-server "
                    "suite (requires_live_server class)",
                    allow_module_level=True)


_ensure_seed_users()


def _login(email: str, password: str) -> tuple[str, str]:
    r = requests.post(f"{API}/auth/login",
                      json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    body = r.json()
    return body["token"], body["user"]["user_id"] if "user" in body else _uid_from_jwt(body["token"])


def _uid_from_jwt(tok: str) -> str:
    import base64, json as _json
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return _json.loads(base64.urlsafe_b64decode(payload))["user_id"]


def _set_tier(user_id: str, tier: str):
    _db.dev_users.update_one({"user_id": user_id}, {"$set": {"tier": tier}})


def _wipe_usage(user_id: str):
    _db.scan_fix_usage.delete_many({"user_id": user_id})


def _set_usage(user_id: str, count: int):
    month = f"{dt.datetime.utcnow().year:04d}-{dt.datetime.utcnow().month:02d}"
    _db.scan_fix_usage.update_one(
        {"user_id": user_id, "month": month},
        {"$set": {"count": count, "user_id": user_id, "month": month}},
        upsert=True,
    )


@pytest.fixture(scope="module")
def reg_ctx():
    tok, uid = _login(REG_EMAIL, REG_PASS)
    yield {"token": tok, "user_id": uid, "headers": {"Authorization": f"Bearer {tok}"}}
    # Cleanup: reset to 'free' and drop usage rows.
    _set_tier(uid, "free")
    _wipe_usage(uid)


@pytest.fixture(scope="module")
def founder_ctx():
    tok, uid = _login(FOUNDER_EMAIL, FOUNDER_PASS)
    yield {"token": tok, "user_id": uid,
           "headers": {"Authorization": f"Bearer {tok}"}}


# ────────────────────────────────────────────────────────────────
# Regression: auth login + robot-guide public
# ────────────────────────────────────────────────────────────────
class TestAuthRegression:
    def test_login_case_insensitive(self):
        r = requests.post(f"{API}/auth/login",
                          json={"email": REG_EMAIL.upper(), "password": REG_PASS},
                          timeout=15)
        assert r.status_code == 200, r.text[:200]
        assert r.json().get("ok") is True
        assert "token" in r.json()

    def test_robot_guide_public(self):
        r = requests.get(f"{API}/auth/robot-guide", timeout=10)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert "signup_message" in data
        assert "login_message"  in data


# ────────────────────────────────────────────────────────────────
# GET /fix-pipeline/quota — per-tier snapshot
# ────────────────────────────────────────────────────────────────
class TestFixQuotaEndpoint:
    _limits = {"free": 10, "starter": 50, "pro": 300, "team": 400}

    @pytest.mark.parametrize("tier,expected_tools,bulk", [
        ("free",    [], False),
        ("starter", ["vanguard-scan"], False),
        ("pro",     ["health-scan", "vanguard-scan"], False),
        ("team",    ["bug-hunt", "health-scan", "security-scan", "vanguard-scan"], True),
    ])
    def test_tier_snapshot(self, reg_ctx, tier, expected_tools, bulk):
        _wipe_usage(reg_ctx["user_id"])
        _set_tier(reg_ctx["user_id"], tier)
        r = requests.get(f"{API}/fix-pipeline/quota", headers=reg_ctx["headers"], timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["tier"] == tier
        assert sorted(data["fix_tools"]) == sorted(expected_tools)
        assert data["bulk_fix"] is bulk
        assert data["monthly_task_limit"] == self._limits[tier]
        assert data["is_unlimited"] is False
        assert isinstance(data["tasks_used"], int)
        assert isinstance(data["tasks_remaining"], int)

    def test_founder_snapshot(self, founder_ctx):
        r = requests.get(f"{API}/fix-pipeline/quota",
                         headers=founder_ctx["headers"], timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["is_unlimited"] is True
        assert data["tasks_remaining"] is None
        assert data["bulk_fix"] is True
        assert sorted(data["fix_tools"]) == sorted([
            "bug-hunt", "health-scan", "security-scan", "vanguard-scan"])

    def test_usage_rollup_includes_scan_fix_usage(self, reg_ctx):
        _set_tier(reg_ctx["user_id"], "team")
        _wipe_usage(reg_ctx["user_id"])
        _set_usage(reg_ctx["user_id"], 5)
        r = requests.get(f"{API}/fix-pipeline/quota",
                         headers=reg_ctx["headers"], timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["tasks_used"] >= 5, data
        assert data["tasks_remaining"] == 400 - data["tasks_used"]
        # If no cto_tasks for this user this month, roll-up equals exactly 5.
        # We assert the delta is at least 5.


# ────────────────────────────────────────────────────────────────
# POST /fix-pipeline/bulk — gate matrix (no real fixes)
# ────────────────────────────────────────────────────────────────
class TestBulkGate:
    F1 = [{"id": "f1", "file": "a.py"}]
    F2 = [{"id": "f1", "file": "a.py"}, {"id": "f2", "file": "b.py"}]
    PROJECT = "bogus-project-id"

    def _bulk(self, ctx, tool, findings):
        return requests.post(
            f"{API}/fix-pipeline/bulk",
            headers=ctx["headers"],
            json={"project_id": self.PROJECT, "tool": tool, "findings": findings},
            timeout=15,
        )

    def test_free_health_scan_403(self, reg_ctx):
        _wipe_usage(reg_ctx["user_id"])
        _set_tier(reg_ctx["user_id"], "free")
        r = self._bulk(reg_ctx, "health-scan", self.F1)
        assert r.status_code == 403, r.text[:200]
        body = r.json()
        detail = body.get("detail", body)
        assert detail.get("error") == "fix_not_available_on_tier"

    def test_starter_health_scan_403(self, reg_ctx):
        _wipe_usage(reg_ctx["user_id"])
        _set_tier(reg_ctx["user_id"], "starter")
        r = self._bulk(reg_ctx, "health-scan", self.F1)
        assert r.status_code == 403, r.text[:200]
        assert r.json().get("detail", {}).get("error") == "fix_not_available_on_tier"

    def test_starter_vanguard_passes_gate(self, reg_ctx):
        _wipe_usage(reg_ctx["user_id"])
        _set_tier(reg_ctx["user_id"], "starter")
        r = self._bulk(reg_ctx, "vanguard-scan", self.F1)
        # Should pass gate and return a job_id (job will fail during
        # real fixing because project_id is bogus — that's fine).
        assert r.status_code == 200, r.text[:400]
        assert r.json().get("job_id")

    def test_starter_bulk_two_findings_403(self, reg_ctx):
        _wipe_usage(reg_ctx["user_id"])
        _set_tier(reg_ctx["user_id"], "starter")
        r = self._bulk(reg_ctx, "vanguard-scan", self.F2)
        assert r.status_code == 403, r.text[:200]
        assert r.json().get("detail", {}).get("error") == "bulk_fix_not_available"

    def test_team_insufficient_tasks_402(self, reg_ctx):
        _set_tier(reg_ctx["user_id"], "team")
        _wipe_usage(reg_ctx["user_id"])
        _set_usage(reg_ctx["user_id"], 399)
        r = self._bulk(reg_ctx, "health-scan", self.F2)
        assert r.status_code == 402, r.text[:200]
        detail = r.json().get("detail", {})
        assert detail.get("error") == "insufficient_tasks"
        assert detail.get("remaining") == 1
        assert detail.get("needed") == 2
        assert "not enough for 2 fixes" in detail.get("message", "")

    def test_unknown_tool_falls_back_to_health_scan(self, reg_ctx):
        """`tool='made-up'` must silently fall back to health-scan gating,
        NOT 500.  On starter that means 403 fix_not_available_on_tier
        (health-scan not allowed on starter)."""
        _wipe_usage(reg_ctx["user_id"])
        _set_tier(reg_ctx["user_id"], "starter")
        r = self._bulk(reg_ctx, "made-up", self.F1)
        assert r.status_code == 403, f"expected 403 fallback got {r.status_code}: {r.text[:200]}"
        assert r.json().get("detail", {}).get("error") == "fix_not_available_on_tier"


# ────────────────────────────────────────────────────────────────
# POST /fix-pipeline/preview — task fields + bulk cap
# ────────────────────────────────────────────────────────────────
class TestPreview:
    # Iter 347 — these tests seed Mongo DIRECTLY and then hit the live
    # API expecting to read that seed back: API and DB must be the
    # SAME environment (preview pod). On CI runners the API is remote
    # preview while MONGO_URL is a local service container → cross-env
    # mismatch, deterministic false-fail. Preview-only by contract.
    pytestmark = pytest.mark.skipif(
        os.environ.get("CI", "").lower() == "true",
        reason="requires API + DB in the same environment (preview-only)",
    )

    F1 = [{"id": "f1", "file": "a.py"}]
    F2 = [{"id": f"f{i}", "file": f"x{i}.py"} for i in range(2)]

    def _preview(self, ctx, findings, tool="health-scan"):
        return requests.post(
            f"{API}/fix-pipeline/preview",
            headers=ctx["headers"],
            json={"project_id": "bogus", "tool": tool, "findings": findings},
            timeout=15,
        )

    def test_team_insufficient_tasks(self, reg_ctx):
        _set_tier(reg_ctx["user_id"], "team")
        _wipe_usage(reg_ctx["user_id"])
        _set_usage(reg_ctx["user_id"], 399)
        r = self._preview(reg_ctx, self.F2, tool="health-scan")
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        for k in ("tool", "tier", "tasks_needed", "tasks_remaining",
                  "monthly_task_limit", "tool_allowed", "bulk_allowed",
                  "can_proceed", "reason", "shortfall"):
            assert k in data, f"missing field: {k}"
        assert data["can_proceed"] is False
        assert data["reason"] == "insufficient_tasks"
        assert data["shortfall"] == 1
        assert data["tasks_needed"] == 2
        assert data["tasks_remaining"] == 1

    def test_founder_preview(self, founder_ctx):
        r = self._preview(founder_ctx, self.F1, tool="health-scan")
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["can_proceed"] is True
        assert data["tasks_remaining"] is None

    def test_bulk_cap_regression(self, reg_ctx):
        """Preview with 25 findings must cap count=20, bulk_max=20,
        total_requested=25."""
        _set_tier(reg_ctx["user_id"], "team")
        _wipe_usage(reg_ctx["user_id"])
        findings25 = [{"id": f"f{i}", "file": f"x{i}.py"} for i in range(25)]
        r = self._preview(reg_ctx, findings25, tool="health-scan")
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data["count"] == 20
        assert data["bulk_max"] == 20
        assert data["total_requested"] == 25
