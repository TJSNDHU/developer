"""Quick isolated test — persist_turn via /chat/stream on production."""
import asyncio, json, os, time
from playwright.async_api import async_playwright

PROD = "https://auremcto.com"
EMAIL = "teji.ss1986@gmail.com"
PASSWORD = "Singh1986$"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context()
        page = await ctx.new_page()
        await page.goto(f"{PROD}/login", timeout=45000)
        await page.wait_for_selector('[data-testid="login-email"]', timeout=15000)
        await page.fill('[data-testid="login-email"]', EMAIL)
        await page.fill('[data-testid="login-password"]', PASSWORD)
        await page.click('[data-testid="login-submit"]')
        await page.wait_for_url(lambda u: "/login" not in u, timeout=25000)
        # Get token
        token = await page.evaluate("() => localStorage.getItem('aurem_token')")
        print(f"TOKEN present: {bool(token)}  len={len(token or '')}")

        session_id = f"e2e-persist-{int(time.time())}"

        # 1) Fire /chat/stream via fetch, read to completion
        result = await page.evaluate(f"""
            async () => {{
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/stream', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + t,
                    }},
                    body: JSON.stringify({{
                        prompt: 'Say the word BANANA and stop.',
                        session_id: '{session_id}',
                        agent: 'auto', mode: 'swift',
                        max_tool_iters: 0,
                    }}),
                }});
                if (!r.ok) return {{ status: r.status, err: await r.text() }};
                const reader = r.body.getReader();
                const decoder = new TextDecoder();
                let buf = '';
                while (true) {{
                    const {{done, value}} = await reader.read();
                    if (done) break;
                    buf += decoder.decode(value, {{stream:true}});
                }}
                return {{ status: r.status, bytes: buf.length, tail: buf.slice(-400) }};
            }}
        """)
        print("STREAM RESULT:", json.dumps(result)[:800])

        # 2) Wait 3s for _persist_turn to complete
        await asyncio.sleep(3)

        # 3) Fetch history
        hist = await page.evaluate(f"""
            async () => {{
                const t = localStorage.getItem('aurem_token');
                const r = await fetch('/api/aurem-dev/chat/history?session_id={session_id}', {{
                    headers: {{'Authorization': 'Bearer ' + t}}
                }});
                const j = await r.json();
                return {{ status: r.status, count: (j.messages || []).length, sample: (j.messages || []).slice(0, 2) }};
            }}
        """)
        print("HISTORY RESULT:", json.dumps(hist)[:800])

        await b.close()

asyncio.run(main())
