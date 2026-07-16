/**
 * components/PersonalTrackBanner.jsx
 *
 * Dismissible one-time banner shown on Dashboard to LEGACY users
 * whose `dev_users.track` field is null / unset. Introduces the new
 * Personal Track and offers a one-click jump to /choose-track.
 *
 * Visibility contract:
 *   - Fetches /auth/me once on mount.
 *   - Renders only when the response's `track` field is missing / null
 *     (i.e. legacy pre–Personal-Track accounts).
 *   - Users with track === "developer" or "personal" NEVER see it.
 *   - Once dismissed OR clicked (Try it), a localStorage flag hides
 *     it forever on this browser.
 *   - Failure to fetch /auth/me → silently hide (no crash, no flash).
 */
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Wand2, X, ArrowRight } from "lucide-react";
import { api } from "../lib/api";

const DISMISS_KEY = "aurem_personal_track_banner_dismissed";

export default function PersonalTrackBanner() {
  const navigate = useNavigate();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // Skip work entirely if the user already dismissed it.
    try {
      if (localStorage.getItem(DISMISS_KEY) === "1") return;
    } catch { /* ignore */ }

    api.get("/auth/me")
      .then((r) => {
        if (cancelled) return;
        const track = r.data?.user?.track;
        // Only show for legacy users where track is null/undefined/empty.
        if (!track) setVisible(true);
      })
      .catch(() => { /* silently hide */ });

    return () => { cancelled = true; };
  }, []);

  const dismiss = () => {
    try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ }
    setVisible(false);
  };

  const tryIt = () => {
    try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ }
    navigate("/choose-track");
  };

  if (!visible) return null;

  return (
    <div
      data-testid="personal-track-banner"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "12px 16px",
        margin: "0 0 10px 0",
        borderRadius: 10,
        border: "1px solid rgba(224,122,95,0.28)",
        background:
          "linear-gradient(90deg, rgba(224,122,95,0.10) 0%, rgba(224,122,95,0.04) 100%)",
        color: "var(--text, #e8e3d3)",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}
    >
      <div
        style={{
          width: 34, height: 34, borderRadius: 8,
          background: "rgba(224,122,95,0.16)",
          display: "flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Wand2 size={17} color="#E07A5F" strokeWidth={1.8} />
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>
          New: Personal Track — build apps without code
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim, #a39d8a)", lineHeight: 1.45 }}>
          Describe your idea in plain English. AUREM handles repo, code,
          database, deploy. Switch anytime from Settings.
        </div>
      </div>

      <button
        type="button"
        data-testid="personal-track-banner-try"
        onClick={tryIt}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "8px 14px",
          fontSize: 12, fontWeight: 600,
          color: "#0a0a0a",
          background: "#E07A5F",
          border: "none", borderRadius: 6,
          cursor: "pointer",
          flexShrink: 0,
        }}
      >
        Try it <ArrowRight size={13} />
      </button>

      <button
        type="button"
        data-testid="personal-track-banner-dismiss"
        onClick={dismiss}
        aria-label="Dismiss"
        style={{
          padding: 6, marginLeft: 2,
          background: "transparent",
          border: "none",
          color: "var(--text-dim, #a39d8a)",
          cursor: "pointer",
          borderRadius: 6,
          flexShrink: 0,
        }}
      >
        <X size={15} />
      </button>
    </div>
  );
}
