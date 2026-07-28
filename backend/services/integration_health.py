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

PROBE_TIMEOUT = 20.0  # Iter 212m-16 — bumped 12→20s. At 12s the daily
# 06:00 UTC cron was marking 7/11 probes as broken under event-loop
# contention, even though the integrations were fully functional
# (manual /integrations/refresh consistently shows them green in 2-4s
# each). 20s gives the parallel `asyncio.gather` more headroom on cold
# DNS / TLS hosts without inflating the manual refresh latency.


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
    # Iter 331 — the Stripe SDK is fully synchronous. Running these 8
    # sequential HTTP calls (Account.retrieve + Price.list + 6×
    # Price.retrieve) directly on the event loop froze the whole app
    # for seconds — PROD logs showed nginx `/health` upstream timeouts
    # (110) exactly during this probe. All sync work now runs in a
    # worker thread via asyncio.to_thread.
    def _sync_probe() -> dict:
        import stripe
        stripe.api_key = key
        acct = stripe.Account.retrieve()
        prices = stripe.Price.list(active=True, limit=5)
        # ── Iter 326 B · Per-price .recurring verification ──────────
        # Live evidence: founder's monthly checkout returned 400/502
        # ("needs valid recurring price IDs") because
        # /admin/architecture only verified the env var was SET, not
        # that each price actually had type=recurring. A monthly
        # price minted as type=one_time silently passed health and
        # only crashed at real user checkout time. Now: retrieve
        # each of the 6 configured price IDs and warn per-ID if
        # `.recurring` is missing or `.type != "recurring"`. Names
        # the offending env var in the warning so ops knows exactly
        # which one to rotate.
        price_env_names = [
            "STRIPE_STARTER_PRICE_ID",
            "STRIPE_PRO_PRICE_ID",
            "STRIPE_TEAM_PRICE_ID",
            "STRIPE_STARTER_ANNUAL_PRICE_ID",
            "STRIPE_PRO_ANNUAL_PRICE_ID",
            "STRIPE_TEAM_ANNUAL_PRICE_ID",
        ]
        env_price_map = {
            n: (os.environ.get(n) or "").strip() for n in price_env_names
        }
        missing_prices = [n for n, v in env_price_map.items() if not v]
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
                           detail=f"{len(missing_prices)}/6 price env vars missing: {', '.join(missing_prices)}",
                           fix_hint="Set the missing STRIPE_*_PRICE_ID env vars")
        # Verify each configured price is truly `recurring`.
        one_time_offenders: list[str] = []
        retrieval_errors: list[str] = []
        for env_name, pid in env_price_map.items():
            try:
                p = stripe.Price.retrieve(pid)
                p_type = getattr(p, "type", None) or (p.get("type") if hasattr(p, "get") else None)
                p_recurring = getattr(p, "recurring", None) or (p.get("recurring") if hasattr(p, "get") else None)
                if p_type != "recurring" or not p_recurring:
                    one_time_offenders.append(f"{env_name}={pid} (type={p_type or 'unknown'})")
            except stripe.error.StripeError as se:
                retrieval_errors.append(f"{env_name}={pid} ({getattr(se, 'user_message', None) or str(se)[:60]})")
        if retrieval_errors:
            return _result("stripe", "Stripe", "broken",
                           summary=summary,
                           detail=f"Stripe rejected {len(retrieval_errors)}/6 price IDs: "
                                  + " | ".join(retrieval_errors),
                           fix_hint="Rotate the failing STRIPE_*_PRICE_ID env vars")
        if one_time_offenders:
            return _result("stripe", "Stripe", "warn",
                           summary=summary + f" — {len(one_time_offenders)}/6 price(s) are one_time not recurring",
                           detail="Subscription checkout will 400 on these: "
                                  + " | ".join(one_time_offenders),
                           fix_hint="Recreate the offending price(s) as recurring "
                                    "(dashboard.stripe.com → Products → New price → "
                                    "toggle 'Recurring'), then update the env var to the new price_id")
        return _result("stripe", "Stripe", "ok",
                       summary=summary,
                       detail=f"acct={acct.id} • charges enabled • {len(prices.data)} active prices • "
                              f"all 6 configured price IDs verified recurring")

    try:
        return await asyncio.to_thread(_sync_probe)
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
        if r.status_code in (402, 429, 432):
            # ── Iter 326 A · Tavily 432 → warn (not broken) ─────────
            # HTTP 432 is Tavily's documented "plan usage limit
            # exceeded" code. Live evidence: `tvly-dev-*` free-tier
            # key returned {'detail':{'error':'This request exceeds
            # your plan's set usage limit.'}} — a soft top-up
            # prompt, not a critical outage. Classifier previously
            # only checked 402/429 so 432 fell through to `broken`
            # and painted /admin/architecture red.
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
    # ── Iter 326 · Firecrawl diagnostic instrumentation ───────────
    # Founder request: capture per-probe latency + failure signature
    # so the next prod-side 20s timeout has a real fingerprint
    # instead of just "timeout after 20s". Key hash (sha256[:8])
    # only — full key never logged. Diagnostic-only, no behaviour
    # change to the probe result contract.
    import hashlib
    import time as _t
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    key_prefix = key[:6]                 # e.g. `fc-b13b`, safe to log
    t0 = _t.monotonic()
    diag: dict = {
        "key_hash":   key_hash,
        "key_prefix": key_prefix,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {key}"},
                json={"url": "https://example.com", "formats": ["markdown"]},
            )
    except httpx.TimeoutException as _te:
        diag["elapsed_ms"] = int((_t.monotonic() - t0) * 1000)
        diag["signature"]  = "timeout"
        diag["exception"]  = type(_te).__name__
        # Preserve existing _result() contract but attach `diag`
        # dict so callers logging to integration_health_history
        # capture the fingerprint.
        return _result("firecrawl", "Firecrawl Scrape", "broken",
                       summary=f"Timeout after {diag['elapsed_ms']}ms",
                       detail=f"key_hash={key_hash} prefix={key_prefix} — no upstream response",
                       fix_hint="Verify prod k8s FIRECRAWL_API_KEY value matches active Firecrawl account")
    except Exception as _ex:                              # noqa: BLE001
        diag["elapsed_ms"] = int((_t.monotonic() - t0) * 1000)
        diag["signature"]  = f"exception:{type(_ex).__name__}"
        return _result("firecrawl", "Firecrawl Scrape", "broken",
                       summary=f"{type(_ex).__name__} at {diag['elapsed_ms']}ms",
                       detail=f"key_hash={key_hash} prefix={key_prefix} — {str(_ex)[:120]}",
                       fix_hint="Check prod egress + k8s secret")
    diag["elapsed_ms"]  = int((_t.monotonic() - t0) * 1000)
    diag["status_code"] = r.status_code
    diag["body_prefix"] = (r.text or "")[:200]
    diag["signature"]   = f"http_{r.status_code}"
    if r.status_code == 200:
        data = r.json()
        ok = data.get("success", False)
        return _result("firecrawl", "Firecrawl Scrape",
                       "ok" if ok else "warn",
                       summary=f"Live • scraped example.com in {diag['elapsed_ms']}ms",
                       detail=f"success={ok} key_hash={key_hash} prefix={key_prefix}")
    if r.status_code == 401:
        return _result("firecrawl", "Firecrawl Scrape", "broken",
                       summary="Invalid API key",
                       detail=f"key_hash={key_hash} prefix={key_prefix} — {r.text[:150]}",
                       fix_hint="Rotate at firecrawl.dev/app")
    if r.status_code == 402:
        return _result("firecrawl", "Firecrawl Scrape", "warn",
                       summary="Credits exhausted",
                       detail=f"key_hash={key_hash} prefix={key_prefix} — {r.text[:150]}",
                       fix_hint="Top up at firecrawl.dev/pricing")
    return _result("firecrawl", "Firecrawl Scrape", "broken",
                   summary=f"HTTP {r.status_code} in {diag['elapsed_ms']}ms",
                   detail=f"key_hash={key_hash} prefix={key_prefix} — {r.text[:150]}")


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
    # Iter 331 — the e2b SDK is synchronous (`e2b.api.client_sync` in
    # prod logs): a sandbox boot + run + kill (up to 15s+) directly on
    # the event loop blocked `/health` (prod nginx 110 timeouts at
    # 05:55:11→12 exactly during Sandbox.create). Sync work now runs
    # in a worker thread via asyncio.to_thread.
    def _sync_probe() -> dict:
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
        finally:
            if sbx is not None:
                try:
                    sbx.kill()
                except Exception:
                    pass

    try:
        return await asyncio.to_thread(_sync_probe)
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
        # Iter 212m-230 — Read SENTRY_ACTIVE from services.app_state
        # (populated by main.py at boot) instead of `import main` —
        # this was the last remaining routers/services → main
        # circular-import edge that architecture_health kept flagging.
        from services.app_state import get_state as _svc_get_state
        active = bool(_svc_get_state("SENTRY_ACTIVE", False))
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
    the same order as _PROBES. Used by the on-demand admin endpoint
    where latency matters."""
    coros = [_run(fn(), id_, name) for id_, name, fn in _PROBES]
    results = await asyncio.gather(*coros, return_exceptions=False)
    return list(results)


async def run_all_probes_serial(gap_s: float = 1.5) -> list[dict]:
    """Iter 336b — probes ONE at a time with a yield gap between each.

    The concurrent 11-probe burst (TLS ×11 + LiteLLM tokenizer init +
    e2b sandbox + Stripe) starved the event loop past nginx's 1 s
    /health proxy timeout on the 500m-CPU prod pod — EVERY cron fire
    (10 min) flapped readiness, and the post-deploy health check hit
    that window and failed the deployment. Serializing spreads the CPU
    over ~30 s, keeping /health <1 s throughout. The cron (600 s
    interval) doesn't care about probe-cycle latency."""
    results = []
    for id_, name, fn in _PROBES:
        results.append(await _run(fn(), id_, name))
        await asyncio.sleep(gap_s)
    return results


def summary_counts(results: list[dict]) -> dict:
    """Reduce a probe list to {ok, warn, broken, missing} counts."""
    out = {"ok": 0, "warn": 0, "broken": 0, "missing": 0, "total": len(results)}
    for r in results:
        s = r.get("status", "broken")
        if s in out:
            out[s] += 1
    return out
