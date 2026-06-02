/**
 * F12ErrorCapture.js  —  AUREM F12 Integration
 * ================================================
 * Drop this script into your React app's index.html <head>
 * OR import it in main.jsx.
 *
 * What it captures:
 *   • console.error() calls
 *   • Unhandled JS exceptions (window.onerror)
 *   • Unhandled promise rejections
 *   • Failed fetch() requests (4xx / 5xx)
 *   • XMLHttpRequest errors
 *
 * It does NOT:
 *   • Send anything automatically
 *   • Capture user input or personal data
 *   • Run in production unless VITE_ENABLE_F12_CAPTURE=true
 *
 * Usage:
 *   1. Add <script src="/F12ErrorCapture.js"></script> to index.html
 *      OR import './F12ErrorCapture.js' in main.jsx
 *   2. In your ORA chat panel, call window.__auremF12.flush() when user
 *      submits a message — the errors are appended to the payload.
 *   3. Backend receives f12_payload in the chat request body.
 *
 * ENV: Only activates when VITE_ENABLE_F12_CAPTURE === "true"
 */

(function () {
  // F12 capture activates everywhere by default so the founder can use it
  // in production too. To force-disable on a host, set:
  //   window.__AUREM_DISABLE_F12 = true;  (before this script loads)
  const ENABLED = !window.__AUREM_DISABLE_F12;
  if (!ENABLED) return;

  const MAX_ERRORS    = 20;
  const MAX_BODY_LEN  = 500;

  const store = {
    console_errors: [],
    network_errors: [],
    stack_traces:   [],
    page_url:       window.location.href,
    captured_at:    new Date().toISOString(),
  };

  // ── 1. console.error capture ─────────────────────────────────────────────
  const _origError = console.error.bind(console);
  console.error = function (...args) {
    _origError(...args);
    if (store.console_errors.length >= MAX_ERRORS) return;
    store.console_errors.push({
      type:      "error",
      message:   args.map(String).join(" ").slice(0, 300),
      source:    _getCallerSource(),
      timestamp: new Date().toISOString(),
    });
  };

  // ── 2. window.onerror ────────────────────────────────────────────────────
  const _origOnError = window.onerror;
  window.onerror = function (msg, src, line, col, err) {
    if (_origOnError) _origOnError.call(this, msg, src, line, col, err);
    store.console_errors.push({
      type:      "uncaught_exception",
      message:   String(msg).slice(0, 300),
      source:    `${src}:${line}:${col}`,
      timestamp: new Date().toISOString(),
    });
    if (err && err.stack) {
      store.stack_traces.push(err.stack.slice(0, 1000));
    }
  };

  // ── 3. Unhandled promise rejections ─────────────────────────────────────
  window.addEventListener("unhandledrejection", function (e) {
    const reason = e.reason;
    const msg    = reason instanceof Error ? reason.message : String(reason);
    store.console_errors.push({
      type:      "unhandled_rejection",
      message:   msg.slice(0, 300),
      source:    "promise",
      timestamp: new Date().toISOString(),
    });
    if (reason instanceof Error && reason.stack) {
      store.stack_traces.push(reason.stack.slice(0, 1000));
    }
  });

  // ── 4. fetch() interceptor ───────────────────────────────────────────────
  const _origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url    = typeof input === "string" ? input : input.url;
    const method = (init && init.method) || "GET";

    let response;
    try {
      response = await _origFetch(input, init);
    } catch (err) {
      // Network failure (no response at all)
      store.network_errors.push({
        url:           url.slice(0, 200),
        method:        method.toUpperCase(),
        status:        0,
        response_body: `Network error: ${err.message}`.slice(0, MAX_BODY_LEN),
        timestamp:     new Date().toISOString(),
      });
      throw err;
    }

    if (!response.ok && response.status >= 400) {
      // Clone to read body without consuming it
      const clone = response.clone();
      let body = "";
      try {
        body = await clone.text();
      } catch (_) {}

      store.network_errors.push({
        url:           url.slice(0, 200),
        method:        method.toUpperCase(),
        status:        response.status,
        response_body: body.slice(0, MAX_BODY_LEN),
        timestamp:     new Date().toISOString(),
      });
    }

    return response;
  };

  // ── 5. XMLHttpRequest interceptor ────────────────────────────────────────
  const _XHROpen = XMLHttpRequest.prototype.open;
  const _XHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this._aurem_method = method;
    this._aurem_url    = url;
    return _XHROpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("load", function () {
      if (this.status >= 400) {
        store.network_errors.push({
          url:           (this._aurem_url || "").slice(0, 200),
          method:        (this._aurem_method || "GET").toUpperCase(),
          status:        this.status,
          response_body: (this.responseText || "").slice(0, MAX_BODY_LEN),
          timestamp:     new Date().toISOString(),
        });
      }
    });
    return _XHRSend.apply(this, arguments);
  };

  // ── Helpers ──────────────────────────────────────────────────────────────
  function _getCallerSource() {
    try {
      const stack = new Error().stack || "";
      const lines = stack.split("\n");
      // Skip F12ErrorCapture frames, find first external line
      for (let i = 2; i < lines.length; i++) {
        const line = lines[i];
        if (!line.includes("F12ErrorCapture")) {
          const match = line.match(/\((.+:\d+:\d+)\)/) || line.match(/at\s+(.+:\d+:\d+)/);
          if (match) return match[1].slice(0, 100);
        }
      }
    } catch (_) {}
    return "unknown";
  }

  // ── Public API ────────────────────────────────────────────────────────────
  window.__auremF12 = {
    /**
     * Returns current captured payload and clears the store.
     * Call this when user sends a message in ORA chat.
     */
    flush() {
      const payload = {
        ...store,
        page_url:     window.location.href,
        captured_at:  new Date().toISOString(),
        user_agent:   navigator.userAgent.slice(0, 100),
      };
      // Clear after flush
      store.console_errors = [];
      store.network_errors = [];
      store.stack_traces   = [];
      return payload;
    },

    /**
     * Returns true if there are any captured errors.
     */
    hasErrors() {
      return (
        store.console_errors.length > 0 ||
        store.network_errors.length  > 0 ||
        store.stack_traces.length    > 0
      );
    },

    /**
     * Returns count of captured errors (for badge display).
     */
    errorCount() {
      return store.console_errors.length + store.network_errors.length;
    },

    /**
     * Clears without returning — use if you want to reset after a fix.
     */
    clear() {
      store.console_errors = [];
      store.network_errors = [];
      store.stack_traces   = [];
    },
  };

  console.info("[AUREM F12] Error capture active. Errors will be sent to ORA when you chat.");
})();
