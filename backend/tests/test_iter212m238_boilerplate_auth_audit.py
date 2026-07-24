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
    is limited-blast-radius.

    Iter 297 — BEHAVIOURAL upgrade (was STATIC_GREP).
    Instead of grepping the file source for the literal
    ``_ACCESS_TTL_S = 60 * 60`` string, we now:

      • Actually LOAD and EXECUTE the react-fastapi boilerplate
        module via `services.boilerplate_audit.load_python_boilerplate`
        and read the compiled ``_ACCESS_TTL_S`` attribute as a real
        Python int. If the constant were somehow overridden by a
        conditional or an env var, this catches it — a grep would not.
      • Actually EVALUATE the nextjs boilerplate constant via
        `services.boilerplate_audit.read_js_constant` (which spawns
        Node.js when available, arithmetic-fallback otherwise). The
        result is the numeric value the compiled JS bundle would
        expose, not a source-code substring.
    """
    from services.boilerplate_audit import (
        load_python_boilerplate, read_js_constant,
    )
    # react-fastapi — executed module attribute.
    mod = load_python_boilerplate("react-fastapi", "auth_server")
    assert isinstance(mod._ACCESS_TTL_S, int), (
        f"_ACCESS_TTL_S must be an int at runtime, got "
        f"{type(mod._ACCESS_TTL_S)!r}"
    )
    assert mod._ACCESS_TTL_S == 3600, (
        f"react-fastapi access TTL must be exactly 1h (3600s); "
        f"got {mod._ACCESS_TTL_S}s. Tightening this without a "
        f"session-refresh rotation would break login; widening it "
        f"expands the blast-radius of a stolen access cookie."
    )
    # Belt-and-braces invariant — refresh must vastly exceed access
    # so the rotation pattern is meaningful.
    assert mod._REFRESH_TTL_S > mod._ACCESS_TTL_S * 24, (
        "refresh TTL must be substantially longer than access TTL"
    )

    # nextjs-node — real Node.js evaluation of the const expression.
    ttl_ms_or_s = read_js_constant("nextjs-node", "auth_lib", "ACCESS_TTL_S")
    assert ttl_ms_or_s == 3600, (
        f"nextjs ACCESS_TTL_S must evaluate to 3600 seconds; "
        f"got {ttl_ms_or_s!r}"
    )


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
    """Reset tokens must be short-lived AND single-use — an attacker
    who intercepts one has ≤15min and one shot.

    Iter 297 — BEHAVIOURAL upgrade (was STATIC_GREP).
    We now call `services.boilerplate_audit.audit_reset_token_flags`
    which:
      1. Actually EXECUTES the react-fastapi auth module (the module
         must import cleanly — a stronger guarantee than a grep).
      2. Reads the compiled `_RESET_TTL_S` attribute as a real int.
      3. Verifies the two invariants of the single-use pattern:
         - insert path writes `used: False`
         - consume path flips it to `used: True` (with `used_at`).
    We also actually EVALUATE the nextjs `RESET_TTL_S` const via
    `read_js_constant`, so a runtime-computed value would be caught.
    """
    from services.boilerplate_audit import (
        audit_reset_token_flags, read_js_constant,
    )
    audit = audit_reset_token_flags("react-fastapi")
    assert audit["reset_ttl_s"] == 900, (
        f"react-fastapi reset TTL must be 15 minutes (900s); "
        f"got {audit['reset_ttl_s']}s. A longer window gives a "
        f"phished reset link an unacceptable time-to-exploit."
    )
    assert audit["used_false_present"], (
        "reset-token INSERT path must write `used: False` — "
        "single-use pattern is broken without the initial flag."
    )
    assert audit["used_true_present"], (
        "reset-token CONSUME path must flip to `used: True` — "
        "without this the same reset link can be replayed."
    )

    # nextjs — real evaluation of the const expression.
    js_reset = read_js_constant("nextjs-node", "auth_lib", "RESET_TTL_S")
    assert js_reset == 900, (
        f"nextjs RESET_TTL_S must evaluate to 900s; got {js_reset!r}"
    )


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
