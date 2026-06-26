"""
Iter 212m-32 — Onboarding "connect a repo" nudge email.

Coverage:
  • render_text / render_html — locked copy + tracked CTA URL
  • _created_at_dt — datetime / epoch s / epoch ms / ISO string
  • eligible_users — t24 + t72 window, no-repo + dedupe gates
  • run_nudge_batch — dry-run + real-send (Resend mocked)
  • click endpoint — 302 redirect + audit row update (clicked_at,
    click_count, idempotent first-click)
  • admin endpoint — auth gate, dry_run preview, real send
  • Source pins — main wires router + cron, dashboard handles
    ?action=connect-repo
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


# ════════════════════════════════════════════════════════════════════
# 1. Copy + rendering
# ════════════════════════════════════════════════════════════════════

def test_render_text_uses_locked_copy_and_tracked_cta():
    from services.onboarding_email import render_text, click_url, SIGNOFF
    body = render_text({
        "user_id": "u_abc", "email": "alice@x.com", "name": "Alice Builder",
    })
    assert "Hey Alice," in body
    assert "Your codebase gets mapped instantly" in body
    assert "Free SEO fix applied automatically" in body
    assert "One of 500 founder spots — yours" in body
    assert "Takes 2 minutes." in body
    assert SIGNOFF == "— Tejinder Sandhu, Founder, Aurem"
    assert SIGNOFF in body
    assert click_url("u_abc") in body
    # Tracked URL must hit the redirector, NOT the dashboard directly
    assert "/api/aurem-dev/onboarding/click?uid=u_abc&c=connect_repo_nudge" in body


def test_render_html_contains_cta_button_link():
    from services.onboarding_email import render_html
    html = render_html({"user_id": "u1", "email": "x@y.com", "name": "Pat"})
    assert 'href="' in html
    assert "/api/aurem-dev/onboarding/click?uid=u1&c=connect_repo_nudge" in html
    assert "Connect your repo" in html


def test_first_name_fallbacks_to_email_localpart():
    from services.onboarding_email import _first_name
    assert _first_name({"name": "Pat Smith"}) == "Pat"
    assert _first_name({"name": "", "email": "alice.b@example.com"}) == "alice"
    assert _first_name({"email": "x@y.com"}) == "x"
    assert _first_name({}) == "there"


# ════════════════════════════════════════════════════════════════════
# 2. created_at coercion
# ════════════════════════════════════════════════════════════════════

def test_created_at_dt_handles_all_legacy_shapes():
    from services.onboarding_email import _created_at_dt
    now = datetime.now(timezone.utc)
    # datetime tz-aware
    assert _created_at_dt(now) == now
    # naive datetime
    naive = now.replace(tzinfo=None)
    coerced = _created_at_dt(naive)
    assert coerced.tzinfo is not None
    # epoch seconds
    secs = now.timestamp()
    assert abs(_created_at_dt(secs).timestamp() - secs) < 1
    # epoch milliseconds
    ms = secs * 1000
    assert abs(_created_at_dt(ms).timestamp() - secs) < 1
    # ISO string
    iso = now.isoformat()
    assert abs(_created_at_dt(iso).timestamp() - secs) < 1
    # None / junk
    assert _created_at_dt(None) is None
    assert _created_at_dt("not-a-date") is None


# ════════════════════════════════════════════════════════════════════
# 3. Eligibility (mocked Mongo)
# ════════════════════════════════════════════════════════════════════

class _FakeDB:
    """Minimal Motor-shaped stand-in covering the queries the nudge
    service makes."""
    def __init__(self):
        self.users: list[dict] = []
        self.projects: list[dict] = []
        self.sent: list[dict] = []

    class _Coll:
        def __init__(self, rows): self.rows = rows
        def find(self, q=None, projection=None):
            outer_q = q or {}
            matches = [r for r in self.rows if _match(r, outer_q)]
            class _Cur:
                def __init__(self, items): self.items = items
                def sort(self, *_a, **_k): return self
                async def to_list(self, length=None):
                    return list(self.items)[: (length or len(self.items))]
            return _Cur(matches)
        async def find_one(self, q=None, projection=None, sort=None):
            outer_q = q or {}
            cands = [r for r in self.rows if _match(r, outer_q)]
            if sort:
                key, direction = sort[0]
                cands.sort(key=lambda r: r.get(key) or 0, reverse=direction < 0)
            return cands[0] if cands else None
        async def insert_one(self, doc):
            doc.setdefault("_id", f"id_{len(self.rows)}")
            self.rows.append(dict(doc))
            return type("R", (), {"inserted_id": doc["_id"]})()
        async def update_one(self, q, update):
            for r in self.rows:
                if _match(r, q):
                    if "$set" in update: r.update(update["$set"])
                    if "$inc" in update:
                        for k, v in update["$inc"].items():
                            r[k] = (r.get(k) or 0) + v
                    return type("R", (), {"matched_count": 1})()
            return type("R", (), {"matched_count": 0})()

    @property
    def dev_users(self): return self._Coll(self.users)
    @property
    def cto_projects(self): return self._Coll(self.projects)
    @property
    def onboarding_emails(self): return self._Coll(self.sent)


def _match(row, q):
    for k, v in q.items():
        if isinstance(v, dict):
            if "$exists" in v and bool(row.get(k) is not None) != v["$exists"]:
                return False
            if "$ne" in v and row.get(k) == v["$ne"]:
                return False
        else:
            if row.get(k) != v:
                return False
    return True


@pytest.mark.asyncio
async def test_eligible_users_picks_t24_and_skips_too_new_or_repo_holders():
    from services.onboarding_email import eligible_users

    now = datetime.now(timezone.utc)
    db = _FakeDB()
    # 1) Eligible: 30 h old, no repo
    db.users.append({"user_id": "u_eligible", "email": "e@x.com",
                     "name": "Eli", "created_at": now - timedelta(hours=30)})
    # 2) Too new: 5 h old
    db.users.append({"user_id": "u_new", "email": "n@x.com",
                     "name": "Newt", "created_at": now - timedelta(hours=5)})
    # 3) Has a repo
    db.users.append({"user_id": "u_repo", "email": "r@x.com",
                     "name": "Repo", "created_at": now - timedelta(hours=48)})
    db.projects.append({"user_id": "u_repo", "project_id": "p1"})
    # 4) Already nudged at t24
    db.users.append({"user_id": "u_done", "email": "d@x.com",
                     "name": "Done", "created_at": now - timedelta(hours=30)})
    db.sent.append({
        "user_id": "u_done", "campaign": "connect_repo_nudge",
        "stage": "t24", "sent_ok": True,
        "sent_at": now - timedelta(hours=1),
    })

    cohort = await eligible_users(db, stage="t24")
    ids = {u["user_id"] for u in cohort}
    assert ids == {"u_eligible"}


@pytest.mark.asyncio
async def test_eligible_users_t72_skips_users_below_72h_cutoff():
    from services.onboarding_email import eligible_users
    now = datetime.now(timezone.utc)
    db = _FakeDB()
    db.users.append({"user_id": "u_48", "email": "a@x.com", "name": "A",
                     "created_at": now - timedelta(hours=48)})   # too new for t72
    db.users.append({"user_id": "u_80", "email": "b@x.com", "name": "B",
                     "created_at": now - timedelta(hours=80)})   # eligible for t72
    cohort = await eligible_users(db, stage="t72")
    assert {u["user_id"] for u in cohort} == {"u_80"}


# ════════════════════════════════════════════════════════════════════
# 4. Send batch — dry-run + real send (Resend mocked)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_nudge_batch_dry_run_does_not_call_resend_or_insert_rows():
    from services import onboarding_email as om
    now = datetime.now(timezone.utc)
    db = _FakeDB()
    db.users.append({"user_id": "u_eligible", "email": "e@x.com", "name": "Eli",
                     "created_at": now - timedelta(hours=30)})

    with patch("services.onboarding_email.get_db", return_value=db), \
         patch("services.onboarding_email._resend_send",
               new=AsyncMock(return_value=(True, None))) as resend_mock:
        result = await om.run_nudge_batch(db, stages=("t24",), dry_run=True)

    assert result["sent"] == 1                  # counted as sent (dry-run row)
    assert resend_mock.await_count == 0         # NO actual send
    assert len(db.sent) == 0                    # NO audit row in dry-run mode


@pytest.mark.asyncio
async def test_run_nudge_batch_real_send_hits_resend_and_logs_audit():
    from services import onboarding_email as om
    now = datetime.now(timezone.utc)
    db = _FakeDB()
    db.users.append({"user_id": "u1", "email": "u1@x.com", "name": "One",
                     "created_at": now - timedelta(hours=30)})

    with patch("services.onboarding_email.get_db", return_value=db), \
         patch("services.onboarding_email._resend_send",
               new=AsyncMock(return_value=(True, None))) as resend_mock:
        result = await om.run_nudge_batch(db, stages=("t24",), dry_run=False)

    assert result["sent"] == 1
    assert result["failed"] == 0
    resend_mock.assert_awaited_once()
    assert len(db.sent) == 1
    row = db.sent[0]
    assert row["user_id"] == "u1"
    assert row["campaign"] == "connect_repo_nudge"
    assert row["stage"] == "t24"
    assert row["sent_ok"] is True


@pytest.mark.asyncio
async def test_run_nudge_batch_resend_failure_records_error():
    from services import onboarding_email as om
    now = datetime.now(timezone.utc)
    db = _FakeDB()
    db.users.append({"user_id": "u_fail", "email": "f@x.com", "name": "F",
                     "created_at": now - timedelta(hours=30)})
    with patch("services.onboarding_email.get_db", return_value=db), \
         patch("services.onboarding_email._resend_send",
               new=AsyncMock(return_value=(False, "HTTP 422"))):
        result = await om.run_nudge_batch(db, stages=("t24",), dry_run=False)
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert "HTTP 422" in result["errors"][0]
    assert db.sent[0]["sent_ok"] is False


@pytest.mark.asyncio
async def test_run_nudge_batch_respects_user_ids_filter():
    """Admin can scope a manual send to a subset of eligible users."""
    from services import onboarding_email as om
    now = datetime.now(timezone.utc)
    db = _FakeDB()
    for uid in ("a", "b", "c"):
        db.users.append({
            "user_id": uid, "email": f"{uid}@x.com", "name": uid.upper(),
            "created_at": now - timedelta(hours=30),
        })
    with patch("services.onboarding_email.get_db", return_value=db), \
         patch("services.onboarding_email._resend_send",
               new=AsyncMock(return_value=(True, None))) as resend_mock:
        result = await om.run_nudge_batch(
            db, stages=("t24",), dry_run=False, user_ids=["a", "c"],
        )
    assert result["sent"] == 2
    sent_uids = {r["user_id"] for r in db.sent}
    assert sent_uids == {"a", "c"}
    assert resend_mock.await_count == 2


# ════════════════════════════════════════════════════════════════════
# 5. Click tracker behaviour (logic only, no FastAPI client)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_click_endpoint_logs_first_click_and_increments_count():
    """Direct call to the route function so we don't need TestClient."""
    from routers import onboarding as r
    now = datetime.now(timezone.utc)
    db = _FakeDB()
    db.sent.append({
        "_id": "row1", "user_id": "u1",
        "campaign": "connect_repo_nudge", "stage": "t24",
        "sent_at": now, "sent_ok": True,
        "clicked_at": None, "click_count": 0,
    })

    with patch("routers.onboarding.get_db", return_value=db):
        first  = await r.onboarding_click(uid="u1", c="connect_repo_nudge")
        second = await r.onboarding_click(uid="u1", c="connect_repo_nudge")

    # Both redirects land on the dashboard with the right params
    assert first.status_code == 302
    assert second.status_code == 302
    assert "action=connect-repo" in first.headers["location"]
    assert "utm_source=email" in first.headers["location"]

    row = db.sent[0]
    assert row["clicked_at"] is not None       # stamped exactly once
    assert row["click_count"] == 2             # incremented on every click
    assert row.get("last_clicked_at") is not None


