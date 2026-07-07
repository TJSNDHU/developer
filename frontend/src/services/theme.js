/**
 * theme.js — Aurem CTO theme controller (Iter 212m-52)
 *
 * Three modes:
 *   • "auto"  — follow OS / browser via prefers-color-scheme
 *   • "light" — explicit day mode (white background)
 *   • "dark"  — explicit night mode (original Aurem aesthetic)
 *
 * Choice persists in localStorage under `aurem_theme`. Default is
 * "auto" so brand-new visitors get whatever their OS prefers, and
 * the founder's existing dark-mode users see no visual change until
 * they explicitly switch.
 *
 * The applied resolved theme is written to `<html data-theme="…">`
 * which the CSS in index.css reads to flip CSS custom-properties.
 * That means EVERY component is themed automatically — no per-
 * component refactor needed.
 */

const STORAGE_KEY = "aurem_theme";
const VALID_MODES = ["auto", "light", "dark"];
let _mql = null;
let _listeners = new Set();

export function getThemeMode() {
  const raw = (() => {
    try { return localStorage.getItem(STORAGE_KEY); }
    catch { return null; }
  })();
  return VALID_MODES.includes(raw) ? raw : "auto";
}

export function getResolvedTheme(mode = getThemeMode()) {
  // Iter 212m-181 — DARK ONLY. The founder decided the product is
  // dark-mode exclusively. Previously "auto" followed the OS via
  // prefers-color-scheme, so iPhones set to Light showed a broken
  // light UI. We now always resolve to "dark" regardless of mode /
  // OS preference. The ThemeToggle + light tokens stay in the code
  // (harmless) but can never flip the app to light.
  return "dark";
}

function _apply(resolved) {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", resolved);
  // Notify any subscribed components (ThemeToggle, etc.).
  _listeners.forEach((fn) => { try { fn(); } catch { /* ignore */ } });
}

export function setThemeMode(mode) {
  if (!VALID_MODES.includes(mode)) mode = "auto";
  try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* ignore */ }
  _apply(getResolvedTheme(mode));
}

export function initTheme() {
  // Applied on app boot. Safe to call multiple times — idempotent.
  _apply(getResolvedTheme());
  // When the user is on "auto" and the OS flips, follow.
  if (typeof window !== "undefined" && window.matchMedia && !_mql) {
    _mql = window.matchMedia("(prefers-color-scheme: light)");
    const handler = () => {
      if (getThemeMode() === "auto") _apply(getResolvedTheme("auto"));
    };
    if (_mql.addEventListener) _mql.addEventListener("change", handler);
    else if (_mql.addListener) _mql.addListener(handler);  // Safari ≤13
  }
}

export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
