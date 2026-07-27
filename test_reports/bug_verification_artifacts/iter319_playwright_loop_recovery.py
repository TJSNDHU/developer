import asyncio
import json
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


BASE = "https://launch-pad-237.preview.emergentagent.com"
OUT = Path("/app/test_reports/bug_verification_artifacts/iter319_standalone")
OUT.mkdir(parents=True, exist_ok=True)


PLAN = {
    "title": "Test Plan",
    "description": "A simple test plan to verify Iter 312 recovery.",
    "files": [
        {"path": "README.md", "action": "edit", "reason": "Add project overview comment"}
    ],
    "steps": ["Read README", "Add comment at top", "Save"],
}


class Result:
    def __init__(self):
        self.checks = []
        self.requests = []
        self.console = []

    def ok(self, name, details=""):
        print(f"PASS: {name} {details}")
        self.checks.append({"name": name, "passed": True, "details": details})

    def fail(self, name, details=""):
        print(f"FAIL: {name} {details}")
        self.checks.append({"name": name, "passed": False, "details": details})


async def dump(page, label):
    try:
        txt = await page.locator("body").inner_text(timeout=5000)
    except Exception as e:
        txt = f"<body read failed: {e}>"
    (OUT / f"{label}.txt").write_text(txt, encoding="utf-8")
    try:
        await page.screenshot(path=str(OUT / f"{label}.jpg"), quality=40, full_page=False)
    except Exception:
        pass
    print(f"\n--- {label} body excerpt ---\n{txt[-2500:]}\n--- end {label} ---\n")
    return txt


async def body_text(page):
    return await page.locator("body").inner_text(timeout=5000)


async def no_error_selector_dump(page):
    error_text = await page.evaluate(
        """() => {
const errorElements = Array.from(document.querySelectorAll('.error, [class*="error"], [id*="error"]'));
return errorElements.map(el => el.textContent).join(", ");
}"""
    )
    if error_text:
        print(f"Found error message: {error_text}")
    else:
        print("No error messages found on the page")
    return error_text


async def seed_storage(page):
    await page.evaluate(
        """() => {
      const project = {project_id:'p_iter312_mock', name:'Iter312 Mock Repo', github_owner:'mock', github_repo:'iter312', branch:'main', pat_status:'unknown', preview_url:''};
      const raw = localStorage.getItem('aurem_user');
      let u = raw ? JSON.parse(raw) : {};
      u = {...u, email: u.email || 'test@aurem.dev', is_admin: true, is_unlimited: true, tier: u.tier || 'founder'};
      localStorage.setItem('aurem_user', JSON.stringify(u));
      localStorage.setItem('ora_execution_mode', 'loop');
      localStorage.setItem('aurem_active_project', 'p_iter312_mock');
      localStorage.setItem('aurem_projects_cache', JSON.stringify([project]));
      localStorage.setItem('aurem_finish_setup_dismissed', '1');
      sessionStorage.setItem('aurem_finish_setup_dismissed', '1');
    }"""
    )


