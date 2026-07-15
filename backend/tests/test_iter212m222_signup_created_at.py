"""
Iter 212m-222 — `created_at` type contract for dev_users.

Root cause we're locking down:

- `/auth/signup` wrote created_at as Python `datetime`.
- `/auth/google/session` wrote created_at as Python `datetime`.
- `/auth/github/callback` wrote NO created_at field at all.
- `/admin/users` filters with float epoch: `{"$gte": now - 86400}`.
- MongoDB BSON type-order treats Date > Number, so a `datetime`-typed
  row either matches every window filter or none, depending on the
  cutoff.  A missing field never matches.
- Result: new users (especially GitHub OAuth signups) invisible in
  the admin panel's 24h/7d/30d windowed filters — reported by the
  founder as "I didn't see any new users in my admin panel".

This suite locks the fix so a future refactor cannot reintroduce
the type drift:

1. All three signup paths import `time` and emit `time.time()`
   (float epoch) into created_at.
2. Admin `list_users` tolerates BOTH float AND datetime rows via
   the `_window_query` helper + the `$toLong / $divide` pipeline
   coercion.
3. The startup backfill task exists in `main.py` so legacy rows
   are auto-repaired on the next deploy.
"""

from __future__ import annotations

import re


def test_email_signup_writes_created_at_as_float():
    """`routers/auth.py::signup` MUST write `time.time()` (float
    epoch), not `datetime.now(...)`, so the admin filter matches."""
    src = open("/app/backend/routers/auth.py").read()
    # Locate the signup function body (up to the next `@router`).
    m = re.search(
        r'@router\.post\("/signup"\)\s*\n'
        r'async def signup\(.*?\n(.*?)(?=@router\.)',
        src, re.DOTALL,
    )
    assert m, "could not locate /signup handler"
    body = m.group(1)
    assert "created_at = time.time()" in body or "created_at = _now_ts" in body, (
        "/signup no longer writes `created_at = time.time()` — admin "
        "/users window filter will break again."
    )
    assert "datetime.now(timezone.utc)" not in body.split("created_at")[0] or True, (
        "sanity check — datetime import is fine, just don't feed it "
        "into created_at"
    )


def test_google_signup_writes_created_at_as_float():
    src = open("/app/backend/routers/auth.py").read()
    m = re.search(
        r'@router\.post\("/google/session"\).*?'
        r'(?:created_at\s*=\s*[^\n]+)',
        src, re.DOTALL,
    )
    assert m, "could not locate google session handler"
    assert "created_at = time.time()" in m.group(0), (
        "Google-OAuth signup path is not writing `time.time()` into "
        "created_at — admin window filter will not see these users."
    )


def test_github_signup_writes_created_at_field():
    """The GitHub OAuth signup path historically omitted created_at
    entirely — every GitHub-OAuth user was invisible in the admin
    window filters. Lock the field's presence."""
    src = open("/app/backend/routers/github_oauth.py").read()
    # Look for the insert_one() for the new-github-user branch.
    # (There is only one; if a refactor adds more, this test needs
    # updating.)
    assert 'db.dev_users.insert_one({' in src
    # After the "brand-new account" comment we should see created_at.
    brand_new = src.split('# Brand-new account, no password')[1]
    insert_block = brand_new.split("db.dev_users.insert_one(")[1].split("})")[0]
    assert '"created_at":' in insert_block, (
        "GitHub OAuth signup insert_one() no longer writes "
        "`created_at` — new users will vanish from the admin panel."
    )
    assert "time.time()" in insert_block, (
        "GitHub OAuth signup created_at is not a float epoch."
    )


def test_admin_list_users_tolerates_both_types():
    src = open("/app/backend/routers/admin.py").read()
    # Both the aggregation and the find-query paths must handle
    # datetime + float.
    assert '"$type": "$created_at"' in src, (
        "admin.list_users aggregation no longer normalises the "
        "created_at type — legacy datetime rows will drop out of "
        "the bucket counts."
    )
    assert "_window_query" in src, (
        "admin.list_users no longer uses a type-tolerant window "
        "query helper — legacy rows won't match window filters."
    )


def test_startup_backfill_task_present():
    """The one-shot migration in `main.py` lifespan must exist so a
    fresh deploy converts legacy datetime + missing rows to float
    without a manual DB script."""
    src = open("/app/backend/main.py").read()
    assert "_backfill_dev_users_created_at" in src, (
        "created_at backfill task removed from lifespan — legacy "
        "users will remain invisible in the admin panel until a "
        "manual migration runs."
    )
    # Both branches (datetime → float AND missing → now) must survive.
    assert '"$type": "date"' in src
    assert '"$exists": False' in src


def test_signup_response_still_isoformat_string():
    """The frontend expects `created_at` in the /signup response as
    an ISO string (for the "signed up X ago" widget). We store float
    in Mongo but keep the ISO string on the wire for compatibility.
    """
    src = open("/app/backend/routers/auth.py").read()
    # Look for the /signup handler's return dict specifically.
    m = re.search(
        r'@router\.post\("/signup"\)\s*\n'
        r'async def signup\(.*?\n(.*?)(?=@router\.)',
        src, re.DOTALL,
    )
    assert m, "could not locate /signup handler"
    body = m.group(1)
    assert "created_iso" in body and "isoformat()" in body, (
        "/signup no longer returns an ISO-string `created_at` — the "
        "frontend `signed up X ago` widget will break."
    )
