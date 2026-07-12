/**
 * components/CookieConsentBanner.jsx
 *
 * GDPR / DPDP / PIPEDA / CCPA-friendly cookie consent banner.
 *
 * Behaviour:
 *   • Reads/writes `aurem_consent` localStorage entry.
 *     Shape: { v: 1, ts: <iso>, choice: "all" | "essential" | "custom",
 *              cats: { necessary: true, functional: bool, analytics: bool, marketing: bool } }
 *   • Honours Global Privacy Control (Sec-GPC / navigator.globalPrivacyControl):
 *     if true, banner auto-defaults to "essential" and does not nag.
 *   • Suppresses Meta Pixel + Google Ads gtag when analytics/marketing = false.
 *   • Listens for `aurem:reopen-consent` custom event so footer link can re-open it.
 *
 * Non-goals:
 *   • Not a full CMP. If we need per-jurisdiction UX (auto-detect EU-only prompt),
 *     integrate a real CMP like Cookiebot/OneTrust — this file is the interim.
 */
import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";

const STORAGE_KEY = "aurem_consent";
const VERSION = 1;

const DEFAULT_CATS = {
  necessary:  true,   // always on, non-toggleable
  functional: true,
  analytics:  false,
  marketing:  false,
};

function loadConsent() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.v !== VERSION) return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

function saveConsent(choice, cats) {
  const record = {
    v: VERSION,
    ts: new Date().toISOString(),
    choice,
    cats,
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
  } catch (_) { /* private mode etc. — silent */ }
  return record;
}

/**
 * Disable / enable the browser trackers we ship in index.html.
 * We can't fully unload scripts already loaded, but we can:
 *   • Stub `fbq` so future calls no-op
 *   • Set gtag consent mode (`analytics_storage`, `ad_storage`) to denied
 *   • Dynamically load Meta Pixel if consent has just been granted and
 *     the loader in index.html skipped it (consent gate).
 */
function applyConsentToTrackers(cats) {
  try {
    if (!cats.marketing) {
      if (typeof window !== "undefined") {
        // Wipe queue + stub. Note: if the pixel script has already
        // loaded and sent PageView, we can only prevent FUTURE events.
        window.fbq = function noop() {};
        window.fbq.q = [];
      }
    } else if (typeof window !== "undefined" && (!window.fbq || !window.fbq.loaded)) {
      // Marketing just granted AND pixel never loaded (index.html gate
      // skipped it). Load it now.
      loadMetaPixel();
    }
    if (typeof window !== "undefined" && typeof window.gtag === "function") {
      window.gtag("consent", "update", {
        ad_storage:         cats.marketing  ? "granted" : "denied",
        analytics_storage:  cats.analytics  ? "granted" : "denied",
        ad_user_data:       cats.marketing  ? "granted" : "denied",
        ad_personalization: cats.marketing  ? "granted" : "denied",
      });
    }
  } catch (_) { /* never break page for consent-plumbing */ }
}

function loadMetaPixel() {
  if (typeof window === "undefined") return;
  if (window.fbq && window.fbq.loaded) return;
  !(function (f, b, e, v, n, t, s) {
    if (f.fbq && f.fbq.loaded) return;
    n = f.fbq = function () { n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments); };
    if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = "2.0";
    n.queue = n.queue || []; t = b.createElement(e); t.async = !0;
    t.src = v; s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
  })(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
  try {
    window.fbq("init", "1571887197933821");
    window.fbq("track", "PageView");
  } catch (_e) {
    // Best-effort: pixel network blocked or fbq stub replaced.
  }
}

function isGpcOn() {
  try {
    return Boolean(navigator?.globalPrivacyControl);
  } catch (_) { return false; }
}

