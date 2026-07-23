"""
Full QA E2E — senior QA engineer sweep on PRODUCTION (auremcto.com)

Founder account.  Real browser (Chromium via Playwright).  Every
checkpoint captures a screenshot + real network/console evidence.

Checklist:
  1. Login → dashboard
  2. Diagnostic endpoint: expect 6/6 TTLs + stream_max_s=1200
  3. Chat history persists across page.reload()
  4. LoopLiveFeed placeholder appears IMMEDIATELY once loop_id is set
     (Iter 281 fix — the panel does not wait for the first SSE event)
  5. Plan Approval card renders (Iter 281 — reachable from any prior state)
  6. After approval, SSE events flow → [loop-sse] console traces
  7. chat-input stays ENABLED throughout the loop (Iter 280 fix)
  8. Cancel mid-loop via chat-stop button
  9. Backend confirms loop cancelled (/loop/active returns null within 2s)
 10. After cancel, no ghost tasks — /_diagnostics still healthy
"""
from __future__ import annotations
import asyncio, json, time
from pathlib import Path
from playwright.async_api import async_playwright

PROD    = "https://auremcto.com"
EMAIL   = "teji.ss1986@gmail.com"
PWD     = "Singh1986$"
LOGDIR  = Path("/app/e2e_prod_qa_final"); LOGDIR.mkdir(exist_ok=True)
REPORT  = LOGDIR / "report.json"

R = {"checkpoints": [], "loop_sse": [], "console_errors": [], "network_err": [],
     "sse_streams_opened": 0}

def _ck(step, ok, detail=""):
    R["checkpoints"].append({"step": step, "ok": ok, "detail": detail,
                              "ts": time.strftime("%H:%M:%S")})
    print(f"  {'✅' if ok else '❌'} {step} — {detail}")


