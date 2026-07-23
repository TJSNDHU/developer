"""
Iter 280 E2E v4 — comprehensive PRODUCTION regression, complete flow.

Adds vs v3: clicks the Plan Approval card so the loop actually opens
its SSE stream. Only then do [loop-sse] traces + LoopLiveFeed appear.
"""
import asyncio, json, time, re
from pathlib import Path
from playwright.async_api import async_playwright

PROD  = "https://auremcto.com"
EMAIL = "teji.ss1986@gmail.com"
PASSWORD = "Singh1986$"
OUT   = Path("/app/e2e_iter280_v4_report.json")
LOGDIR = Path("/app/e2e_iter280_v4_logs"); LOGDIR.mkdir(exist_ok=True)

report = {"steps": [], "loop_sse": [], "console_errors": [], "network_err": [], "sse_urls": []}

def log(step, ok, detail=""):
    entry = {"step": step, "ok": ok, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    report["steps"].append(entry)
    print(f"[{'PASS' if ok else 'FAIL'}] {step} :: {detail}")

async def wait_send_ready(page, timeout=90):
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

        def on_console(msg):
            try: t = msg.text
            except: t = str(msg)
            if "[loop-sse]" in t:
                report["loop_sse"].append(t[:600])
                print(f"  🔵 {t[:180]}")
            if msg.type == "error":
                report["console_errors"].append(t[:300])
        page.on("console", on_console)

        def on_response(r):
            try:
                if r.status >= 400 and "/api/" in r.url:
                    report["network_err"].append({"url": r.url, "status": r.status})
                if "/loop/" in r.url and "stream" in r.url:
                    report["sse_urls"].append({"url": r.url, "status": r.status})
            except: pass
        page.on("response", on_response)

        # Login
        await page.goto(f"{PROD}/login", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
        await page.fill('[data-testid="login-email"]', EMAIL)
        await page.fill('[data-testid="login-password"]', PASSWORD)
        await page.click('[data-testid="login-submit"]')
        await page.wait_for_url(lambda u: "/login" not in u, timeout=25000)
        log("login", True, page.url)
        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=25000)
        await page.wait_for_timeout(2000)

        # Cookie dismiss
        try:
            if await page.locator('[data-testid="cookie-accept-btn"]').count() > 0:
                await page.click('[data-testid="cookie-accept-btn"]')
                await page.wait_for_timeout(1000)
                log("cookie:dismissed", True)
        except: pass

        # If a previous active loop exists on this project, cancel it first.
        pre_active = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/active', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {body: JSON.stringify(j).slice(0, 500)};
            }
        """)
        m = re.search(r'"loop_id"\s*:\s*"([^"]+)"', pre_active.get("body", ""))
        if m:
            lid = m.group(1)
            cancel = await page.evaluate(f"""
                async () => {{
                    const t = localStorage.getItem('aurem_token');
                    const r = await fetch('/api/aurem-dev/loop/{lid}/cancel', {{
                        method:'POST', headers: {{'Authorization': 'Bearer ' + t}}
                    }});
                    return {{status: r.status}};
                }}
            """)
            log("pre-cleanup:cancel-existing-loop", True, f"lid={lid} status={cancel.get('status')}")
            await page.wait_for_timeout(3000)
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_selector('[data-testid="chat-panel"]', timeout=20000)
            await page.wait_for_timeout(3000)

        # Enable LOOP mode
        if await page.locator('[data-testid="loop-mode-toggle"]').count() > 0:
            await page.locator('[data-testid="loop-mode-toggle"]').click(force=True)
            await page.wait_for_timeout(1500)
            log("loop-mode-toggle:enabled", True)
        else:
            log("loop-mode-toggle:enabled", False, "not found")

        await page.screenshot(path=str(LOGDIR/"01_loop_mode_on.png"))

        # Send loop prompt
        loop_prompt = ("add a comment line '<!-- iter280-e2e -->' at the top of the file "
                       "README.md, no other changes")
        try:
            inp = page.locator('[data-testid="chat-input"]')
            await inp.click(force=True)
            await inp.fill(loop_prompt)
            await page.wait_for_timeout(500)
            await page.locator('[data-testid="chat-send"]').click(force=True)
            log("loop:prompt-sent", True, loop_prompt[:60])
        except Exception as e:
            log("loop:prompt-sent", False, str(e)[:200])

        # Wait for Plan Approval Card
        plan_appeared = False
        deadline = time.time() + 120
        while time.time() < deadline:
            await page.wait_for_timeout(3000)
            try:
                if await page.locator('[data-testid="plan-approval-card"]').count() > 0:
                    plan_appeared = True
                    break
            except: pass
        log("loop:plan-approval-card-rendered", plan_appeared, "waited up to 120s")
        await page.screenshot(path=str(LOGDIR/"02_plan_card.png"))

        # Approve plan → this fires openLoopStream()
        if plan_appeared:
            try:
                await page.locator('[data-testid="plan-approve-btn"]').click(force=True)
                log("loop:plan-approved", True)
            except Exception as e:
                log("loop:plan-approved", False, str(e)[:200])

        # Now watch for [loop-sse] traces + LoopLiveFeed panel + phase progression
        feed_seen = False
        first_sse_seen_at = None
        deadline = time.time() + 240  # 4 minutes for loop to progress
        while time.time() < deadline:
            await page.wait_for_timeout(3000)
            try:
                if await page.locator('[data-testid="loop-live-feed"]').count() > 0:
                    feed_seen = True
            except: pass
            if report["loop_sse"] and first_sse_seen_at is None:
                first_sse_seen_at = time.time()
            # Break early once we have >=8 SSE events + feed is visible
            if feed_seen and len(report["loop_sse"]) >= 8:
                break

        log("Iter280-DEBUG:loop-sse-traces-received", len(report["loop_sse"]) > 0,
            f"total={len(report['loop_sse'])}")
        log("loop:live-feed-visible", feed_seen, f"visible={feed_seen}")
        log("loop:sse-stream-opened-network", len(report["sse_urls"]) > 0,
            f"count={len(report['sse_urls'])}")
        await page.screenshot(path=str(LOGDIR/"03_loop_running.png"))

        # chat-input MUST be enabled during loop (Iter 280 P0 #1)
        try:
            dis = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-during-loop", not dis, f"disabled={dis}")
        except Exception as e:
            log("Iter280-FIX1:chat-input-enabled-during-loop", False, str(e)[:200])

        # ── Heartbeat visibility (Iter 278) ──────────────────────────
        hb = any("keepalive=True" in t or "heartbeat" in t.lower() for t in report["loop_sse"])
        log("Iter278:heartbeat-in-sse-events", hb, f"any_keepalive_or_heartbeat={hb}")

        # STOP loop via chat-stop button (Iter 279)
        try:
            if await page.locator('[data-testid="chat-stop"]').count() > 0:
                await page.locator('[data-testid="chat-stop"]').click(force=True)
                log("loop:stop-via-button", True)
            else:
                log("loop:stop-via-button", False, "chat-stop not visible")
        except Exception as e:
            log("loop:stop-via-button", False, str(e)[:200])

        await page.wait_for_timeout(4000)

        # Verify backend cancelled the loop within 2s of stop
        final_active = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/active', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {body: JSON.stringify(j).slice(0,300)};
            }
        """)
        active_null = ('"active":null' in (final_active.get("body") or "")) or \
                      ('"active": null' in (final_active.get("body") or ""))
        log("Iter279:backend-loop-stopped", active_null, str(final_active)[:200])
        await page.screenshot(path=str(LOGDIR/"04_after_stop.png"))

        # chat-input still enabled after stop
        try:
            dis = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-after-stop", not dis, f"disabled={dis}")
        except Exception as e:
            log("Iter280-FIX1:chat-input-enabled-after-stop", False, str(e)[:200])

        # ── SAVE ─────────────────────────────────────────────────────
        report["loop_sse_sample"] = report["loop_sse"][:20]
        OUT.write_text(json.dumps(report, indent=2, default=str))
        passed = sum(1 for s in report["steps"] if s["ok"])
        print(f"\n=== SUMMARY ===  {passed}/{len(report['steps'])} passed")
        print(f"loop_sse traces: {len(report['loop_sse'])}   sse_urls_opened: {len(report['sse_urls'])}")
        for s in report["steps"]:
            print(f"  {'✅' if s['ok'] else '❌'} {s['step']} — {s['detail']}")
        if report["loop_sse"]:
            print("\n--- SSE SAMPLE (first 8) ---")
            for t in report["loop_sse"][:8]:
                print(f"  {t[:220]}")
        await browser.close()

asyncio.run(main())
