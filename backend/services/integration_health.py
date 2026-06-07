"""
services/integration_health.py — REAL live probes for every external
provider the platform depends on. No mocks, no shortcuts — each probe
hits the actual API and returns a structured status the admin UI can
render.

Output shape (per provider):
    {
      "id":        "stripe",
      "name":      "Stripe",
      "status":    "ok" | "warn" | "broken" | "missing",
      "summary":   short human string ("Live, $1234 balance")
      "detail":    longer string with whatever the probe found
      "fix_hint":  what the founder should do next (URL or instruction)
      "checked_at": ISO timestamp
      "latency_ms": int — how long the probe took
    }

Status semantics:
  ok       — fully working, no action needed
  warn     — works but degraded (e.g. credits low, no verified domains)
  broken   — key invalid / quota exhausted / API returned non-2xx
  missing  — env var not configured (or empty string)

Every probe is wrapped to NEVER raise into the caller — any unexpected
exception returns status="broken" with the message in `detail`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 12.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_env(*keys: str) -> str:
    for k in keys:
        v = (os.environ.get(k) or "").strip().strip('"').strip("'")
        if v and not v.startswith("sk_test_emergent"):
            return v
    return ""


def _result(id_: str, name: str, status: str, summary: str,
            detail: str = "", fix_hint: str = "",
            latency_ms: int = 0) -> dict:
    return {
        "id":         id_,
        "name":       name,
        "status":     status,
        "summary":    summary,
        "detail":     detail[:500],
        "fix_hint":   fix_hint,
        "checked_at": _now_iso(),
        "latency_ms": latency_ms,
    }


async def _run(coro: Awaitable, id_: str, name: str,
               fix_hint: str = "") -> dict:
    """Run a probe coroutine with timeout + exception isolation."""
    t0 = time.time()
    try:
        result = await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT)
        # Probe returned its own dict — just add latency.
        result["latency_ms"] = int((time.time() - t0) * 1000)
        return result
    except asyncio.TimeoutError:
        return _result(id_, name, "broken",
                       summary=f"Probe timed out after {PROBE_TIMEOUT}s",
                       detail="The API didn't respond in time.",
                       fix_hint=fix_hint,
                       latency_ms=int(PROBE_TIMEOUT * 1000))
    except Exception as e:
        logger.warning(f"[health] {id_} probe crashed: {e!r}")
        return _result(id_, name, "broken",
                       summary=f"Probe crashed: {type(e).__name__}",
                       detail=str(e),
                       fix_hint=fix_hint,
                       latency_ms=int((time.time() - t0) * 1000))


# ────────────────────────────────────────────────────────────────────────
# Per-provider probes
# ────────────────────────────────────────────────────────────────────────

async def _probe_stripe() -> dict:
    key = _safe_env("STRIPE_SECRET_KEY", "STRIPE_API_KEY")
    # Also reach into .env directly to dodge the platform's stale placeholder.
    if not key:
        try:
            from dotenv import dotenv_values
            env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
            vals = dotenv_values(env_path)
            for k in ("STRIPE_SECRET_KEY", "STRIPE_API_KEY"):
                v = (vals.get(k) or "").strip().strip('"').strip("'")
                if v and not v.startswith("sk_test_emergent"):
                    key = v
                    break
        except Exception:
            pass
    if not key:
        return _result("stripe", "Stripe", "missing",
                       summary="STRIPE_SECRET_KEY not configured",
                       fix_hint="Add real sk_live_… key from dashboard.stripe.com/apikeys to .env")
    try:
        import stripe
        stripe.api_key = key
        acct = stripe.Account.retrieve()
        prices = stripe.Price.list(active=True, limit=5)
        price_ids = [
            os.environ.get("STRIPE_STARTER_PRICE_ID"),
            os.environ.get("STRIPE_PRO_PRICE_ID"),
            os.environ.get("STRIPE_TEAM_PRICE_ID"),
        ]
        missing_prices = [p for p in price_ids if not p]
        # Detect mode
        mode = "live" if key.startswith("sk_live_") else "test"
        summary = f"{mode.upper()} mode • {acct.business_profile.name if acct.business_profile else acct.id}"
        if not acct.charges_enabled:
            return _result("stripe", "Stripe", "warn",
                           summary=f"{summary} (charges DISABLED)",
                           detail="Stripe account exists but cannot accept payments.",
                           fix_hint="Complete onboarding at dashboard.stripe.com/account")
        if missing_prices:
            return _result("stripe", "Stripe", "warn",
                           summary=summary,
                           detail=f"{len(missing_prices)}/3 price IDs missing in env",
                           fix_hint="Set STRIPE_STARTER_PRICE_ID, STRIPE_PRO_PRICE_ID, STRIPE_TEAM_PRICE_ID")
        return _result("stripe", "Stripe", "ok",
                       summary=summary,
                       detail=f"acct={acct.id} • charges & payouts enabled • {len(prices.data)} active prices")
    except Exception as e:
        return _result("stripe", "Stripe", "broken",
                       summary="Stripe API rejected the key",
                       detail=str(e),
                       fix_hint="Rotate STRIPE_SECRET_KEY from dashboard.stripe.com/apikeys")


async def _probe_github_oauth() -> dict:
    cid = _safe_env("GITHUB_OAUTH_CLIENT_ID")
    csec = _safe_env("GITHUB_OAUTH_CLIENT_SECRET")
    redirect = _safe_env("GITHUB_REDIRECT_URI")
    if not (cid and csec):
        return _result("github_oauth", "GitHub OAuth", "missing",
                       summary="OAuth credentials not configured",
                       fix_hint="github.com/settings/developers → New OAuth App")
    if not redirect:
        return _result("github_oauth", "GitHub OAuth", "warn",
                       summary="Credentials set but GITHUB_REDIRECT_URI missing",
                       detail=f"client_id={cid[:8]}…",
                       fix_hint="Set GITHUB_REDIRECT_URI to https://auremcto.com/api/aurem-dev/github/oauth/callback")
    # We can't validate the secret without an OAuth code flow, but we
    # can at least verify GitHub's OAuth endpoint accepts our client_id.
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://github.com/login/oauth/authorize",
            params={"client_id": cid, "redirect_uri": redirect,
                    "state": "healthcheck"},
            follow_redirects=False,
        )
        # GitHub returns 302 for valid client_id, 404/error page for bad one.
        if r.status_code in (200, 302):
            return _result("github_oauth", "GitHub OAuth", "ok",
                           summary=f"client_id {cid[:10]}… accepted by GitHub",
                           detail=f"redirect → {redirect}")
        return _result("github_oauth", "GitHub OAuth", "broken",
                       summary=f"GitHub rejected client_id ({r.status_code})",
                       detail=r.text[:200],
                       fix_hint="Re-verify OAuth App at github.com/settings/developers")


async def _probe_tavily() -> dict:
    key = _safe_env("TAVILY_API_KEY")
    if not key:
        return _result("tavily", "Tavily Search", "missing",
                       summary="TAVILY_API_KEY not configured",
                       fix_hint="Get key at app.tavily.com")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": "aurem cto healthcheck", "max_results": 1},
        )
        if r.status_code == 200:
            data = r.json()
            n = len(data.get("results") or [])
            return _result("tavily", "Tavily Search", "ok",
                           summary=f"Live • returned {n} result(s)",
                           detail="Web-search skill operational")
        if r.status_code == 401:
            return _result("tavily", "Tavily Search", "broken",
                           summary="Invalid API key",
                           detail=r.text[:200],
                           fix_hint="Rotate TAVILY_API_KEY at app.tavily.com")
        if r.status_code in (402, 429):
            return _result("tavily", "Tavily Search", "warn",
                           summary=f"Credits exhausted or rate-limited ({r.status_code})",
                           detail=r.text[:200],
                           fix_hint="Top up at tavily.com/pricing")
        return _result("tavily", "Tavily Search", "broken",
                       summary=f"Unexpected HTTP {r.status_code}",
                       detail=r.text[:200])


async def _probe_firecrawl() -> dict:
    key = _safe_env("FIRECRAWL_API_KEY")
    if not key:
        return _result("firecrawl", "Firecrawl Scrape", "missing",
                       summary="FIRECRAWL_API_KEY not configured",
                       fix_hint="Get key at firecrawl.dev/app")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {key}"},
            json={"url": "https://example.com", "formats": ["markdown"]},
        )
        if r.status_code == 200:
            data = r.json()
            ok = data.get("success", False)
            return _result("firecrawl", "Firecrawl Scrape",
                           "ok" if ok else "warn",
                           summary=f"Live • scraped example.com",
                           detail=f"success={ok}")
        if r.status_code == 401:
            return _result("firecrawl", "Firecrawl Scrape", "broken",
                           summary="Invalid API key",
                           detail=r.text[:200],
                           fix_hint="Rotate at firecrawl.dev/app")
        if r.status_code == 402:
            return _result("firecrawl", "Firecrawl Scrape", "warn",
                           summary="Credits exhausted",
                           detail=r.text[:200],
                           fix_hint="Top up at firecrawl.dev/pricing")
        return _result("firecrawl", "Firecrawl Scrape", "broken",
                       summary=f"HTTP {r.status_code}",
                       detail=r.text[:200])


async def _probe_resend() -> dict:
    key = _safe_env("RESEND_API_KEY")
    if not key:
        return _result("resend", "Resend Email", "missing",
                       summary="RESEND_API_KEY not configured",
                       fix_hint="Get key at resend.com/api-keys")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code != 200:
            return _result("resend", "Resend Email", "broken",
                           summary=f"HTTP {r.status_code}",
                           detail=r.text[:200],
                           fix_hint="Rotate RESEND_API_KEY at resend.com/api-keys")
        domains = r.json().get("data", []) or []
        verified = [d for d in domains if d.get("status") == "verified"]
        from_email = os.environ.get("RESEND_FROM_EMAIL", "")
        if not verified:
            return _result("resend", "Resend Email", "warn",
                           summary=f"Key valid but 0 verified domains",
                           detail=f"Found {len(domains)} domains, none verified",
                           fix_hint="Verify aurem.live via DNS records in Resend dashboard")
        vlist = ", ".join(d.get("name") for d in verified)
        return _result("resend", "Resend Email", "ok",
                       summary=f"Live • {len(verified)} verified domain(s)",
                       detail=f"Domains: {vlist} • from={from_email!r}")


async def _probe_e2b() -> dict:
    key = _safe_env("E2B_API_KEY")
    if not key:
        return _result("e2b", "E2B Sandbox", "missing",
                       summary="E2B_API_KEY not configured",
                       fix_hint="Get key at e2b.dev")
    # Real sandbox spin-up. Tighter timeout (sandbox boot is fast but not instant).
    try:
        from e2b_code_interpreter import Sandbox
    except ImportError:
        return _result("e2b", "E2B Sandbox", "broken",
                       summary="e2b-code-interpreter SDK not installed",
                       fix_hint="pip install e2b-code-interpreter")
    sbx = None
    try:
        sbx = Sandbox.create(api_key=key, timeout=15)
        ex = sbx.run_code("print(2+2)")
        logs = getattr(ex, "logs", None)
        out = ("".join(getattr(logs, "stdout", None) or [])).strip()
        if "4" not in out:
            return _result("e2b", "E2B Sandbox", "warn",
                           summary="Sandbox ran but output unexpected",
                           detail=f"stdout={out!r}")
        return _result("e2b", "E2B Sandbox", "ok",
                       summary=f"Live • sandbox {sbx.sandbox_id} executed Python",
                       detail="Real code execution operational")
    except Exception as e:
        msg = str(e)
        if "401" in msg or "Invalid" in msg.lower() or "auth" in msg.lower():
            return _result("e2b", "E2B Sandbox", "broken",
                           summary="Invalid API key",
                           detail=msg[:300],
                           fix_hint="Rotate E2B_API_KEY at e2b.dev")
        if "quota" in msg.lower() or "limit" in msg.lower():
            return _result("e2b", "E2B Sandbox", "warn",
                           summary="Quota/limit hit",
                           detail=msg[:300],
                           fix_hint="Upgrade plan at e2b.dev/pricing")
        return _result("e2b", "E2B Sandbox", "broken",
                       summary=f"Sandbox error: {type(e).__name__}",
                       detail=msg[:300])
    finally:
        if sbx is not None:
            try:
                sbx.kill()
            except Exception:
                pass


async def _probe_sentry() -> dict:
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return _result("sentry", "Sentry Monitoring", "missing",
                       summary="SENTRY_DSN not configured",
                       fix_hint="sentry.io → Settings → Projects → Client Keys (DSN)")
    if not (dsn.startswith("https://") and ".ingest." in dsn and ".sentry.io/" in dsn):
        return _result("sentry", "Sentry Monitoring", "broken",
                       summary="DSN format invalid",
                       detail=f"got: {dsn[:80]}",
                       fix_hint="DSN must look like https://xxx@oXXX.ingest.us.sentry.io/XXX")
    # Sentry doesn't have a "validate DSN" API endpoint — best we can do is
    # confirm the SDK initialized cleanly on this process.
    try:
        import main as _main
        active = bool(getattr(_main, "SENTRY_ACTIVE", False))
    except Exception:
        active = False
    if not active:
        return _result("sentry", "Sentry Monitoring", "warn",
                       summary="DSN set but SDK didn't initialize",
                       detail="Check backend logs for `Sentry init failed`",
                       fix_hint="Restart backend; verify _sentry_filter is defined before init")
    env = os.environ.get("SENTRY_ENV", "production")
    return _result("sentry", "Sentry Monitoring", "ok",
                   summary=f"Active • env={env}",
                   detail=f"DSN: …{dsn[-40:]}")


async def _probe_vercel() -> dict:
    key = _safe_env("VERCEL_API_TOKEN")
    if not key:
        return _result("vercel", "Vercel Deploy", "missing",
                       summary="VERCEL_API_TOKEN not configured",
                       fix_hint="vercel.com/account/tokens → Create Token")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://api.vercel.com/v2/user",
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code != 200:
            return _result("vercel", "Vercel Deploy", "broken",
                           summary=f"HTTP {r.status_code}",
                           detail=r.text[:200],
                           fix_hint="Rotate token at vercel.com/account/tokens")
        user = r.json().get("user", {})
        return _result("vercel", "Vercel Deploy", "ok",
                       summary=f"Live • user {user.get('username','?')}",
                       detail=f"email={user.get('email','?')}")


async def _probe_mongodb() -> dict:
    mongo_url = os.environ.get("MONGO_URL") or ""
    if not mongo_url:
        return _result("mongodb", "MongoDB", "missing",
                       summary="MONGO_URL not configured",
                       fix_hint="Set MONGO_URL in .env")
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return _result("mongodb", "MongoDB", "broken",
                           summary="Database client not initialised",
                           detail="cto_services.db.get_db() returned None",
                           fix_hint="Check main.py lifespan startup logs")
        # Real round-trip
        pong = await db.command("ping")
        ok = bool(pong.get("ok"))
        # Count a few key collections
        stats = {}
        for col in ("dev_users", "cto_tasks", "cto_payments", "chat_sessions"):
            try:
                stats[col] = await db[col].estimated_document_count()
            except Exception:
                stats[col] = -1
        return _result("mongodb", "MongoDB",
                       "ok" if ok else "warn",
                       summary=f"Live • {stats.get('dev_users','?')} users, {stats.get('cto_tasks','?')} tasks",
                       detail=f"ping={pong} • collections={stats}")
    except Exception as e:
        return _result("mongodb", "MongoDB", "broken",
                       summary=f"Connection failed: {type(e).__name__}",
                       detail=str(e),
                       fix_hint="Check MONGO_URL + network reachability")


async def _probe_emergent_llm() -> dict:
    key = _safe_env("EMERGENT_LLM_KEY")
    if not key:
        return _result("emergent_llm", "Emergent LLM (Claude)", "missing",
                       summary="EMERGENT_LLM_KEY not configured",
                       fix_hint="Profile → Universal Key in Emergent dashboard")
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import uuid as _uuid
        chat = (
            LlmChat(api_key=key, session_id=f"healthcheck-{_uuid.uuid4().hex[:8]}",
                    system_message="Reply with exactly the word: OK")
            .with_model("anthropic", "claude-haiku-4-5")
            .with_params(max_tokens=10, temperature=0.0)
        )
        resp = await chat.send_message(UserMessage(text="ping"))
        text = (resp or "").strip()
        if "OK" in text.upper() or len(text) > 0:
            return _result("emergent_llm", "Emergent LLM (Claude)", "ok",
                           summary=f"Live • Claude responded ({len(text)} chars)",
                           detail=f"reply: {text[:80]!r}")
        return _result("emergent_llm", "Emergent LLM (Claude)", "warn",
                       summary="Empty reply from Claude",
                       detail=f"got: {text!r}")
    except Exception as e:
        msg = str(e)
        if "budget" in msg.lower() or "quota" in msg.lower() or "402" in msg:
            return _result("emergent_llm", "Emergent LLM (Claude)", "warn",
                           summary="Budget exhausted",
                           detail=msg[:300],
                           fix_hint="Profile → Universal Key → Add Balance in Emergent dashboard")
        if "401" in msg or "invalid" in msg.lower():
            return _result("emergent_llm", "Emergent LLM (Claude)", "broken",
                           summary="Key rejected",
                           detail=msg[:300],
                           fix_hint="Rotate EMERGENT_LLM_KEY from Emergent profile")
        return _result("emergent_llm", "Emergent LLM (Claude)", "broken",
                       summary=f"LLM error: {type(e).__name__}",
                       detail=msg[:300])


async def _probe_openrouter() -> dict:
    key = _safe_env("OPENROUTER_API_KEY")
    if not key:
        return _result("openrouter", "OpenRouter (DeepSeek)", "missing",
                       summary="OPENROUTER_API_KEY not configured",
                       fix_hint="Get key at openrouter.ai/keys")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code == 200:
            data = r.json().get("data") or {}
            total = float(data.get("total_credits") or 0)
            used = float(data.get("total_usage") or 0)
            remaining = total - used
            status = "ok"
            fix = ""
            if remaining < 1.0:
                status = "warn"
                fix = "Top up at openrouter.ai/credits"
            return _result("openrouter", "OpenRouter (DeepSeek)", status,
                           summary=f"${remaining:.2f} remaining (${used:.2f} used)",
                           detail=f"total_credits=${total:.2f}",
                           fix_hint=fix)
        if r.status_code == 401:
            return _result("openrouter", "OpenRouter (DeepSeek)", "broken",
                           summary="Invalid API key",
                           detail=r.text[:200],
                           fix_hint="Rotate at openrouter.ai/keys")
        return _result("openrouter", "OpenRouter (DeepSeek)", "broken",
                       summary=f"HTTP {r.status_code}",
                       detail=r.text[:200])


# ────────────────────────────────────────────────────────────────────────
# Public entry points
# ────────────────────────────────────────────────────────────────────────

# Maps id → (display name, async probe fn, fix-hint fallback)
_PROBES: list[tuple[str, str, Callable[[], Awaitable[dict]]]] = [
    ("stripe",        "Stripe",                _probe_stripe),
    ("github_oauth",  "GitHub OAuth",          _probe_github_oauth),
    ("emergent_llm",  "Emergent LLM (Claude)", _probe_emergent_llm),
    ("openrouter",    "OpenRouter (DeepSeek)", _probe_openrouter),
    ("e2b",           "E2B Sandbox",           _probe_e2b),
    ("tavily",        "Tavily Search",         _probe_tavily),
    ("firecrawl",     "Firecrawl Scrape",      _probe_firecrawl),
    ("resend",        "Resend Email",          _probe_resend),
    ("sentry",        "Sentry Monitoring",     _probe_sentry),
    ("vercel",        "Vercel Deploy",         _probe_vercel),
    ("mongodb",       "MongoDB",               _probe_mongodb),
]


async def run_all_probes() -> list[dict]:
    """Run every probe concurrently. Returns a list of result dicts in
    the same order as _PROBES."""
    coros = [_run(fn(), id_, name) for id_, name, fn in _PROBES]
    results = await asyncio.gather(*coros, return_exceptions=False)
    return list(results)


def summary_counts(results: list[dict]) -> dict:
    """Reduce a probe list to {ok, warn, broken, missing} counts."""
    out = {"ok": 0, "warn": 0, "broken": 0, "missing": 0, "total": len(results)}
    for r in results:
        s = r.get("status", "broken")
        if s in out:
            out[s] += 1
    return out
