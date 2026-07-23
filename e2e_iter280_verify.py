"""
Iter 280 Full Regression on PRODUCTION (auremcto.com)
- Login as Founder
- Check chat history persistence on refresh (Iter 280 Fix #2)
- Start a Loop and monitor:
    - [loop-sse] console traces  (Iter 280 Debug)
    - LoopLiveFeed rendering
    - chat-input NOT disabled during loop  (Iter 280 Fix #1)
    - Loop stop actually cancels backend (Iter 279)
    - Heartbeat frames (Iter 278)
"""
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright

PROD_URL = "https://auremcto.com"
EMAIL = "teji.ss1986@gmail.com"
PASSWORD = "Singh1986$"
OUT = Path("/app/e2e_iter280_report.json")
LOGDIR = Path("/app/e2e_iter280_logs")
LOGDIR.mkdir(exist_ok=True)

report = {"steps": [], "console_logs": [], "loop_sse_traces": [], "network_errors": []}

def log(step, ok, detail=""):
    entry = {"step": step, "ok": ok, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    report["steps"].append(entry)
    print(f"[{'PASS' if ok else 'FAIL'}] {step} :: {detail}")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        # ── console log capture ────────────────────────────────────────
        def on_console(msg):
            try:
                text = msg.text
            except Exception:
                text = str(msg)
            report["console_logs"].append({"type": msg.type, "text": text[:400]})
            if "[loop-sse]" in text:
                report["loop_sse_traces"].append(text[:600])
        page.on("console", on_console)

        def on_response(resp):
            try:
                if resp.status >= 400 and "/api/" in resp.url:
                    report["network_errors"].append({"url": resp.url, "status": resp.status})
            except Exception:
                pass
        page.on("response", on_response)

        # ── STEP 1: Login ───────────────────────────────────────────────
        try:
            await page.goto(f"{PROD_URL}/login", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
            await page.fill('[data-testid="login-email"]', EMAIL)
            await page.fill('[data-testid="login-password"]', PASSWORD)
            await page.click('[data-testid="login-submit"]')
            # Wait for chat root OR 2FA
            await page.wait_for_url(lambda url: "/login" not in url, timeout=25000)
            log("login", True, f"landed on {page.url}")
        except Exception as e:
            log("login", False, str(e)[:200])
            await page.screenshot(path=str(LOGDIR / "01_login_fail.png"))
            await browser.close()
            OUT.write_text(json.dumps(report, indent=2))
            return

        # Wait for chat panel
        try:
            await page.wait_for_selector('[data-testid="chat-panel"]', timeout=25000)
            log("chat-panel visible", True)
        except Exception as e:
            log("chat-panel visible", False, str(e)[:200])
            await page.screenshot(path=str(LOGDIR / "02_chat_not_visible.png"), full_page=False)

        await page.screenshot(path=str(LOGDIR / "03_after_login.png"))

        # ── STEP 2: History Persistence — count messages ─────────────
        await page.wait_for_timeout(3000)
        try:
            initial_msgs = await page.locator('[data-testid="chat-messages"] > *').count()
        except Exception:
            initial_msgs = -1
        log("history:initial-message-count", initial_msgs >= 0, f"count={initial_msgs}")

        # ── STEP 3: Send a small chat message to seed history ─────────
        try:
            await page.wait_for_selector('[data-testid="chat-input"]', timeout=10000)
            disabled_before = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("chat-input:enabled-before-loop", not disabled_before, f"disabled={disabled_before}")
            probe_text = f"E2E probe iter280 {int(time.time())}"
            await page.fill('[data-testid="chat-input"]', probe_text)
            await page.wait_for_timeout(500)
            await page.click('[data-testid="chat-send"]')
            log("chat:send-probe", True, probe_text)
        except Exception as e:
            log("chat:send-probe", False, str(e)[:200])

        # Wait for assistant reply / message to render
        await page.wait_for_timeout(8000)
        await page.screenshot(path=str(LOGDIR / "04_probe_sent.png"))
        try:
            mid_count = await page.locator('[data-testid="chat-messages"] > *').count()
        except Exception:
            mid_count = -1
        log("history:after-probe-count", mid_count > initial_msgs, f"before={initial_msgs} after={mid_count}")

        # ── STEP 4: Refresh page → chat history should persist ────────
        await page.reload(wait_until="domcontentloaded")
        try:
            await page.wait_for_selector('[data-testid="chat-messages"]', timeout=20000)
            await page.wait_for_timeout(5000)
            after_reload = await page.locator('[data-testid="chat-messages"] > *').count()
            persisted = after_reload >= max(mid_count, 1)
            log("history:persists-after-reload", persisted, f"before-reload={mid_count} after-reload={after_reload}")
        except Exception as e:
            log("history:persists-after-reload", False, str(e)[:200])
        await page.screenshot(path=str(LOGDIR / "05_after_reload.png"))

        # ── STEP 5: Start a LOOP (this triggers loop_mode) ────────────
        # Loop mode is triggered by keywords like "build", "add feature".
        # We'll send a very small, low-risk instruction.
        loop_prompt = "Add a single-line HTML comment '<!-- iter280 e2e probe -->' to the top of README.md if it exists. Do nothing else."
        try:
            input_el = page.locator('[data-testid="chat-input"]')
            await input_el.click()
            await input_el.fill(loop_prompt)
            await page.wait_for_timeout(500)
            await page.click('[data-testid="chat-send"]')
            log("loop:send-prompt", True, loop_prompt[:80])
        except Exception as e:
            log("loop:send-prompt", False, str(e)[:200])

        # ── STEP 6: Watch for loop start + LoopLiveFeed within 60s ────
        loop_started = False
        live_feed_seen = False
        deadline = time.time() + 90
        while time.time() < deadline:
            await page.wait_for_timeout(2000)
            try:
                if await page.locator('[data-testid="loop-live-feed"]').count() > 0:
                    live_feed_seen = True
                    loop_started = True
                    break
            except Exception:
                pass
            # also check for [loop-sse] traces
            if report["loop_sse_traces"]:
                loop_started = True
        log("loop:started-and-live-feed-visible", live_feed_seen,
            f"sse_traces={len(report['loop_sse_traces'])} feed_visible={live_feed_seen}")
        await page.screenshot(path=str(LOGDIR / "06_loop_started.png"))

        # ── STEP 7: chat-input must be ENABLED during loop (Iter 280 P0) ─
        try:
            disabled_during = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-during-loop", not disabled_during,
                f"disabled_during_loop={disabled_during}")
        except Exception as e:
            log("Iter280-FIX1:chat-input-enabled-during-loop", False, str(e)[:200])

        # ── STEP 8: Wait for heartbeat / phase events (up to 90s more)
        phase_events_seen = 0
        heartbeat_seen = False
        deadline2 = time.time() + 90
        while time.time() < deadline2:
            await page.wait_for_timeout(3000)
            phase_events_seen = len(report["loop_sse_traces"])
            hb = any("heartbeat" in t.lower() or "keepalive" in t.lower() for t in report["loop_sse_traces"])
            if hb:
                heartbeat_seen = True
            if phase_events_seen >= 6:
                break
        log("loop:sse-phase-events-count", phase_events_seen >= 2,
            f"count={phase_events_seen}")
        log("loop:heartbeat-visible", heartbeat_seen,
            f"heartbeat_found={heartbeat_seen}")
        await page.screenshot(path=str(LOGDIR / "07_loop_mid.png"))

        # ── STEP 9: STOP the loop (Iter 279 cancel) ────────────────────
        try:
            stop_btn = page.locator('[data-testid="chat-stop"]')
            if await stop_btn.count() > 0 and await stop_btn.is_visible():
                await stop_btn.click()
                log("loop:stop-click", True)
            else:
                log("loop:stop-click", False, "chat-stop not visible")
        except Exception as e:
            log("loop:stop-click", False, str(e)[:200])

        # Wait for terminal frame
        await page.wait_for_timeout(8000)
        await page.screenshot(path=str(LOGDIR / "08_after_stop.png"))
        try:
            # LoopLiveFeed should transition to terminal (still visible OR gone)
            feed_after = await page.locator('[data-testid="loop-live-feed"]').count()
            # chat-input must be enabled after stop
            disabled_after = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-after-stop", not disabled_after,
                f"disabled_after_stop={disabled_after}")
            log("loop:live-feed-after-stop", True, f"still_mounted={feed_after > 0}")
        except Exception as e:
            log("loop:live-feed-after-stop", False, str(e)[:200])

        # ── STEP 10: Check backend actually stopped via /api/aurem-dev/loop
        # Grab auth cookies/tokens from context
        try:
            cookies = await ctx.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            # Use fetch inside page for same-origin auth
            api_resp = await page.evaluate("""
                async () => {
                    try {
                        const r = await fetch('/api/aurem-dev/loop/active', {credentials:'include'});
                        const j = await r.json();
                        return {status: r.status, body: j};
                    } catch(e) { return {error: String(e)}; }
                }
            """)
            active_loops = 0
            if isinstance(api_resp.get("body"), dict):
                active_loops = len(api_resp["body"].get("sessions", []))
            elif isinstance(api_resp.get("body"), list):
                active_loops = len(api_resp["body"])
            log("Iter279:backend-loop-cancelled", active_loops == 0,
                f"api_resp={json.dumps(api_resp)[:300]}")
        except Exception as e:
            log("Iter279:backend-loop-cancelled", False, str(e)[:200])

        # ── STEP 11: Final refresh — history should include probe+loop msgs
        await page.reload(wait_until="domcontentloaded")
        try:
            await page.wait_for_selector('[data-testid="chat-messages"]', timeout=20000)
            await page.wait_for_timeout(5000)
            final_count = await page.locator('[data-testid="chat-messages"] > *').count()
            log("history:persists-final-reload", final_count >= 2, f"final_count={final_count}")
        except Exception as e:
            log("history:persists-final-reload", False, str(e)[:200])
        await page.screenshot(path=str(LOGDIR / "09_final.png"))

        # ── Save report ────────────────────────────────────────────────
        # Summary of loop-sse traces (first 10)
        report["loop_sse_traces_sample"] = report["loop_sse_traces"][:15]
        report["console_error_sample"] = [c for c in report["console_logs"] if c["type"] == "error"][:10]
        OUT.write_text(json.dumps(report, indent=2, default=str))
        print("\n=== SUMMARY ===")
        passed = sum(1 for s in report["steps"] if s["ok"])
        print(f"Passed: {passed}/{len(report['steps'])}")
        print(f"loop-sse traces captured: {len(report['loop_sse_traces'])}")
        print(f"api network errors: {len(report['network_errors'])}")
        await browser.close()

asyncio.run(main())
