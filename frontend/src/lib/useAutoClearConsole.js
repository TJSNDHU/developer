/**
 * lib/useAutoClearConsole.js — Iter 212m-25
 *
 * Auto-clears the browser DevTools (F12) console:
 *   - Once at app startup
 *   - On every route change (location.pathname change)
 *   - Periodically every 30 seconds
 *
 * Skipped in localhost / development so a dev's debugging session
 * isn't wiped while they're inspecting an error. The user asked for
 * BOTH triggers (30s periodic + startup + route change).
 *
 * To temporarily disable for debugging without a code change, run in
 * the console:
 *     window.__AUREM_DISABLE_AUTO_CLEAR_CONSOLE = true;
 */
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const PERIOD_MS = 30 * 1000;

function safeClear() {
  if (typeof window === "undefined") return;
  if (window.__AUREM_DISABLE_AUTO_CLEAR_CONSOLE) return;
  try {
    // console.clear() is a no-op when DevTools "Preserve log" is on,
    // which is exactly the dev's escape hatch. In normal sessions it
    // clears every output line.
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
  useEffect(() => {
    const id = window.setInterval(safeClear, PERIOD_MS);
    return () => window.clearInterval(id);
  }, []);
}
