/**
 * pages/personal/ChooseTrack.jsx — Iter 212m-235 — Phase 6
 *
 * Mandatory 2-card selector shown once, right after signup. Sets
 * `dev_users.track` via `POST /auth/set-track`, then routes:
 *   • personal  → /build
 *   • developer → /dashboard  (or state.next if it was preserved)
 *
 * Existing users never see this — login flow reads their track and
 * routes accordingly. See `hooks/useTrack.js`.
 */
import React, { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { Wand2, TerminalSquare, ArrowRight } from "lucide-react";
import { api } from "../../lib/api";

const CARD_BASE = {
  cursor: "pointer",
  padding: "36px 28px",
  borderRadius: 20,
  border: "1px solid",
  transition: "transform 200ms ease, box-shadow 200ms ease, opacity 200ms ease",
};

export default function ChooseTrack() {
  const nav = useNavigate();
  const loc = useLocation();
  const nextFromSignup = loc.state?.next || "/dashboard";
  const [hovered, setHovered] = useState(null);
  const [busy, setBusy] = useState(false);

  async function pick(track) {
    if (busy) return;
    setBusy(true);
    try {
      await api.post("/auth/set-track", { track });
      nav(track === "personal" ? "/build" : nextFromSignup, { replace: true });
    } catch (e) {
      setBusy(false);
      alert("Hmm, something got tangled up. Let's try that again.");
    }
  }

  return (
    <div
      data-testid="choose-track-page"
      style={{
        minHeight: "100vh",
        background: "#FDFDF9",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "48px 24px",
        fontFamily: "'Manrope', system-ui, -apple-system, sans-serif",
        color: "#1C1C19",
      }}
    >
      <motion.div
        initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        style={{ maxWidth: 900, width: "100%" }}
      >
        <h1 data-testid="choose-track-heading" style={{
          fontSize: "clamp(32px, 5vw, 48px)",
          fontWeight: 500, letterSpacing: "-0.03em",
          textAlign: "center", margin: "0 0 12px",
        }}>
          Pick how you want to build.
        </h1>
        <p data-testid="choose-track-subheading" style={{
          fontSize: 16, color: "#6B6B63",
          textAlign: "center", margin: "0 0 44px", lineHeight: 1.6,
        }}>
          You can switch anytime from Settings.
        </p>

        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
          gap: 24,
        }}>
          {/* Personal Track — warm cream card */}
          <motion.div
            data-testid="track-selector-personal-card"
            role="button" tabIndex={0}
            onMouseEnter={() => setHovered("personal")}
            onMouseLeave={() => setHovered(null)}
            onClick={() => pick("personal")}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pick("personal")}
            whileHover={{ scale: 1.02, y: -4 }}
            whileTap={{ scale: 0.98 }}
            style={{
              ...CARD_BASE,
              background: "#FFFFFF",
              borderColor: "#E5E5DF",
              boxShadow: "0 8px 32px rgba(224,122,95,0.08)",
              opacity: hovered && hovered !== "personal" ? 0.5 : 1,
            }}
          >
            <div style={{
              width: 52, height: 52, borderRadius: 14,
              background: "rgba(224,122,95,0.12)",
              display: "flex", alignItems: "center", justifyContent: "center",
              marginBottom: 20,
            }}>
              <Wand2 size={26} color="#E07A5F" strokeWidth={1.6} />
            </div>
            <h2 style={{
              fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em",
              margin: "0 0 8px",
            }}>Personal Track</h2>
            <p style={{ fontSize: 14, lineHeight: 1.65, color: "#6B6B63", margin: 0 }}>
              Describe your idea. We handle the code, the database, the deployment —
              you just watch your app come to life. No GitHub, no terminals, no jargon.
            </p>
            <div data-testid="track-personal-cta" style={{
              marginTop: 24, display: "flex", alignItems: "center", gap: 8,
              color: "#E07A5F", fontSize: 14, fontWeight: 600,
            }}>
              Start building <ArrowRight size={16} />
            </div>
          </motion.div>

          {/* Developer Track — dark IDE card */}
          <motion.div
            data-testid="track-selector-developer-card"
            role="button" tabIndex={0}
            onMouseEnter={() => setHovered("developer")}
            onMouseLeave={() => setHovered(null)}
            onClick={() => pick("developer")}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pick("developer")}
            whileHover={{ scale: 1.02, y: -4 }}
            whileTap={{ scale: 0.98 }}
            style={{
              ...CARD_BASE,
              background: "#0A0A0A",
              borderColor: "#27272A",
              color: "#FFFFFF",
              boxShadow: "0 8px 32px rgba(0,0,0,0.24)",
              opacity: hovered && hovered !== "developer" ? 0.5 : 1,
              fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
            }}
          >
            <div style={{
              width: 52, height: 52, borderRadius: 14,
              background: "rgba(59,130,246,0.16)",
              display: "flex", alignItems: "center", justifyContent: "center",
              marginBottom: 20,
            }}>
              <TerminalSquare size={26} color="#3B82F6" strokeWidth={1.6} />
            </div>
            <h2 style={{
              fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em",
              margin: "0 0 8px", color: "#FFFFFF",
            }}>Developer Track</h2>
            <p style={{ fontSize: 14, lineHeight: 1.65, color: "#A1A1AA", margin: 0 }}>
              Connect your own repos. Full IDE-style control over code, deployments,
              and infrastructure. Everything AUREM CTO has built for pro developers.
            </p>
            <div data-testid="track-developer-cta" style={{
              marginTop: 24, display: "flex", alignItems: "center", gap: 8,
              color: "#3B82F6", fontSize: 14, fontWeight: 600,
              fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
            }}>
              Enter workspace <ArrowRight size={16} />
            </div>
          </motion.div>
        </div>

        <p style={{
          marginTop: 32, textAlign: "center", fontSize: 12, color: "#8B8B7D",
        }}>
          {busy ? "Setting up your workspace…" : "Not sure? Personal Track is a safer first pick — you can switch to Developer anytime."}
        </p>
      </motion.div>
    </div>
  );
}
