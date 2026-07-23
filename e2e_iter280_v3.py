"""
Iter 280 E2E v3 — final regression on PRODUCTION (auremcto.com)

Fixes vs v2:
  - Correct API paths: /api/aurem-dev/chat/* and /api/aurem-dev/loop/*
  - Explicitly click [data-testid="loop-mode-toggle"] to enter LOOP mode
  - Persistence tested BOTH via API (source of truth) and UI count
"""
import asyncio, json, time
from pathlib import Path
from playwright.async_api import async_playwright

PROD  = "https://auremcto.com"
API   = "/api/aurem-dev"
EMAIL = "teji.ss1986@gmail.com"
PASSWORD = "Singh1986$"
OUT   = Path("/app/e2e_iter280_v3_report.json")
LOGDIR = Path("/app/e2e_iter280_v3_logs"); LOGDIR.mkdir(exist_ok=True)

report = {"steps": [], "loop_sse": [], "console_errors": [], "network_err": [], "sse_urls": []}

def log(step, ok, detail=""):
    entry = {"step": step, "ok": ok, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    report["steps"].append(entry)
    print(f"[{'PASS' if ok else 'FAIL'}] {step} :: {detail}")

async def wait_send_ready(page, timeout=90):
    """Wait until chat-send button is present and chat-stop is gone."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            send_c = await page.locator('[data-testid="chat-send"]').count()
            stop_c = await page.locator('[data-testid="chat-stop"]').count()
            if send_c and not stop_c:
                return True
        except Exception: pass
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
            if msg.type == "error" and "warning" not in t.lower():
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

        # ─── Login ───────────────────────────────────────────────────
        await page.goto(f"{PROD}/login", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
        await page.fill('[data-testid="login-email"]', EMAIL)
        await page.fill('[data-testid="login-password"]', PASSWORD)
        await page.click('[data-testid="login-submit"]')
        await page.wait_for_url(lambda u: "/login" not in u, timeout=25000)
        log("login", True, page.url)

        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=25000)
        await page.wait_for_timeout(2000)

        # Dismiss cookie
        try:
            if await page.locator('[data-testid="cookie-accept-btn"]').count() > 0:
                await page.click('[data-testid="cookie-accept-btn"]')
                await page.wait_for_timeout(1000)
                log("cookie:dismissed", True)
        except Exception as e:
            log("cookie:dismissed", False, str(e)[:150])

        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(LOGDIR/"01_login.png"))

        # Grab session_id
        sess = await page.evaluate("""
            () => {
                for (const k of Object.keys(localStorage)) {
                    if (k.startsWith('aurem_session_')) return localStorage.getItem(k);
                }
                return null;
            }
        """)
        report["session_id"] = sess

        # ─── History baseline via API ───────────────────────────────
        base = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, count: (j.messages || []).length};
            }
        """, sess)
        log("history:api-baseline", base.get("status")==200, f"session={sess} count={base.get('count')}")

        # ─── Send probe chat (default mode) ─────────────────────────
        probe = f"E2E ping v3 iter280 {int(time.time())}"
        await page.locator('[data-testid="chat-input"]').click(force=True)
        await page.locator('[data-testid="chat-input"]').fill(probe)
        await page.wait_for_timeout(400)
        await page.locator('[data-testid="chat-send"]').click(force=True)
        finished = await wait_send_ready(page, timeout=90)
        log("chat:probe-sent-and-streamed", finished, f"streaming_completed={finished}")
        await page.wait_for_timeout(3000)

        after_probe = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, count: (j.messages || []).length, last: (j.messages||[]).slice(-1)[0]};
            }
        """, sess)
        persisted = (after_probe.get("count") or 0) > (base.get("count") or 0)
        log("Iter280-FIX2:probe-persisted-to-server", persisted,
            f"before={base.get('count')} after={after_probe.get('count')} last_role={(after_probe.get('last') or {}).get('role')}")

        # ─── Reload → verify history still there ─────────────────────
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=20000)
        await page.wait_for_timeout(6000)
        after_reload = await page.evaluate("""
            async (sid) => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/history?session_id=' + sid, {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, count: (j.messages || []).length};
            }
        """, sess)
        # Also check what UI actually rendered — count MessageBubble elements
        try:
            ui_bubbles = await page.evaluate("""
                () => document.querySelectorAll('[data-testid^="council-recall-caption-"], [data-testid="chat-messages"] > * > div').length
            """)
        except: ui_bubbles = -1
        stable = after_reload.get("count") == after_probe.get("count")
        log("Iter280-FIX2:history-stable-after-reload", stable,
            f"before-reload={after_probe.get('count')} after-reload={after_reload.get('count')} ui_bubbles={ui_bubbles}")

        await page.screenshot(path=str(LOGDIR/"02_after_reload.png"))

        # ─── ENABLE LOOP MODE ────────────────────────────────────────
        loop_toggle_found = False
        try:
            if await page.locator('[data-testid="loop-mode-toggle"]').count() > 0:
                await page.locator('[data-testid="loop-mode-toggle"]').click(force=True)
                loop_toggle_found = True
                log("loop-mode-toggle:click", True)
                await page.wait_for_timeout(1500)
            elif await page.locator('[data-testid="loop-mode-toggle-locked"]').count() > 0:
                log("loop-mode-toggle:click", False, "toggle is LOCKED (paywall/gate)")
            else:
                log("loop-mode-toggle:click", False, "toggle not found in DOM")
        except Exception as e:
            log("loop-mode-toggle:click", False, str(e)[:200])

        await page.screenshot(path=str(LOGDIR/"03_loop_mode_enabled.png"))

        # ─── SEND loop-triggering prompt ─────────────────────────────
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
            await page.screenshot(path=str(LOGDIR/"04_prompt_fail.png"))

        # ─── Watch for [loop-sse], LoopLiveFeed, active loop (3 min) ─
        feed_seen = False; loop_id_seen = None; deadline = time.time() + 180
        active_probes = 0
        while time.time() < deadline:
            await page.wait_for_timeout(4000)
            try:
                if await page.locator('[data-testid="loop-live-feed"]').count() > 0:
                    feed_seen = True
            except: pass
            active = await page.evaluate("""
                async () => {
                    const t = localStorage.getItem('aurem_token');
                    const r = await fetch('/api/aurem-dev/loop/active', {
                        headers: {'Authorization': 'Bearer ' + t}
                    });
                    const j = await r.json();
                    return {status: r.status, body: JSON.stringify(j).slice(0, 400)};
                }
            """)
            active_probes += 1
            if "loop_id" in (active.get("body") or "") or "\"active\":{" in (active.get("body") or ""):
                # Try to extract loop_id
                import re as _re
                m = _re.search(r'"loop_id"\s*:\s*"([^"]+)"', active.get("body", ""))
                if m:
                    loop_id_seen = m.group(1)
            if feed_seen and (report["loop_sse"] or loop_id_seen):
                break

        log("loop:live-feed-visible", feed_seen, f"feed={feed_seen}")
        log("loop:loop-sse-console-count", len(report["loop_sse"]) > 0, f"count={len(report['loop_sse'])}")
        log("loop:backend-active-shows-loop", loop_id_seen is not None,
            f"loop_id={loop_id_seen} probes={active_probes}")
        await page.screenshot(path=str(LOGDIR/"05_loop_mid.png"))

        # ─── chat-input enabled during loop (Iter 280 P0 #1) ─────────
        try:
            dis = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-during-loop", not dis, f"disabled={dis}")
        except Exception as e:
            log("Iter280-FIX1:chat-input-enabled-during-loop", False, str(e)[:200])

        # ─── STOP loop via chat-stop button, else API cancel ─────────
        stopped = False
        try:
            if await page.locator('[data-testid="chat-stop"]').count() > 0:
                await page.locator('[data-testid="chat-stop"]').click(force=True)
                log("loop:stop-via-button", True)
                stopped = True
            else:
                log("loop:stop-via-button", False, "chat-stop button not visible")
        except Exception as e:
            log("loop:stop-via-button", False, str(e)[:200])

        if not stopped and loop_id_seen:
            cancel = await page.evaluate(f"""
                async () => {{
                    const t = localStorage.getItem('aurem_token');
                    const r = await fetch('/api/aurem-dev/loop/{loop_id_seen}/cancel', {{
                        method: 'POST',
                        headers: {{'Authorization': 'Bearer ' + t}}
                    }});
                    return {{status: r.status}};
                }}
            """)
            log("loop:stop-via-api-fallback", cancel.get("status", 0) < 400, str(cancel))

        await page.wait_for_timeout(6000)

        # ─── Verify backend loop is gone ─────────────────────────────
        final_active = await page.evaluate("""
            async () => {
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/loop/active', {
                    headers: {'Authorization': 'Bearer ' + t}
                });
                const j = await r.json();
                return {status: r.status, body: JSON.stringify(j).slice(0, 400)};
            }
        """)
        no_loop = "loop_id" not in (final_active.get("body") or "") \
                  and "\"active\":null" in (final_active.get("body") or "")
        log("Iter279:backend-loop-cancelled", no_loop, str(final_active)[:200])
        await page.screenshot(path=str(LOGDIR/"06_after_stop.png"))

        # ─── Final: chat-input enabled after stop ────────────────────
        try:
            dis = await page.locator('[data-testid="chat-input"]').is_disabled()
            log("Iter280-FIX1:chat-input-enabled-after-stop", not dis, f"disabled={dis}")
        except Exception as e:
            log("Iter280-FIX1:chat-input-enabled-after-stop", False, str(e)[:200])

        # ─── Final reload → verify chat & loop turns still persist ────
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
                return {count: (j.messages || []).length};
            }
        """, sess)
        log("history:final-reload-persisted", (final_hist.get("count") or 0) >= (after_probe.get("count") or 0),
            f"final={final_hist.get('count')} after_probe={after_probe.get('count')}")
        await page.screenshot(path=str(LOGDIR/"07_final_reload.png"))

        # ─── Save ────────────────────────────────────────────────────
        report["loop_sse_sample"] = report["loop_sse"][:15]
        OUT.write_text(json.dumps(report, indent=2, default=str))
        passed = sum(1 for s in report["steps"] if s["ok"])
        print(f"\n=== SUMMARY ===  {passed}/{len(report['steps'])} passed")
        print(f"loop_sse traces: {len(report['loop_sse'])}   sse_urls: {len(report['sse_urls'])}   4xx/5xx api: {len(report['network_err'])}")
        for s in report["steps"]:
            print(f"  {'✅' if s['ok'] else '❌'} {s['step']} — {s['detail']}")
        await browser.close()

asyncio.run(main())
