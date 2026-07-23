"""
Iter 280 E2E v2 — production regression.
Fixes v1 issues:
  - Wait for assistant streaming to FULLY finish before reload
  - Use `force=True` clicks + JS fallback if UI is obstructed
  - Capture all `[loop-sse]` traces + all SSE-related network events
  - Attempt loop stop via chat-stop button AND via API fallback
"""
import asyncio, json, time, re
from pathlib import Path
from playwright.async_api import async_playwright

PROD = "https://auremcto.com"
EMAIL = "teji.ss1986@gmail.com"
PASSWORD = "Singh1986$"
OUT = Path("/app/e2e_iter280_v2_report.json")
LOGDIR = Path("/app/e2e_iter280_v2_logs")
LOGDIR.mkdir(exist_ok=True)

report = {"steps": [], "console": [], "loop_sse": [], "network": [], "sse_urls": []}

def log(step, ok, detail=""):
    entry = {"step": step, "ok": ok, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    report["steps"].append(entry)
    print(f"[{'PASS' if ok else 'FAIL'}] {step} :: {detail}")

async def wait_streaming_finished(page, timeout=45):
    """Wait until assistant streaming indicator disappears (busy=false)."""
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        try:
            # 'chat-stop' visible means streaming; wait until it disappears or chat-send returns
            send_visible = await page.locator('[data-testid="chat-send"]').count()
            stop_visible = await page.locator('[data-testid="chat-stop"]').count()
            if send_visible and not stop_visible:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(1000)
    return False

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        def on_console(msg):
            try: text = msg.text
            except: text = str(msg)
            report["console"].append({"type": msg.type, "text": text[:400]})
            if "[loop-sse]" in text:
                report["loop_sse"].append(text[:800])
                print(f"  🔵 loop-sse: {text[:200]}")

        page.on("console", on_console)

        def on_request(req):
            if "/loop/" in req.url and "stream" in req.url:
                report["sse_urls"].append({"url": req.url, "method": req.method})

        page.on("request", on_request)

        def on_response(resp):
            try:
                if resp.status >= 400 and "/api/" in resp.url:
                    report["network"].append({"url": resp.url, "status": resp.status})
            except: pass
        page.on("response", on_response)

        # ── Login ─────────────────────────────────────────────────────
        try:
            await page.goto(f"{PROD}/login", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
            await page.fill('[data-testid="login-email"]', EMAIL)
            await page.fill('[data-testid="login-password"]', PASSWORD)
            await page.click('[data-testid="login-submit"]')
            await page.wait_for_url(lambda u: "/login" not in u, timeout=25000)
            log("login", True, f"url={page.url}")
        except Exception as e:
            log("login", False, str(e)[:200]); await browser.close()
            OUT.write_text(json.dumps(report, indent=2, default=str)); return

        # Grab auth token from localStorage for API fallback checks
        token = await page.evaluate("() => localStorage.getItem('token') || localStorage.getItem('auth_token') || localStorage.getItem('aurem_token')")
        report["token_present"] = bool(token)

        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=25000)
        await page.wait_for_timeout(2000)
        # Dismiss cookie consent banner (blocks pointer events)
        try:
            if await page.locator('[data-testid="cookie-accept-btn"]').count() > 0:
                await page.click('[data-testid="cookie-accept-btn"]')
                await page.wait_for_timeout(1000)
                log("cookie-consent:dismissed", True)
        except Exception as e:
            log("cookie-consent:dismissed", False, str(e)[:150])
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(LOGDIR / "01_login.png"))

        # Get initial history via API (source of truth) & sessionId from localStorage
        sess_id = await page.evaluate("""
            () => {
                for (const k of Object.keys(localStorage)) {
                    if (k.startsWith('aurem_session_')) return localStorage.getItem(k);
                }
                return null;
            }
        """)
        report["session_id"] = sess_id

        history_before = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token') || '';
                const r = await fetch('/api/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, count: (j.messages || []).length};
            }
        """, sess_id)
        log("history:api-baseline", history_before.get("count", 0) >= 0,
            f"session={sess_id} count={history_before.get('count')}")

        # ── STEP: send probe chat and let it fully finish streaming ────
        probe = f"E2E ping iter280 {int(time.time())}"
        input_el = page.locator('[data-testid="chat-input"]')
        await input_el.click()
        await input_el.fill(probe)
        await page.wait_for_timeout(400)
        await page.click('[data-testid="chat-send"]')
        log("send:probe-clicked", True, probe)

        finished = await wait_streaming_finished(page, timeout=60)
        log("send:probe-streaming-finished", finished, f"finished={finished}")
        await page.wait_for_timeout(3000)  # give persist_turn time to hit DB
        await page.screenshot(path=str(LOGDIR / "02_probe_done.png"))

        # Check history via API AFTER probe should complete
        history_after = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token') || '';
                const r = await fetch('/api/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, count: (j.messages || []).length, last_role: (j.messages||[]).slice(-1)[0]?.role};
            }
        """, sess_id)
        persisted = history_after.get("count", 0) > history_before.get("count", 0)
        log("Iter280-FIX2:history-persisted-server-side", persisted,
            f"before={history_before.get('count')} after={history_after.get('count')} last_role={history_after.get('last_role')}")

        # ── Reload → history reload check ─────────────────────────────
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=20000)
        await page.wait_for_timeout(6000)  # wait for /chat/history to complete
        history_after_reload = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token') || '';
                const r = await fetch('/api/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, count: (j.messages || []).length};
            }
        """, sess_id)
        # Also check what the UI displays
        try:
            ui_count = await page.locator('[data-testid^="msg-"]').count()
            if ui_count == 0:
                ui_count = await page.locator('[data-testid="chat-messages"] > *').count()
        except: ui_count = -1
        server_stable = history_after_reload.get("count") == history_after.get("count")
        log("Iter280-FIX2:history-reload-server-count-stable", server_stable,
            f"server={history_after_reload.get('count')} ui={ui_count} probe_count={history_after.get('count')}")
        log("Iter280-FIX2:ui-renders-persisted-history", ui_count > 0,
            f"ui_count={ui_count}")
        await page.screenshot(path=str(LOGDIR / "03_after_reload.png"))

        # ── STEP: START A LOOP with force clicks ───────────────────────
        loop_prompt = "please add a single HTML comment line '<!-- iter280 e2e -->' at the very top of README.md if the file exists, nothing else."
        try:
            # Use force + JS to bypass any overlay
            input_el = page.locator('[data-testid="chat-input"]')
            await input_el.scroll_into_view_if_needed()
            await input_el.click(force=True)
            await input_el.fill(loop_prompt)
            await page.wait_for_timeout(500)
            await page.locator('[data-testid="chat-send"]').click(force=True)
            log("loop:prompt-sent", True, loop_prompt[:60])
        except Exception as e:
            log("loop:prompt-sent", False, str(e)[:200])

        # Watch for loop start (LoopLiveFeed panel + [loop-sse] traces) up to 3 min
        loop_seen = False
        feed_seen = False
        deadline = time.time() + 180
        while time.time() < deadline:
            await page.wait_for_timeout(4000)
            try:
                fc = await page.locator('[data-testid="loop-live-feed"]').count()
                if fc > 0:
                    feed_seen = True
                    loop_seen = True
            except: pass
            if report["loop_sse"]:
                loop_seen = True
            # Also check via API if loop is running
            loop_active = await page.evaluate("""
                async () => {
                    const t = localStorage.getItem('aurem_token') || '';
                    try {
                        const r = await fetch('/api/aurem-dev/loop/active', {
                            headers: {'Authorization': 'Bearer ' + t}
                        });
                        const j = await r.json();
                        return {status: r.status, sessions: (j.sessions || j || []).length, body: JSON.stringify(j).slice(0, 300)};
                    } catch(e) { return {error: String(e)}; }
                }
            """)
            report.setdefault("loop_active_probe", []).append(loop_active)
            if loop_active.get("sessions") is not None and loop_active.get("sessions", 0) > 0:
                loop_seen = True
                if feed_seen: break
            if feed_seen and report["loop_sse"]:
                break

        log("loop:LoopLiveFeed-visible", feed_seen, f"feed_count>0={feed_seen}")
        log("loop:loop-sse-console-traces", len(report["loop_sse"]) > 0,
            f"count={len(report['loop_sse'])}")
        log("loop:backend-active-endpoint-showed-loop", loop_seen,
            f"active_probes={len(report.get('loop_active_probe', []))}")
        await page.screenshot(path=str(LOGDIR / "04_loop_mid.png"))

        # ── STEP: chat-input enabled during loop (Iter 280 P0 #1) ──────
        try:
            disabled_during = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-during-loop", not disabled_during,
                f"disabled_during={disabled_during}")
        except Exception as e:
            log("Iter280-FIX1:chat-input-enabled-during-loop", False, str(e)[:200])

        # ── STEP: STOP loop (Iter 279 cancel) ──────────────────────────
        stopped = False
        try:
            stop_btn = page.locator('[data-testid="chat-stop"]')
            if await stop_btn.count() > 0:
                await stop_btn.click(force=True)
                log("loop:stop-click", True)
                stopped = True
            else:
                # Fallback: cancel via API
                cancel_resp = await page.evaluate("""
                    async () => {
                        const t = localStorage.getItem('aurem_token') || '';
                        const ra = await fetch('/api/aurem-dev/loop/active', {
                            headers: {'Authorization': 'Bearer ' + t}
                        });
                        const ja = await ra.json();
                        const sess = (ja.sessions || ja || []);
                        if (!sess.length) return {no_loop: true};
                        const lid = sess[0].loop_id || sess[0].id || sess[0].session_id;
                        const rc = await fetch('/api/aurem-dev/loop/' + lid + '/cancel', {
                            method: 'POST',
                            headers: {'Authorization': 'Bearer ' + t}
                        });
                        return {status: rc.status, lid};
                    }
                """)
                log("loop:stop-via-api", cancel_resp.get("status", 0) < 400,
                    json.dumps(cancel_resp)[:200])
                stopped = cancel_resp.get("status", 999) < 400
        except Exception as e:
            log("loop:stop-click", False, str(e)[:200])

        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(LOGDIR / "05_after_stop.png"))

        # Verify backend loop actually stopped
        final_active = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token') || '';
                const r = await fetch('/api/aurem-dev/loop/active', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, sessions: (j.sessions || j || []).length, body: JSON.stringify(j).slice(0, 300)};
            }
        """)
        log("Iter279:backend-loop-stopped", (final_active.get("sessions") or 0) == 0,
            json.dumps(final_active)[:200])

        # ── FINAL: summarize + save ────────────────────────────────────
        report["loop_sse_sample"] = report["loop_sse"][:20]
        report["console_errors"] = [c for c in report["console"] if c["type"] == "error"][:15]
        OUT.write_text(json.dumps(report, indent=2, default=str))
        passed = sum(1 for s in report["steps"] if s["ok"])
        print(f"\n=== SUMMARY ===  {passed}/{len(report['steps'])} passed")
        print(f"loop_sse traces: {len(report['loop_sse'])}   sse_urls_opened: {len(report['sse_urls'])}")
        for s in report["steps"]:
            print(f"  {'✅' if s['ok'] else '❌'} {s['step']} — {s['detail']}")
        await browser.close()

asyncio.run(main())
