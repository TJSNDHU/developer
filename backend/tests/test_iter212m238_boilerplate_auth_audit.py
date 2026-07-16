"""
Iter 212m-238 — Boilerplate auth security audit regression.

Locks in the OWASP AI-code checklist decisions applied to every
generated Personal Track app:

    1. NO localStorage for tokens (XSS-vulnerable). Cookies only.
    2. httpOnly + SameSite=Lax + Secure-in-prod on every auth cookie.
    3. Access + refresh token pattern (1h + 30d).
    4. Rate limiting on every sensitive endpoint (signup, login, reset).
    5. Enumeration-safe password reset: 202 with generic message,
       token in BODY not URL, 15-min TTL, single-use.
    6. Constant-time password compare (bcrypt.checkpw / bcrypt.compare
       always runs, even on missing users, to prevent timing leaks).

A single failure here means a generated app will ship with a known
vulnerability. Any future refactor that reintroduces localStorage or
skips rate-limit MUST fail CI.
"""
from __future__ import annotations

import os
import re


# ── Files under audit ──────────────────────────────────────────────
STACKS = {
    "react-fastapi": {
        "auth_server": "/app/backend/templates/stacks/react-fastapi/boilerplate/api/auth.py",
        "auth_client": "/app/backend/templates/stacks/react-fastapi/boilerplate/ui/src/App.jsx",
    },
    "nextjs-node": {
        "auth_lib":     "/app/backend/templates/stacks/nextjs-node/boilerplate/lib/auth.js",
        "signup":       "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/signup/route.js",
        "login":        "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/login/route.js",
        "refresh":      "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/refresh/route.js",
        "reset_req":    "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/password-reset-request/route.js",
        "reset_conf":   "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/password-reset-confirm/route.js",
    },
    "vue-express": {
        "server": "/app/backend/templates/stacks/vue-express/boilerplate/server/index.js",
    },
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 1. NO localStorage token storage ANYWHERE ────────────────────
def test_no_localstorage_token_storage_in_any_boilerplate():
    """The Lovable-lesson invariant. If ANY future refactor writes
    a token into localStorage, CI must fail here."""
    banned = [
        re.compile(r"""localStorage\.setItem\s*\(\s*['"](?:token|session|access_token|jwt|auth)"""),
        re.compile(r"""localStorage\.getItem\s*\(\s*['"](?:token|session|access_token|jwt|auth)"""),
        re.compile(r"""localStorage\[\s*['"](?:token|session|access_token|jwt|auth)"""),
    ]
    for stack, files in STACKS.items():
        for label, path in files.items():
            src = _read(path)
            for rx in banned:
                m = rx.search(src)
                assert not m, (
                    f"[{stack}/{label}] localStorage token-storage pattern "
                    f"reintroduced: {m.group(0)!r} in {path}. This is an "
                    f"XSS-vulnerable pattern — use httpOnly cookies instead."
                )


# ── 2. httpOnly + SameSite + Secure-in-prod on cookies ───────────
def test_react_fastapi_uses_httponly_cookies():
    src = _read(STACKS["react-fastapi"]["auth_server"])
    assert "httponly=True" in src
    assert 'samesite="lax"' in src
    assert "secure=_IS_PROD" in src, "Secure flag must scale with APP_ENV"


def test_nextjs_uses_httponly_cookies():
    src = _read(STACKS["nextjs-node"]["auth_lib"])
    assert "httpOnly: true" in src
    assert 'sameSite: "lax"' in src
    assert 'process.env.NODE_ENV === "production"' in src


def test_vue_express_uses_httponly_cookies():
    src = _read(STACKS["vue-express"]["server"])
    assert "httpOnly: true" in src
    assert 'sameSite: "lax"' in src
    assert "IS_PROD" in src


# ── 3. Access + refresh token pattern ────────────────────────────
def test_all_stacks_expose_refresh_endpoint():
    r = _read(STACKS["react-fastapi"]["auth_server"])
    assert "@router.post(\"/refresh\")" in r or '"/refresh"' in r
    assert "_ACCESS_TTL_S" in r and "_REFRESH_TTL_S" in r

    assert os.path.isfile(STACKS["nextjs-node"]["refresh"])
    n = _read(STACKS["nextjs-node"]["refresh"])
    assert "session_r" in n
    assert 'payload.typ !== "refresh"' in n

    v = _read(STACKS["vue-express"]["server"])
    assert '"/api/auth/refresh"' in v
    assert "session_r" in v


def test_access_token_ttl_is_short_lived():
    """Access tokens should be short-lived (≤ 2h) so a stolen cookie
    is limited-blast-radius."""
    r = _read(STACKS["react-fastapi"]["auth_server"])
    # _ACCESS_TTL_S = 60 * 60  → 3600
    assert "_ACCESS_TTL_S     = 60 * 60" in r or "_ACCESS_TTL_S = 60 * 60" in r

    n = _read(STACKS["nextjs-node"]["auth_lib"])
    assert "ACCESS_TTL_S  = 60 * 60" in n or "ACCESS_TTL_S = 60 * 60" in n


# ── 4. Rate limiting on sensitive endpoints ──────────────────────
def test_react_fastapi_rate_limits_all_sensitive_endpoints():
    src = _read(STACKS["react-fastapi"]["auth_server"])
    for bucket in ("signup", "login", "reset_request", "reset_confirm"):
        assert f'_rate_limit_check("{bucket}"' in src, f"missing rate-limit on {bucket}"


def test_nextjs_rate_limits_all_sensitive_endpoints():
    for label in ("signup", "login", "reset_req", "reset_conf"):
        src = _read(STACKS["nextjs-node"][label])
        assert "rateLimitCheck(" in src, f"nextjs {label} missing rate limit"


def test_vue_express_rate_limits_all_sensitive_endpoints():
    src = _read(STACKS["vue-express"]["server"])
    for bucket in ('"signup"', '"login"', '"reset_request"', '"reset_confirm"'):
        assert f"rateLimit({bucket})" in src, f"vue-express missing rate limit on {bucket}"


# ── 5. Password reset — enumeration-safe + body-only + short TTL ──
def test_password_reset_returns_202_with_generic_message():
    """Reset REQUEST must always return 202 with a generic message,
    never leaking whether the email exists."""
    r = _read(STACKS["react-fastapi"]["auth_server"])
    assert 'status_code=202' in r
    assert "If an account matches that email" in r

    n = _read(STACKS["nextjs-node"]["reset_req"])
    assert "status: 202" in n
    assert "If an account matches that email" in n

    v = _read(STACKS["vue-express"]["server"])
    assert "status(202)" in v
    assert "If an account matches that email" in v


def test_reset_token_read_from_body_not_url_query():
    """Reset CONFIRM must read the token from the request BODY —
    URL query params leak into logs, referer headers, browser history."""
    r = _read(STACKS["react-fastapi"]["auth_server"])
    # Pydantic body model definition:
    assert "class ResetConfirmIn(BaseModel):" in r
    assert "token:        str" in r
    # No `request.query_params.get("token")` or similar:
    assert "query_params" not in r or "request.query_params.get('token'" not in r

    n = _read(STACKS["nextjs-node"]["reset_conf"])
    assert "req.json()" in n           # body parse
    assert "searchParams.get(" not in n  # no URL-param read


def test_reset_token_has_short_ttl_and_single_use():
    r = _read(STACKS["react-fastapi"]["auth_server"])
    assert "_RESET_TTL_S      = 15 * 60" in r or "_RESET_TTL_S = 15 * 60" in r
    assert '"used":       False' in r or '"used": False' in r
    assert '{"used": True' in r or '"used": True, "used_at"' in r

    n = _read(STACKS["nextjs-node"]["auth_lib"])
    assert "RESET_TTL_S   = 15 * 60" in n or "RESET_TTL_S = 15 * 60" in n


# ── 6. Constant-time password compare (timing side-channel guard) ──
def test_login_runs_bcrypt_even_on_missing_user():
    """OWASP anti-timing pattern: always run bcrypt so response time
    doesn't reveal whether the email exists."""
    r = _read(STACKS["react-fastapi"]["auth_server"])
    assert "dummy-timing-guard" in r

    n = _read(STACKS["nextjs-node"]["login"])
    assert "dummy-timing-guard" in n

    v = _read(STACKS["vue-express"]["server"])
    assert "dummy-timing-guard" in v


# ── 7. Frontend uses cookies (credentials: 'include') ────────────
def test_react_client_uses_credentials_include_not_localstorage():
    src = _read(STACKS["react-fastapi"]["auth_client"])
    assert 'credentials: "include"' in src or "credentials: 'include'" in src
    # Belt-and-suspenders: banned pattern already checked above; assert
    # the affirmative pattern too.
    assert "Bearer" not in src, (
        "React client should not manually mint Bearer headers — "
        "cookie-based auth is the pattern."
    )


# ── 8. Enumeration-safe response shape on signup taken-email ─────
def test_signup_taken_email_uses_neutral_message():
    """Whether or not email enumeration is fully preventable at signup
    is a policy trade-off (users need to know if their email is taken
    to be able to sign in). We enforce the softer version: the error
    message must not mention "already exists" or the specific email —
    it says "can't be used" (which could be "already taken" OR
    "reserved" OR "invalid" from the attacker's POV)."""
    for stack, label in [
        ("react-fastapi", "auth_server"),
        ("nextjs-node",   "signup"),
        ("vue-express",   "server"),
    ]:
        src = _read(STACKS[stack][label])
        assert "can't be used to sign up" in src
        assert "already exists" not in src.lower() or "already exists" not in re.search(
            r"(?i)status.*?409.*?[^\n]+", src, re.DOTALL,
        ).group(0)