@pytest.mark.asyncio
async def test_click_endpoint_redirects_even_on_unknown_uid():
    from routers import onboarding as r
    db = _FakeDB()
    with patch("routers.onboarding.get_db", return_value=db):
        resp = await r.onboarding_click(uid="ghost", c="connect_repo_nudge")
    assert resp.status_code == 302
    assert "/dashboard?" in resp.headers["location"]
    assert db.sent == []                       # no row created


# ════════════════════════════════════════════════════════════════════
# 6. Source pins — wiring contracts
# ════════════════════════════════════════════════════════════════════

def test_main_wires_onboarding_router_and_cron():
    src = open("/app/backend/main.py").read()
    assert "from routers.onboarding import router as onboarding_router" in src
    assert "app.include_router(onboarding_router" in src
    assert "from services.onboarding_email import nudge_cron" in src
    assert "nudge_task" in src


def test_dashboard_handles_action_query_param():
    src = open("/app/frontend/src/pages/Dashboard.jsx").read()
    assert "useSearchParams" in src
    assert '"action") === "connect-repo"' in src or 'action") === "connect-repo"' in src
    assert "setShowWizard(true)" in src


def test_signoff_matches_user_request_exactly():
    """Founder name is the user's specific choice and must not drift."""
    src = open("/app/backend/services/onboarding_email.py").read()
    assert "— Tejinder Sandhu, Founder, Aurem" in src
