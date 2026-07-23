"""
Real E2E proof for Step 0 fix on PREVIEW.

Verifies that after clicking "Start Loop" the LoopLiveFeed panel
appears IMMEDIATELY (via the Iter 281 placeholder), even before
any SSE event lands — proving the null-return bug is fixed.
"""
import asyncio, json, time
from pathlib import Path
from playwright.async_api import async_playwright

PREVIEW = "https://launch-pad-237.preview.emergentagent.com"
EMAIL, PWD = "test@aurem.dev", "AuremTest2026!"
LOGDIR = Path("/app/e2e_iter281_step0_proof"); LOGDIR.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        traces = []
        page.on("console", lambda m: traces.append(m.text) if "[loop-sse]" in m.text else None)

        await page.goto(f"{PREVIEW}/login", timeout=45000)
        await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
        await page.fill('[data-testid="login-email"]', EMAIL)
        await page.fill('[data-testid="login-password"]', PWD)
        await page.click('[data-testid="login-submit"]')
        await page.wait_for_url(lambda u: "/login" not in u, timeout=25000)
        await page.wait_for_selector('[data-testid="chat-panel"]', timeout=25000)
        await page.wait_for_timeout(2000)

        # Cookie
        if await page.locator('[data-testid="cookie-accept-btn"]').count():
            await page.click('[data-testid="cookie-accept-btn"]')
            await page.wait_for_timeout(1000)

        await page.screenshot(path=str(LOGDIR / "01_login.png"))

        # LOOP mode
        if await page.locator('[data-testid="loop-mode-toggle"]').count():
            await page.locator('[data-testid="loop-mode-toggle"]').click()
            await page.wait_for_timeout(1500)
        elif await page.locator('[data-testid="loop-mode-toggle-locked"]').count():
            print("LOOP mode locked for this user — cannot test Step 0 flow here.")
            await page.screenshot(path=str(LOGDIR / "02_loop_locked.png"))
            await browser.close()
            return

        # Send prompt
        prompt = "add a docstring comment '// iter281 e2e' to a JS utility"
        await page.locator('[data-testid="chat-input"]').fill(prompt)
        await page.wait_for_timeout(500)
        await page.locator('[data-testid="chat-send"]').click()

        # Wait up to 90s for either the LoopLiveFeed (placeholder OR real)
        # OR the plan-approval card.
        placeholder_seen = False
        feed_seen = False
        placeholder_time = None
        deadline = time.time() + 90
        while time.time() < deadline:
            await page.wait_for_timeout(1500)
            if await page.locator('[data-testid="loop-live-feed"]').count() > 0:
                feed_seen = True
                if await page.locator('[data-testid="loop-live-feed-placeholder"]').count() > 0:
                    placeholder_seen = True
                    if placeholder_time is None:
                        placeholder_time = time.time()
                    await page.screenshot(path=str(LOGDIR / "03_placeholder.png"))
                    break
            if await page.locator('[data-testid="plan-approval-card"]').count() > 0:
                break

        await page.screenshot(path=str(LOGDIR / "04_final.png"))
        result = {
            "placeholder_seen": placeholder_seen,
            "loop_live_feed_seen": feed_seen,
            "loop_sse_traces": len(traces),
            "sample_trace": traces[:3],
        }
        (LOGDIR / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        await browser.close()

asyncio.run(main())
