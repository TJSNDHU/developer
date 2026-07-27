/**
 * lib/useAutoClearConsole.js — Iter 321 (opt-IN default)
 *
 * Historical (Iter 212m-25): this hook auto-cleared DevTools on
 * mount + every route change + every 30 seconds. During Iter 318's
 * live debugging session the founder observed the 30s timer wiping
 * their console mid-investigation (README data-loss evidence lost
 * between refreshes).
 *
 * Iter 321 fix: gate ALL console.clear() calls behind an OPT-IN
 * flag. Default behaviour is now "never clear". To restore the old
 * behaviour (e.g., a QA harness that needs a clean console between
 * routes), set the flag ONCE in the DevTools console:
 *
 *     window.__AUREM_ENABLE_AUTO_CLEAR_CONSOLE = true;
 *
 * The previous opt-OUT variable (`__AUREM_DISABLE_AUTO_CLEAR_CONSOLE`)
 * is intentionally removed — it defaulted to the wrong direction and
 * was easy to forget when handing off a debugging session.
 */
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const PERIOD_MS = 30 * 1000;

function safeClear() {
  if (typeof window === "undefined") return;
  // ── Iter 321 · opt-IN gate ─────────────────────────────────────
  // Default OFF. Only fires when the founder / QA harness has
  // explicitly set window.__AUREM_ENABLE_AUTO_CLEAR_CONSOLE = true.
  if (!window.__AUREM_ENABLE_AUTO_CLEAR_CONSOLE) return;
  try {
    if (console && typeof console.clear === "function") console.clear();
  } catch {
    /* no-op — never let console clearing crash the app */
  }
}

export default function useAutoClearConsole() {
  const location = useLocation();

  // Trigger 1: route change (also fires once on mount).
  useEffect(() => {
    safeClear();
  }, [location.pathname]);

  // Trigger 2: every 30s while the app is mounted.
  // The setInterval STILL runs (cheap, single timer) but the
  // safeClear() body no-ops unless the opt-in flag is set. This
  // keeps the mount / cleanup shape stable so nothing else needs
  // to react to the mode change.
  useEffect(() => {
    const id = window.setInterval(safeClear, PERIOD_MS);
    return () => window.clearInterval(id);
  }, []);
}
