/**
 * components/ReferralShare.jsx — Settings-page card showing the
 * user's referral link, click count, and one-tap share buttons.
 *
 * Pulls live data from `GET /api/aurem-dev/referrals/my`. No mocks.
 */
import React, { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function ReferralShare() {
  const [data, setData] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.get("/referrals/my")
      .then((r) => setData(r.data))
      .catch(() => setData(null));
  }, []);

  if (!data) return null;
  const link = data.ref_link || "";

  const copy = () => {
    navigator.clipboard?.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const tweet = () =>
    window.open(
      `https://twitter.com/intent/tweet?text=${encodeURIComponent(
        "Building with AUREM CTO — the autonomous AI engineer that ships code to your GitHub. Use my link to get started:"
      )}&url=${encodeURIComponent(link)}`,
      "_blank",
    );

  const linkedIn = () =>
    window.open(
      `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(link)}`,
      "_blank",
    );

  const stat = (label, value, color = "var(--text)") => (
    <div style={{ flex: 1, padding: "8px 0" }}>
      <div style={{ fontSize: 10, color: "var(--text-faint)",
                    letterSpacing: ".08em", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 600, color, marginTop: 2 }}>
        {value}
      </div>
    </div>
  );

  return (
    <div data-testid="referral-share-card" style={{
      padding: "20px 22px",
      background: "linear-gradient(180deg, rgba(109,212,161,0.08) 0%, var(--panel, #0f1219) 60%)",
      border: "1px solid rgba(109,212,161,0.25)",
      borderRadius: 10,
      display: "flex", flexDirection: "column", gap: 14,
    }}>
      <div>
        <h3 style={{
          margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text)",
          letterSpacing: "-0.01em",
        }}>Refer a builder, earn 1 month free</h3>
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-dim)" }}>
          When someone signs up via your link and upgrades, your next month
          is on us. No limit on how many you can stack.
        </p>
      </div>

      <div style={{
        display: "flex", alignItems: "stretch",
        background: "var(--bg-elev, #0a0c10)",
        border: "1px solid var(--border)",
        borderRadius: 6,
      }}>
        <input
          data-testid="referral-link"
          readOnly
          value={link}
          onClick={(e) => e.target.select()}
          style={{
            flex: 1, padding: "10px 12px",
            background: "transparent", border: "none", outline: "none",
            color: "var(--text)", fontFamily: "monospace", fontSize: 12,
          }}
        />
        <button
          data-testid="copy-referral-link"
          onClick={copy}
          style={{
            padding: "10px 14px", fontSize: 11, fontWeight: 600,
            background: copied ? "#6dd4a1" : "var(--accent, #ff8a2a)",
            color: "var(--bg, #0a0c10)",
            border: "none", cursor: "pointer",
            letterSpacing: ".04em",
          }}
        >{copied ? "Copied ✓" : "Copy"}</button>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button data-testid="share-twitter" onClick={tweet}
          style={{
            flex: 1, padding: "8px 12px", fontSize: 11, fontWeight: 600,
            background: "transparent", color: "var(--text)",
            border: "1px solid var(--border)", borderRadius: 5,
            cursor: "pointer", letterSpacing: ".04em",
          }}>Share on X / Twitter</button>
        <button data-testid="share-linkedin" onClick={linkedIn}
          style={{
            flex: 1, padding: "8px 12px", fontSize: 11, fontWeight: 600,
            background: "transparent", color: "var(--text)",
            border: "1px solid var(--border)", borderRadius: 5,
            cursor: "pointer", letterSpacing: ".04em",
          }}>Share on LinkedIn</button>
      </div>

      <div data-testid="referral-stats" style={{
        display: "flex", gap: 16,
        borderTop: "1px solid var(--border)", paddingTop: 12,
      }}>
        {stat("Clicks", data.clicks ?? 0, "var(--text-dim)")}
        {stat("Sign-ups", data.invites_sent ?? 0, "var(--accent, #ff8a2a)")}
        {stat("Paid conversions", data.verified_signups ?? 0, "#6dd4a1")}
        {stat("Free months earned", data.verified_signups ?? 0, "#6dd4a1")}
      </div>
    </div>
  );
}
