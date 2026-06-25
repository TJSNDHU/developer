"""
Iter 212m-25 — Cache cleanup + auto-clear console (frontend feature).

Pure source-level pins. Since this is a frontend-only feature, the
backend has nothing to assert other than that NO backend file was
accidentally modified for the cleanup logic. The frontend pieces:
  - lib/cacheCleaner.js — clearUICache() + clearUICacheAndReload()
  - lib/useAutoClearConsole.js — startup + route-change + 30s
  - components/ClearCacheButton.jsx — "🧹 Clear cache" pill
  - components/Shell.jsx — logo click invokes clear+reload, button
                           sits under the logo when expanded
  - App.jsx — AutoClearConsoleHost wraps useAutoClearConsole inside
              the BrowserRouter so useLocation() works
"""
from __future__ import annotations

import os
import re

FE = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src")
CACHE_CLEANER = os.path.join(FE, "lib", "cacheCleaner.js")
AUTO_CLEAR    = os.path.join(FE, "lib", "useAutoClearConsole.js")
CLEAR_BTN     = os.path.join(FE, "components", "ClearCacheButton.jsx")
SHELL         = os.path.join(FE, "components", "Shell.jsx")
APP           = os.path.join(FE, "App.jsx")


def test_cache_cleaner_module_exists():
    assert os.path.isfile(CACHE_CLEANER), \
        "lib/cacheCleaner.js missing"


def test_cache_cleaner_preserves_auth_keys():
    src = open(CACHE_CLEANER).read()
    # AUTH_KEYS must include both aurem_token and aurem_user.
    assert 'AUTH_KEYS = ["aurem_token", "aurem_user"]' in src
    # The clear loop skips keys in AUTH_KEYS.
    assert "if (k && !AUTH_KEYS.includes(k)) keysToRemove.push(k);" in src


def test_cache_cleaner_clears_all_four_stores():
    """sessionStorage, localStorage, IndexedDB, CacheStorage."""
    src = open(CACHE_CLEANER).read()
    assert "sessionStorage.clear()" in src
    assert "localStorage.removeItem" in src
    assert "indexedDB.deleteDatabase" in src
    assert "caches.delete(name)" in src


def test_cache_cleaner_export_reload_helper():
    """clearUICacheAndReload must reload the CURRENT URL with a
    cache-bust param so the browser drops any 304 / from-cache HTML."""
    src = open(CACHE_CLEANER).read()
    assert "export async function clearUICacheAndReload" in src
    assert "url.searchParams.set(\"_cc\"" in src
    assert "window.location.replace(url.toString())" in src


def test_use_auto_clear_console_module():
    src = open(AUTO_CLEAR).read()
    # 30s periodic interval.
    assert "PERIOD_MS = 30 * 1000" in src
    assert "setInterval(safeClear, PERIOD_MS)" in src
    # Startup + route change via useLocation().
    assert "useLocation" in src
    assert "location.pathname" in src
    # Disable escape hatch for devs.
    assert "__AUREM_DISABLE_AUTO_CLEAR_CONSOLE" in src
    # console.clear() is what actually fires.
    assert "console.clear()" in src


def test_clear_cache_button_component():
    src = open(CLEAR_BTN).read()
    # Hides itself when sidebar is collapsed.
    assert "if (collapsed) return null;" in src
    # Test-id default.
    assert "data-testid={testid || \"clear-cache-btn\"}" in src
    # Uses the reload helper, not just clear.
    assert "clearUICacheAndReload" in src


def test_shell_logo_triggers_cache_clear_and_reload():
    """Logo click must call clearUICacheAndReload — the user asked
    for click-logo → clear cache + auto-refresh keeping current page."""
    src = open(SHELL).read()
    assert "clearUICacheAndReload" in src
    # The brand-link element changed from NavLink to button.
    assert 'data-testid="brand-link"' in src
    # And the ClearCacheButton sits in the sidebar.
    assert "<ClearCacheButton collapsed={collapsed}" in src


def test_app_wires_auto_clear_inside_router():
    """useLocation() only works inside <BrowserRouter>, so the hook
    must be hosted in a child of BrowserRouter — NOT in App() directly."""
    src = open(APP).read()
    assert "useAutoClearConsole" in src
    assert "function AutoClearConsoleHost()" in src
    assert "<AutoClearConsoleHost />" in src
    # AutoClearConsoleHost must be rendered INSIDE <BrowserRouter>.
    router_open = src.find("<BrowserRouter>")
    router_close = src.find("</BrowserRouter>")
    host_use = src.find("<AutoClearConsoleHost />")
    assert router_open != -1 and router_close != -1 and host_use != -1
    assert router_open < host_use < router_close


def test_shell_no_longer_uses_navlink_for_brand():
    """We swapped the brand NavLink for a <button> so the click can
    intercept default navigation and do clear+reload instead."""
    src = open(SHELL).read()
    idx = src.find('data-testid="brand-link"')
    assert idx != -1
    # Walk back to the most recent opening tag.
    head = src[:idx]
    last_button = head.rfind("<button")
    last_navlink = head.rfind("<NavLink")
    assert last_button > last_navlink, (
        f"brand-link must sit inside a <button>, not <NavLink>. "
        f"last <button> at {last_button}, last <NavLink> at {last_navlink}"
    )