async def main():
    result = Result()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("console", lambda msg: result.console.append(f"{msg.type}: {msg.text}"))
        page.on("request", lambda req: result.requests.append({"method": req.method, "url": req.url}))

        await page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        await page.get_by_test_id("login-email").fill("test@aurem.dev")
        await page.get_by_test_id("login-password").fill("AuremTest2026!")
        await page.get_by_test_id("login-submit").click()
        try:
            await page.wait_for_url(re.compile(r".*/(dashboard|build)(\?.*)?$"), timeout=25000)
            result.ok("login", page.url)
        except Exception as e:
            result.fail("login", str(e))
            await dump(page, "login_failed")
            await no_error_selector_dump(page)
            raise

        await seed_storage(page)

        async def common_route(route):
            url = route.request.url
            method = route.request.method
            if "/api/aurem-dev/cto/projects/list" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "ok": True,
                    "projects": [{"project_id":"p_iter312_mock", "name":"Iter312 Mock Repo", "github_owner":"mock", "github_repo":"iter312", "branch":"main", "pat_status":"unknown", "preview_url":""}],
                }))
            elif "/api/aurem-dev/chat/sessions" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"sessions": []}))
            elif "/api/aurem-dev/chat/history" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"turns": []} if method == "GET" else {"ok": True}))
            elif "/api/aurem-dev/usage/me" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"remaining": 999999, "is_unlimited": True}))
            elif "/api/aurem-dev/auth/tokens" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"tokens_remaining": 999999}))
            elif "/api/aurem-dev/auth/me" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"user": {"email":"test@aurem.dev", "track":"developer", "is_admin": True, "is_unlimited": True, "tier":"founder"}}))
            elif "/api/health" in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
            elif "/api/aurem-dev/loop/" in url and url.endswith("/cancel"):
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
            else:
                await route.continue_()

        for pat in [
            "**/api/aurem-dev/cto/projects/list**", "**/api/aurem-dev/chat/sessions**",
            "**/api/aurem-dev/chat/history**", "**/api/aurem-dev/usage/me**",
            "**/api/aurem-dev/auth/tokens**", "**/api/aurem-dev/auth/me**",
            "**/api/health**", "**/api/aurem-dev/loop/*/cancel",
        ]:
            await page.route(pat, common_route)

        await page.goto(f"{BASE}/dashboard", wait_until="domcontentloaded")
        await page.wait_for_selector('[data-testid="chat-input"]', timeout=30000)
        try:
            await page.get_by_text("Accept all", exact=True).click(timeout=1500)
        except Exception:
            pass
        await page.get_by_test_id("loop-mode-toggle").wait_for(timeout=15000)
        if await page.get_by_test_id("loop-mode-toggle").get_attribute("data-loop-active") != "1":
            await page.get_by_test_id("loop-mode-toggle").click()
        result.ok("dashboard loaded with loop mode")

        # PLAYWRIGHT-1/2/3/4 recovery path: keep SSE route pending until banner is asserted.
        recovery_release = asyncio.Event()
        recovery_stream_requested = asyncio.Event()
        recovery_active_state = {"state": "planning", "phase": "plan", "plan": None}

        async def start_abort(route):
            await route.abort("timedout")

        async def active_recovery(route):
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            await route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True,
                "active": {
                    "loop_id": "loop_fake_iter312_recovery",
                    "state": recovery_active_state["state"],
                    "phase": recovery_active_state["phase"],
                    "project_id": "p_iter312_mock",
                    "plan": recovery_active_state["plan"],
                    "ship_pending": None,
                    "files_changed": [],
                    "updated_at": now_iso,
                },
            }))

        async def stream_recovery(route):
            recovery_stream_requested.set()
            await recovery_release.wait()
            recovery_active_state.update({"state": "awaiting_confirmation", "phase": "plan", "plan": PLAN})
            ev = {
                "loop_id": "loop_fake_iter312_recovery",
                "state": "awaiting_confirmation",
                "phase": "plan",
                "seq": 1,
                "message": "Plan ready — awaiting your approval.",
                "requires_user_action": True,
                "data": {"plan": PLAN},
            }
            await route.fulfill(status=200, content_type="text/event-stream", headers={"Cache-Control":"no-cache"}, body="data: " + json.dumps(ev) + "\n\n")

        await page.route("**/api/aurem-dev/loop/start", start_abort)
        await page.route("**/api/aurem-dev/loop/active**", active_recovery)
        await page.route("**/api/aurem-dev/loop/loop_fake_iter312_recovery/stream**", stream_recovery)
        await page.get_by_test_id("chat-input").fill("add a comment to README")
        await page.get_by_test_id("chat-send").click()

        try:
            await page.wait_for_function("document.body.innerText.includes('Plan taking longer than expected') || document.body.innerText.includes('loop `')", timeout=10000)
            txt = await body_text(page)
            if "Loop failed to start" in txt:
                result.fail("PLAYWRIGHT-1", "Recovery banner appeared but Loop failed to start also appeared")
            else:
                result.ok("PLAYWRIGHT-1", "Recovery banner/bubble visible and no failure card")
        except Exception as e:
            result.fail("PLAYWRIGHT-1", f"Recovery banner did not appear before SSE release: {e}")
        await dump(page, "recovery_banner_phase")

        try:
            await asyncio.wait_for(recovery_stream_requested.wait(), timeout=10)
            result.ok("PLAYWRIGHT-2", "Browser issued GET /loop/loop_fake_iter312_recovery/stream")
        except Exception as e:
            result.fail("PLAYWRIGHT-2", f"No recovery SSE stream request observed: {e}")

        recovery_release.set()
        await page.wait_for_timeout(1000)
        card_visible = False
        try:
            await page.wait_for_selector('[data-testid="plan-approval-card"]', timeout=15000)
            await page.wait_for_selector('[data-testid="plan-approve-btn"]', timeout=3000)
            await page.wait_for_selector('[data-testid="plan-cancel-btn"]', timeout=3000)
            card_visible = True
            result.ok("PLAYWRIGHT-3 card", "Plan approval card/buttons rendered after recovery SSE")
        except Exception as e:
            result.fail("PLAYWRIGHT-3 card", f"Plan approval card/buttons missing after recovery SSE: {e}")
        txt = await dump(page, "recovery_after_sse")
        missing = [s for s in ["Test Plan", "README.md", "Read README", "Add comment at top", "Save"] if s not in txt]
        if missing:
            result.fail("PLAYWRIGHT-3 markdown", f"Formatted plan markdown missing: {missing}")
        else:
            result.ok("PLAYWRIGHT-3 markdown", "Plan markdown includes title, file, and steps")
        chip_txt = ""
        if await page.get_by_test_id("loop-status-chip").count() > 0:
            chip_txt = await page.get_by_test_id("loop-status-chip").inner_text(timeout=2000)
        if "Loop failed to start" in txt and ("LOOP · PLANNING" in chip_txt or "LOOP · AWAITING" in chip_txt):
            result.fail("PLAYWRIGHT-4", f"Original contradiction still present: chip={chip_txt!r}")
        elif card_visible and "LOOP · PLANNING" in chip_txt:
            result.fail("PLAYWRIGHT-4", f"Chip still says planning while approval card is expected/visible: chip={chip_txt!r}")
        else:
            result.ok("PLAYWRIGHT-4", f"No chip/chat contradiction observed; chip={chip_txt!r}")

        await page.unroute("**/api/aurem-dev/loop/start", start_abort)
        await page.unroute("**/api/aurem-dev/loop/active**", active_recovery)
        await page.unroute("**/api/aurem-dev/loop/loop_fake_iter312_recovery/stream**", stream_recovery)

        # PLAYWRIGHT-5 happy path, fresh reload. Hold SSE briefly so Generating plan can be asserted.
        happy_release = asyncio.Event()
        happy_stream_requested = asyncio.Event()

        async def start_happy(route):
            await route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "loop_id": "loop_fake_iter312_happy",
                "state": "planning",
                "phase": "plan",
                "plan": None,
                "async_start": True,
            }))

        async def active_happy(route):
            await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "active": None}))

        async def stream_happy(route):
            happy_stream_requested.set()
            await happy_release.wait()
            ev = {
                "loop_id": "loop_fake_iter312_happy",
                "state": "awaiting_confirmation",
                "phase": "plan",
                "seq": 1,
                "message": "Plan ready — awaiting your approval.",
                "requires_user_action": True,
                "data": {"plan": PLAN},
            }
            await route.fulfill(status=200, content_type="text/event-stream", headers={"Cache-Control":"no-cache"}, body="data: " + json.dumps(ev) + "\n\n")

        await page.route("**/api/aurem-dev/loop/start", start_happy)
        await page.route("**/api/aurem-dev/loop/active**", active_happy)
        await page.route("**/api/aurem-dev/loop/loop_fake_iter312_happy/stream**", stream_happy)
        await seed_storage(page)
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_selector('[data-testid="chat-input"]', timeout=30000)
        if await page.get_by_test_id("loop-mode-toggle").get_attribute("data-loop-active") != "1":
            await page.get_by_test_id("loop-mode-toggle").click()
        await page.get_by_test_id("chat-input").fill("add a comment to README")
        await page.get_by_test_id("chat-send").click()
        try:
            await page.wait_for_function("document.body.innerText.includes('Generating plan')", timeout=7000)
            result.ok("PLAYWRIGHT-5 pending", "Generating plan pending bubble appeared")
        except Exception as e:
            result.fail("PLAYWRIGHT-5 pending", f"Generating plan pending bubble missing: {e}")
        try:
            await asyncio.wait_for(happy_stream_requested.wait(), timeout=10)
            result.ok("PLAYWRIGHT-5 stream", "Happy path SSE stream opened")
        except Exception as e:
            result.fail("PLAYWRIGHT-5 stream", f"Happy path SSE request missing: {e}")
        happy_release.set()
        try:
            await page.wait_for_selector('[data-testid="plan-approval-card"]', timeout=15000)
            await page.wait_for_selector('[data-testid="plan-approve-btn"]', timeout=3000)
            await page.wait_for_selector('[data-testid="plan-cancel-btn"]', timeout=3000)
            txt = await body_text(page)
            if "Loop failed to start" in txt:
                result.fail("PLAYWRIGHT-5 card", "Plan card appeared but Loop failed to start is present")
            else:
                result.ok("PLAYWRIGHT-5 card", "Async happy path card/buttons rendered without failure card")
        except Exception as e:
            result.fail("PLAYWRIGHT-5 card", f"Async happy path card/buttons missing: {e}")
        txt = await dump(page, "happy_after_sse")
        missing = [s for s in ["Test Plan", "README.md", "Read README", "Add comment at top", "Save"] if s not in txt]
        if missing:
            result.fail("PLAYWRIGHT-5 markdown", f"Happy path formatted plan markdown missing: {missing}")
        else:
            result.ok("PLAYWRIGHT-5 markdown", "Happy path markdown includes title, file, and steps")

        await no_error_selector_dump(page)
        (OUT / "requests.json").write_text(json.dumps(result.requests, indent=2), encoding="utf-8")
        (OUT / "console.json").write_text(json.dumps(result.console[-300:], indent=2), encoding="utf-8")
        (OUT / "checks.json").write_text(json.dumps(result.checks, indent=2), encoding="utf-8")
        await browser.close()

    failed = [c for c in result.checks if not c["passed"]]
    print("\nSUMMARY:")
    print(json.dumps({"failed_count": len(failed), "checks": result.checks}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))