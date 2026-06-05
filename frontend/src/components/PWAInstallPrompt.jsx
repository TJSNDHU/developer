/**
 * PWAInstallPrompt.jsx — friendly install banner after sign-in.
 *
 * Hooks the browser's `beforeinstallprompt` event and surfaces it as a
 * branded card the user can tap to install AUREM as a PWA. Pairs with
 * Iter 80's service-worker + manifest work.
 *
 * Display rules:
 *   1. Only when `beforeinstallprompt` actually fired (browser thinks
 *      this site is installable).
 *   2. Don't pester an already-installed PWA (display-mode standalone).
 *   3. Show ONCE per user, then either install or dismiss permanently
 *      via localStorage flags.
 *   4. Auto-pop right after a fresh login (the `aurem_just_logged_in`
 *      flag set by Login / Signup / OAuthFinish).
 */
import React, { useEffect, useRef, useState } from "react";
import { X, Download, Smartphone } from "lucide-react";

const DISMISSED_KEY = "aurem_pwa_dismissed";
const INSTALLED_KEY = "aurem_pwa_installed";

export default function PWAInstallPrompt() {
  const [visible, setVisible]     = useState(false);
  const [busy, setBusy]           = useState(false);
  const deferredEvent = useRef(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Already installed → nothing to do.
    if (window.matchMedia?.("(display-mode: standalone)").matches) {
      try { localStorage.setItem(INSTALLED_KEY, "1"); } catch {}
      return;
    }
    if (localStorage.getItem(INSTALLED_KEY) === "1") return;
    if (localStorage.getItem(DISMISSED_KEY) === "1") return;

    function onPrompt(e) {
      e.preventDefault();
      deferredEvent.current = e;
      // Only auto-pop right after a fresh login — never on a random
      // background tab visit, that'd be obnoxious.
      const fresh = localStorage.getItem("aurem_just_logged_in") === "1";
      if (fresh) {
        setVisible(true);
        try { localStorage.removeItem("aurem_just_logged_in"); } catch {}
      }
    }
    function onInstalled() {
      try { localStorage.setItem(INSTALLED_KEY, "1"); } catch {}
      setVisible(false);
    }
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  async function install() {
    const evt = deferredEvent.current;
    if (!evt) return;
    setBusy(true);
    try {
      await evt.prompt();
      const choice = await evt.userChoice;
      if (choice?.outcome === "accepted") {
        try { localStorage.setItem(INSTALLED_KEY, "1"); } catch {}
      } else {
        // Soft dismiss only — user said "not now", let `appinstalled`
        // / a future login retry.
      }
    } finally {
      setBusy(false);
      setVisible(false);
      deferredEvent.current = null;
    }
  }

  function dismiss() {
    try { localStorage.setItem(DISMISSED_KEY, "1"); } catch {}
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div
      data-testid="pwa-install-prompt"
      role="dialog"
      aria-label="Install AUREM CTO"
      style={{
        position: "fixed",
        right: 18,
        bottom: 18,
        zIndex: 90,
        maxWidth: 360,
        padding: "16px 18px 14px",
        borderRadius: 6,
        background: "linear-gradient(180deg, #131a2c 0%, #0d1322 100%)",
        border: "1px solid rgba(255, 206, 122, 0.28)",
        boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
        color: "#e9edf2",
        fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
        animation: "auremPwaIn 220ms ease-out",
      }}
    >
      <button
        onClick={dismiss}
        aria-label="Dismiss install prompt"
        data-testid="pwa-install-dismiss"
        style={{
          position: "absolute",
          top: 8, right: 8,
          width: 24, height: 24,
          background: "transparent",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 4,
          color: "rgba(255,255,255,0.5)",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <X size={12} />
      </button>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <div
          style={{
            width: 38, height: 38, borderRadius: 8,
            background: "rgba(255, 206, 122, 0.12)",
            border: "1px solid rgba(255, 206, 122, 0.32)",
            display: "inline-flex", alignItems: "center",
            justifyContent: "center", flexShrink: 0,
            color: "#ffce7a",
          }}
        >
          <Smartphone size={18} />
        </div>
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 11, letterSpacing: "0.14em",
              color: "#ffce7a", fontFamily: "'JetBrains Mono', monospace",
              marginBottom: 4,
            }}
          >
            PWA · install AUREM
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.35 }}>
            Open AUREM CTO straight from your home screen.
          </div>
          <div
            style={{
              fontSize: 12, color: "rgba(233,237,242,0.7)",
              marginTop: 4, lineHeight: 1.4,
            }}
          >
            One tap to chat with ORA · works offline shell · faster than
            the browser tab.
          </div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button
          onClick={install}
          disabled={busy}
          data-testid="pwa-install-confirm"
          style={{
            flex: 1,
            padding: "9px 12px",
            borderRadius: 4,
            background: "#ffce7a",
            color: "#0a0e1a",
            border: "none",
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: "0.06em",
            fontFamily: "'JetBrains Mono', monospace",
            textTransform: "uppercase",
            cursor: busy ? "wait" : "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
          }}
        >
          <Download size={12} /> {busy ? "installing…" : "install"}
        </button>
        <button
          onClick={dismiss}
          data-testid="pwa-install-later"
          style={{
            padding: "9px 12px",
            borderRadius: 4,
            background: "transparent",
            color: "rgba(233,237,242,0.7)",
            border: "1px solid rgba(255,255,255,0.12)",
            fontSize: 12,
            letterSpacing: "0.04em",
            fontFamily: "'JetBrains Mono', monospace",
            cursor: "pointer",
          }}
        >
          not now
        </button>
      </div>
      <style>{`@keyframes auremPwaIn {
        from { opacity: 0; transform: translateY(12px); }
        to   { opacity: 1; transform: translateY(0); }
      }`}</style>
    </div>
  );
}
