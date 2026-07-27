from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, expect
import json, time, os
BASE=Path('/app/frontend/.env').read_text().split('REACT_APP_BACKEND_URL=')[1].splitlines()[0].strip()
EMAIL='test@aurem.dev'; PASS='AuremTest2026!'
out=Path('/app/test_reports/bug_verification_artifacts')
screens=Path('/app/test_reports/screenshots/iter312'); screens.mkdir(parents=True, exist_ok=True)
logs=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1920,'height':1080})
    page=context.new_page()
    page.on('console', lambda msg: logs.append({'type':msg.type,'text':msg.text[:500]}))
    page.on('request', lambda req: logs.append({'type':'request','url':req.url,'method':req.method}) if '/loop/' in req.url else None)
    page.on('response', lambda resp: logs.append({'type':'response','url':resp.url,'status':resp.status}) if '/loop/' in resp.url else None)
    page.goto(BASE+'/login?next=/dashboard', wait_until='networkidle', timeout=60000)
    page.get_by_test_id('login-email').fill(EMAIL)
    page.get_by_test_id('login-password').fill(PASS)
    page.get_by_test_id('login-submit').click()
    page.wait_for_url('**/dashboard', timeout=60000)
    page.wait_for_load_state('networkidle', timeout=60000)
    page.screenshot(path=str(screens/'iter312_dashboard_after_login.jpg'), quality=40, full_page=False)
    print('URL_AFTER_LOGIN', page.url)
    # Ensure loop mode on
    toggle=page.get_by_test_id('loop-mode-toggle')
    expect(toggle).to_be_visible(timeout=30000)
    attr=toggle.get_attribute('data-loop-active')
    print('LOOP_TOGGLE_INITIAL', attr)
    if attr != '1':
        toggle.click()
        page.wait_for_timeout(500)
    print('LOOP_TOGGLE_AFTER', toggle.get_attribute('data-loop-active'))
    inp=page.get_by_test_id('chat-input')
    expect(inp).to_be_visible(timeout=30000)
    inp.fill('Iter 312 UI smoke: create a short plan to update a README typo only. Do not execute yet.')
    page.get_by_test_id('chat-send').click()
    page.wait_for_timeout(5000)
    text=page.locator('body').inner_text(timeout=10000)
    print('BODY_CONTAINS_LOOP_FAILED', 'Loop failed to start' in text)
    print('BODY_CONTAINS_PAT_ERROR', 'PAT' in text or 'Reconnect your repo' in text)
    print('BODY_CONTAINS_PLAN_CARD', 'Plan ready — your approval needed' in text)
    chip_text=''
    if page.get_by_test_id('loop-status-chip').count():
        chip_text=page.get_by_test_id('loop-status-chip').first.inner_text(timeout=5000)
    print('CHIP_TEXT', chip_text)
    page.screenshot(path=str(screens/'iter312_after_loop_submit.jpg'), quality=40, full_page=False)
    # Specific error selectors
    error_text = page.evaluate("""() => {
const errorElements = Array.from(document.querySelectorAll('.error, [class*="error"], [id*="error"]'));
return errorElements.map(el => el.textContent).join(", ");
}""")
    print('ERROR_TEXT', error_text if error_text else 'No error messages found on the page')
    (out/'iter312_ui_logs.json').write_text(json.dumps(logs, indent=2))
    browser.close()