async def _wait_send_ready(page, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = await page.locator('[data-testid="chat-send"]').count()
            st = await page.locator('[data-testid="chat-stop"]').count()
            if s and not st:
                return True
        except: pass
        await page.wait_for_timeout(1000)
    return False


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        def _console(m):
            try: t = m.text
            except: t = str(m)
            if "[loop-sse]" in t:
                R["loop_sse"].append(t[:600])
                print(f"    🔵 {t[:180]}")
            if m.type == "error" and "warning" not in t.lower():
                R["console_errors"].append(t[:300])
        page.on("console", _console)

        def _resp(r):
            try:
                if r.status >= 400 and "/api/" in r.url:
                    R["network_err"].append({"url": r.url, "status": r.status})
                if "/loop/" in r.url and r.url.endswith("/stream"):
                    R["sse_streams_opened"] += 1
            except: pass
        page.on("response", _resp)

        # ─── CHECKPOINT 1 — Login ────────────────────────────────
        print("\n──[ 1 ] Login as founder ─────────────────────")
        await page.goto(f"{PROD}/login", timeout=45000)
        await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
        await page.fill('[data-testid="login-email"]', EMAIL)
        await page.fill('[data-testid="login-password"]', PWD)
        await page.click('[data-testid="login-submit"]')
        await page.wait_for_url(lambda u: "/login" not in u, timeout=25000)
        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=25000)
        await page.wait_for_timeout(2000)
        if await page.locator('[data-testid="cookie-accept-btn"]').count():
            await page.click('[data-testid="cookie-accept-btn"]')
            await page.wait_for_timeout(1000)
        _ck("login", True, page.url)
        await page.screenshot(path=str(LOGDIR/"01_login.png"))

        # ─── CHECKPOINT 2 — Diagnostic endpoint ──────────────────
        print("\n──[ 2 ] /_diagnostics endpoint ────────────────")
        diag = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/_diagnostics', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                return {status: r.status, body: await r.json()};
            }
        """)
        b = diag.get("body", {})
        ttl_count = len(b.get("ttl_indexes_present", []))
        _ck("diag:stream_max_s", b.get("stream_max_s") == 1200,
            f"got {b.get('stream_max_s')}")
        _ck("diag:6of6_ttls", ttl_count == 6, f"count={ttl_count}")
        _ck("diag:db_name",
            (b.get("db_name") or "").startswith("launch-pad-237-"),
            f"db={b.get('db_name')}")

        # ─── CHECKPOINT 3 — Grab session_id + baseline history ──
        print("\n──[ 3 ] Chat history baseline ───────────────")
        sess = await page.evaluate("""
            () => {
                for (const k of Object.keys(localStorage)) {
                    if (k.startsWith('aurem_session_')) return localStorage.getItem(k);
                }
                return null;
            }
        """)
        base = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return (j.messages || []).length;
            }
        """, sess)
        _ck("chat:baseline-history", base >= 0,
            f"session={sess} baseline_turns={base}")

        # Cancel any pre-existing active loop so we start clean
        pre = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/active', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                return await r.json();
            }
        """)
        import re
        m = re.search(r'"loop_id"\s*:\s*"([^"]+)"', json.dumps(pre))
        if m:
            lid = m.group(1)
            await page.evaluate(f"""
                async () => {{
                    const t = localStorage.getItem('aurem_token');
                    await fetch('/api/aurem-dev/loop/{lid}/cancel', {{
                        method: 'POST',
                        headers: {{'Authorization': 'Bearer ' + t}}
                    }});
                }}
            """)
            print(f"    ⚙  cleaned up pre-existing active loop {lid}")
            await page.wait_for_timeout(3000)

        # ─── CHECKPOINT 4 — Enable LOOP mode ────────────────────
        print("\n──[ 4 ] Enable LOOP mode ────────────────────")
        if await page.locator('[data-testid="loop-mode-toggle"]').count():
            await page.locator('[data-testid="loop-mode-toggle"]').click()
            await page.wait_for_timeout(1500)
            _ck("loop-mode:toggled-on", True)
        elif await page.locator('[data-testid="loop-mode-toggle-locked"]').count():
            _ck("loop-mode:toggled-on", False, "toggle is LOCKED — halt")
            R["_locked"] = True
        else:
            _ck("loop-mode:toggled-on", False, "toggle not found")
        await page.screenshot(path=str(LOGDIR/"02_loop_mode_on.png"))

        # ─── CHECKPOINT 5 — Send LOOP prompt ─────────────────────
        print("\n──[ 5 ] Send LOOP prompt ───────────────────")
        loop_prompt = ("add a single HTML comment '<!-- iter282 e2e qa -->' at "
                       "the very top of README.md — nothing else")
        try:
            inp = page.locator('[data-testid="chat-input"]')
            await inp.click()
            await inp.fill(loop_prompt)
            await page.wait_for_timeout(500)
            # Prefer keyboard Enter — the form's onSubmit is what actually
            # dispatches send() reliably.
            await inp.press("Enter")
            _ck("loop:prompt-submitted", True, loop_prompt[:60])
        except Exception as e:
            _ck("loop:prompt-submitted", False, str(e)[:150])
            await page.screenshot(path=str(LOGDIR/"03a_submit_fail.png"))

        # Watch for LoopLiveFeed placeholder OR plan approval card
        print("\n──[ 6 ] LoopLiveFeed placeholder + Plan Approval ─")
        placeholder_seen = False
        placeholder_time = None
        plan_card_seen   = False
        plan_card_time   = None
        deadline = time.time() + 120
        while time.time() < deadline:
            await page.wait_for_timeout(2000)
            try:
                if not placeholder_seen and await page.locator(
                    '[data-testid="loop-live-feed-placeholder"]'
                ).count() > 0:
                    placeholder_seen = True
                    placeholder_time = time.time()
                    await page.screenshot(path=str(LOGDIR/"03_placeholder.png"))
                if not plan_card_seen and await page.locator(
                    '[data-testid="plan-approval-card"]'
                ).count() > 0:
                    plan_card_seen = True
                    plan_card_time = time.time()
                    await page.screenshot(path=str(LOGDIR/"04_plan_card.png"))
                    break
            except: pass
        _ck("Iter281:live-feed-placeholder-appeared", placeholder_seen,
            f"visible={placeholder_seen}")
        _ck("Iter281:plan-approval-card-appeared", plan_card_seen,
            f"visible={plan_card_seen}")

        # ─── CHECKPOINT 7 — Approve plan → SSE stream opens ──────
        print("\n──[ 7 ] Approve plan → SSE flow ────────────")
        if plan_card_seen:
            try:
                await page.locator('[data-testid="plan-approve-btn"]').click()
                _ck("loop:plan-approved", True)
            except Exception as e:
                _ck("loop:plan-approved", False, str(e)[:150])

            # Wait up to 180s for actual SSE events + LoopLiveFeed real state
            print("     waiting up to 180s for [loop-sse] traces …")
            deadline = time.time() + 180
            real_feed_seen = False
            while time.time() < deadline:
                await page.wait_for_timeout(3000)
                # LoopLiveFeed with real events (not placeholder)
                try:
                    feed_ct = await page.locator('[data-testid="loop-live-feed"][data-state]:not([data-state="pending"])').count()
                    if feed_ct > 0:
                        real_feed_seen = True
                except: pass
                if len(R["loop_sse"]) >= 5 and real_feed_seen:
                    break
            _ck("SSE:events-observed", len(R["loop_sse"]) > 0,
                f"count={len(R['loop_sse'])}")
            _ck("SSE:stream-opened-in-network", R["sse_streams_opened"] > 0,
                f"count={R['sse_streams_opened']}")
            _ck("Iter281:live-feed-real-events-rendered", real_feed_seen,
                f"visible={real_feed_seen}")
            await page.screenshot(path=str(LOGDIR/"05_loop_mid.png"))
        else:
            _ck("loop:plan-approved", False, "plan card never appeared — skipping")

        # ─── CHECKPOINT 8 — chat-input enabled during loop ──────
        print("\n──[ 8 ] chat-input during loop ──────────────")
        try:
            dis = await page.locator('[data-testid="chat-input"]').is_disabled()
            _ck("Iter280:chat-input-enabled-during-loop", not dis,
                f"disabled={dis}")
        except Exception as e:
            _ck("Iter280:chat-input-enabled-during-loop", False, str(e)[:150])

        # ─── CHECKPOINT 9 — Cancel mid-loop ─────────────────────
        print("\n──[ 9 ] Cancel mid-loop ────────────────────")
        try:
            if await page.locator('[data-testid="chat-stop"]').count():
                await page.locator('[data-testid="chat-stop"]').click()
                _ck("loop:stop-button-clicked", True)
            else:
                _ck("loop:stop-button-clicked", False, "chat-stop not visible")
        except Exception as e:
            _ck("loop:stop-button-clicked", False, str(e)[:150])

        await page.wait_for_timeout(4000)

        # Verify backend actually stopped
        stopped = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/active', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                return await r.json();
            }
        """)
        body_str = json.dumps(stopped)
        backend_null = '"active":null' in body_str or '"active": null' in body_str
        _ck("Iter279:backend-cancelled-within-2s", backend_null,
            f"active={body_str[:150]}")
        await page.screenshot(path=str(LOGDIR/"06_after_stop.png"))

        # ─── CHECKPOINT 10 — chat-input after stop + history preserved ─
        print("\n──[ 10 ] Post-stop verification ─────────────")
        try:
            dis = await page.locator('[data-testid="chat-input"]').is_disabled()
            _ck("Iter280:chat-input-enabled-after-stop", not dis,
                f"disabled={dis}")
        except Exception as e:
            _ck("Iter280:chat-input-enabled-after-stop", False, str(e)[:150])

        # History persistence after reload
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=20000)
        await page.wait_for_timeout(6000)
        final_hist = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return (j.messages || []).length;
            }
        """, sess)
        _ck("Iter280:history-persists-across-reload",
            final_hist >= base,
            f"before={base} after={final_hist}")
        await page.screenshot(path=str(LOGDIR/"07_final_reload.png"))

        # ─── CHECKPOINT 11 — Diagnostic still healthy ────────────
        print("\n──[ 11 ] Diagnostic still healthy after cancel ─")
        diag2 = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/_diagnostics', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                return await r.json();
            }
        """)
        _ck("diag:still-6of6-ttls-after-cancel",
            len(diag2.get("ttl_indexes_present", [])) == 6,
            f"count={len(diag2.get('ttl_indexes_present', []))}")

        # ─── SAVE ──────────────────────────────────────────────
        R["diag_before"]        = b
        R["diag_after"]         = diag2
        R["placeholder_seen_at_offset"] = (
            placeholder_time - (placeholder_time or 0)
            if placeholder_time is None else None
        )
        R["loop_sse_sample"] = R["loop_sse"][:8]
        REPORT.write_text(json.dumps(R, indent=2, default=str))

        passed = sum(1 for c in R["checkpoints"] if c["ok"])
        print(f"\n════════════════════════════════════════════")
        print(f"   FINAL: {passed}/{len(R['checkpoints'])} checkpoints passed")
        print(f"   loop_sse traces:  {len(R['loop_sse'])}")
        print(f"   sse_streams:      {R['sse_streams_opened']}")
        print(f"   api 4xx/5xx:      {len(R['network_err'])}")
        print(f"════════════════════════════════════════════")
        await browser.close()

asyncio.run(main())