export default function CookieConsentBanner() {
  const [visible, setVisible]   = useState(false);
  const [showPrefs, setShowPrefs] = useState(false);
  const [cats, setCats] = useState(DEFAULT_CATS);

  const openBanner = useCallback(() => {
    setShowPrefs(false);
    setCats((prev) => {
      const stored = loadConsent();
      return stored?.cats || prev;
    });
    setVisible(true);
  }, []);

  // First-mount decision: show only if no prior consent AND not GPC.
  useEffect(() => {
    const existing = loadConsent();
    if (existing) {
      applyConsentToTrackers(existing.cats);
      return;
    }
    if (isGpcOn()) {
      // Respect GPC: silently store essential-only, don't nag.
      const rec = saveConsent("essential-gpc", DEFAULT_CATS);
      applyConsentToTrackers(rec.cats);
      return;
    }
    // No stored consent + no GPC → show banner.
    setVisible(true);
  }, []);

  // Allow footer "Cookie preferences" link to reopen the banner.
  useEffect(() => {
    window.addEventListener("aurem:reopen-consent", openBanner);
    return () => window.removeEventListener("aurem:reopen-consent", openBanner);
  }, [openBanner]);

  if (!visible) return null;

  const acceptAll = () => {
    const next = { necessary: true, functional: true, analytics: true, marketing: true };
    const rec = saveConsent("all", next);
    applyConsentToTrackers(rec.cats);
    setVisible(false);
  };

  const rejectAll = () => {
    const next = { ...DEFAULT_CATS, functional: true }; // functional stays on (localStorage prefs)
    const rec = saveConsent("essential", next);
    applyConsentToTrackers(rec.cats);
    setVisible(false);
  };

  const saveCustom = () => {
    const rec = saveConsent("custom", cats);
    applyConsentToTrackers(rec.cats);
    setVisible(false);
  };

  const toggle = (key) => setCats((c) => ({ ...c, [key]: !c[key] }));

  return (
    <div
      role="dialog"
      aria-live="polite"
      aria-label="Cookie consent"
      data-testid="cookie-consent-banner"
      style={{
        position: "fixed",
        left: 16,
        right: 16,
        bottom: 16,
        maxWidth: 620,
        margin: "0 auto",
        zIndex: 10000,
        background: "rgba(12, 14, 18, 0.96)",
        backdropFilter: "blur(18px)",
        WebkitBackdropFilter: "blur(18px)",
        border: "1px solid rgba(255,200,120,0.18)",
        borderRadius: 12,
        padding: 20,
        color: "var(--text, #e8e3d3)",
        fontFamily: "Inter, system-ui, sans-serif",
        fontSize: 13,
        lineHeight: 1.55,
        boxShadow: "0 24px 60px rgba(0,0,0,0.48)",
      }}
    >
      {!showPrefs ? (
        <>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>
            We use cookies to improve your experience
          </div>
          <div style={{ color: "var(--text-dim, #a39d8a)", marginBottom: 14 }}>
            Strictly-necessary cookies keep you logged in. With your permission,
            we also use analytics and marketing cookies to measure conversions.
            Read our{" "}
            <Link to="/cookie-policy" style={{ color: "var(--accent, #ff8a2a)" }}>Cookie Policy</Link>
            {" · "}
            <Link to="/privacy" style={{ color: "var(--accent, #ff8a2a)" }}>Privacy Policy</Link>.
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "flex-end" }}>
            <button
              type="button"
              onClick={() => setShowPrefs(true)}
              data-testid="cookie-manage-btn"
              style={btnGhost}
            >
              Manage preferences
            </button>
            <button
              type="button"
              onClick={rejectAll}
              data-testid="cookie-reject-btn"
              style={btnGhost}
            >
              Reject non-essential
            </button>
            <button
              type="button"
              onClick={acceptAll}
              data-testid="cookie-accept-btn"
              style={btnPrimary}
            >
              Accept all
            </button>
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
            Cookie preferences
          </div>
          <CatRow label="Strictly necessary" desc="Login, session, security. Required." checked disabled testid="cookie-cat-necessary" />
          <CatRow label="Functional" desc="UI preferences (theme, sidebar state)." checked={cats.functional} onChange={() => toggle("functional")} testid="cookie-cat-functional" />
          <CatRow label="Analytics" desc="Measure how you use ORA (aggregate)." checked={cats.analytics} onChange={() => toggle("analytics")} testid="cookie-cat-analytics" />
          <CatRow label="Marketing" desc="Meta Pixel, Google Ads conversion tracking." checked={cats.marketing} onChange={() => toggle("marketing")} testid="cookie-cat-marketing" />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
            <button type="button" onClick={() => setShowPrefs(false)} style={btnGhost} data-testid="cookie-back-btn">Back</button>
            <button type="button" onClick={saveCustom} style={btnPrimary} data-testid="cookie-save-btn">Save preferences</button>
          </div>
        </>
      )}
    </div>
  );
}

function CatRow({ label, desc, checked, disabled, onChange, testid }) {
  return (
    <label
      data-testid={testid}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "10px 0",
        borderBottom: "1px solid rgba(255,200,120,0.08)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.72 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        style={{ marginTop: 3, cursor: disabled ? "default" : "pointer" }}
      />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text, #e8e3d3)" }}>{label}</div>
        <div style={{ fontSize: 12, color: "var(--text-dim, #a39d8a)", marginTop: 2 }}>{desc}</div>
      </div>
    </label>
  );
}

const btnPrimary = {
  background: "var(--accent, #ff8a2a)",
  color: "#0a0c10",
  border: "none",
  padding: "8px 16px",
  borderRadius: 8,
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

const btnGhost = {
  background: "transparent",
  color: "var(--text-dim, #a39d8a)",
  border: "1px solid rgba(255,200,120,0.24)",
  padding: "8px 16px",
  borderRadius: 8,
  fontSize: 13,
  cursor: "pointer",
};
