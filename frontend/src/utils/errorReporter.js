/**
 * errorReporter.js — Iter 212h
 *
 * Catches uncaught JS errors (console.error + unhandledrejection)
 * and silently POSTs them to the admin /errors/report endpoint.
 *
 * Goals:
 *   • Zero noise — never throw, never log (would loop), never block.
 *   • Dedupe locally so a runaway loop doesn't hammer the API; the
 *     backend dedupes too, but client-side throttling spares the
 *     network entirely.
 *   • Tag every report with the current URL + UA so the admin can
 *     reproduce.
 *
 * Import this once from main.jsx — side-effecting, no exports needed.
 */

const REPORT_URL = (() => {
  const base = (typeof process !== "undefined"
                 && process.env
                 && process.env.REACT_APP_BACKEND_URL) || "";
  return `${base}/api/aurem-dev/admin/errors/report`;
})();

// Local dedupe: same (message + url) won't re-report within this window.
const RECENT = new Map();  // key → ts (ms)
const COOLDOWN_MS = 30_000;
const MAX_PAYLOAD_PER_MIN = 20;
let _budget = MAX_PAYLOAD_PER_MIN;
setInterval(() => { _budget = MAX_PAYLOAD_PER_MIN; }, 60_000);

function _shouldReport(message, url) {
  if (_budget <= 0) return false;
  const key = `${message}|${url}`;
  const last = RECENT.get(key) || 0;
  if (Date.now() - last < COOLDOWN_MS) return false;
  RECENT.set(key, Date.now());
  // Bound the map so a long-running tab doesn't leak memory.
  if (RECENT.size > 200) {
    const oldest = [...RECENT.entries()].sort((a, b) => a[1] - b[1])[0];
    if (oldest) RECENT.delete(oldest[0]);
  }
  _budget -= 1;
  return true;
}

async function _send(payload) {
  // Use sendBeacon when available — it survives page unload, doesn't
  // race with navigation, and never blocks the UI thread.
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([JSON.stringify(payload)],
                            { type: "application/json" });
      if (navigator.sendBeacon(REPORT_URL, blob)) return;
    }
  } catch { /* fall through */ }
  // Fallback: keep-alive fetch so the request survives unload.
  try {
    await fetch(REPORT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
  } catch { /* swallow — never log, would loop */ }
}

function _toStr(arg) {
  if (arg === null) return "null";
  if (arg === undefined) return "undefined";
  if (arg instanceof Error) {
    return arg.stack || `${arg.name}: ${arg.message}`;
  }
  if (typeof arg === "object") {
    try { return JSON.stringify(arg).slice(0, 500); } catch { return "[object]"; }
  }
  return String(arg);
}

function _capture(args, type) {
  const message = args.map(_toStr).join(" ").slice(0, 4_000);
  const stack = (args.find((a) => a instanceof Error)?.stack || "")
                  .slice(0, 16_000);
  const url = (typeof window !== "undefined" && window.location)
                ? window.location.href : "";
  if (!_shouldReport(message, url)) return;
  _send({
    message,
    stack,
    url,
    timestamp: new Date().toISOString(),
    type,
  });
}

// ── 1. Hook console.error ─────────────────────────────────────────
if (typeof window !== "undefined") {
  const _origErr = window.console && window.console.error;
  if (typeof _origErr === "function") {
    window.console.error = function patched(...args) {
      _capture(args, "console_error");
      return _origErr.apply(this, args);
    };
  }

  // ── 2. unhandledrejection (async rejections) ────────────────────
  window.addEventListener("unhandledrejection", (e) => {
    const r = e?.reason;
    _capture([r], "unhandled_rejection");
  });

  // ── 3. window.onerror (top-level synchronous errors) ────────────
  window.addEventListener("error", (e) => {
    if (!e) return;
    const err = e.error || e.message || "unknown_error";
    _capture([err], "window_error");
  });
}
