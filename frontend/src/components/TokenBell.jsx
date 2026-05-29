/**
 * TokenBell.jsx — Wallet badge + recharge nudges.
 *
 * Rings + toasts at: 500, 200, 100, 50, 10 tokens (each threshold fires once,
 * persisted in localStorage so a refresh doesn't re-ring). At 10 tokens, the
 * <RechargeModal/> blocks the UI until dismissed or recharged.
 */
import React, { useEffect, useState, useRef } from "react";
import { Bell, Zap } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "./Toast";

const THRESHOLDS = [500, 200, 100, 50, 10];
const SEEN_KEY = "aurem_token_warn_seen";

function loadSeen() {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
  } catch {
    return [];
  }
}
function saveSeen(arr) {
  localStorage.setItem(SEEN_KEY, JSON.stringify(arr));
}

function tone(tokens) {
  if (tokens <= 50) return "var(--danger)";
  if (tokens <= 200) return "var(--accent-2)";
  if (tokens <= 500) return "var(--accent)";
  return "var(--ok)";
}

const TOAST_FOR = {
  500: { kind: "warn", msg: "⚡ 500 tokens left — consider recharging." },
  200: { kind: "warn", msg: "⚠️ 200 tokens left." },
  100: { kind: "error", msg: "🔴 100 tokens left — running low!" },
  50: { kind: "error", msg: "🚨 50 tokens left!" },
  10: { kind: "error", msg: "🚨 Almost out — recharge to keep building." },
};

export default function TokenBell({ tokens, collapsed }) {
  const [ringing, setRinging] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const seenRef = useRef(loadSeen());
  const navigate = useNavigate();

  useEffect(() => {
    if (typeof tokens !== "number") return;
    // Find lowest threshold crossed that hasn't been shown yet.
    const newly = THRESHOLDS.filter(
      (t) => tokens <= t && !seenRef.current.includes(t)
    );
    if (newly.length === 0) return;
    // Fire toast for the *lowest* one crossed (most urgent)
    const lowest = Math.min(...newly);
    const t = TOAST_FOR[lowest];
    if (t) toast({ message: t.msg, kind: t.kind, duration: 4500 });
    // Mark every crossed threshold as seen so we don't keep firing.
    seenRef.current = Array.from(new Set([...seenRef.current, ...newly]));
    saveSeen(seenRef.current);
    setRinging(true);
    setTimeout(() => setRinging(false), 3000);
    if (lowest <= 10) setShowModal(true);
  }, [tokens]);

  // Reset seen list when wallet is topped up above 500 again.
  useEffect(() => {
    if (typeof tokens === "number" && tokens > 500 && seenRef.current.length > 0) {
      seenRef.current = [];
      saveSeen([]);
    }
  }, [tokens]);

  const color = tone(tokens);
  const display = typeof tokens === "number"
    ? (tokens >= 1000 ? `${Math.floor(tokens / 100) / 10}k` : tokens)
    : "—";

  return (
    <>
      <div
        data-testid="token-bell"
        onClick={() => { setRinging(false); navigate("/tokens"); }}
        title={`${tokens ?? "—"} tokens remaining`}
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: collapsed ? 32 : "auto",
          padding: collapsed ? 0 : "6px 10px",
          gap: 8,
          cursor: "pointer",
          border: "1px solid var(--border)",
          borderRadius: 4,
          background: tokens <= 50 ? "rgba(255,107,107,0.05)" : "transparent",
          transition: "border-color 120ms, background 120ms",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.borderColor = color)}
        onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
      >
        <Bell
          size={14}
          style={{
            color,
            filter: `drop-shadow(0 0 4px ${color})`,
            animation: ringing ? "bell-ring 0.5s ease infinite" : "none",
          }}
        />
        {!collapsed && (
          <span
            data-testid="token-bell-count"
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 11,
              color,
              letterSpacing: "0.05em",
              fontWeight: 600,
            }}
          >
            {display}
          </span>
        )}
        {collapsed && typeof tokens === "number" && tokens <= 500 && (
          <span
            style={{
              position: "absolute",
              top: -4,
              right: -4,
              background: color,
              color: "#0a0a0a",
              borderRadius: 999,
              fontSize: 8,
              fontWeight: 700,
              padding: "1px 4px",
              minWidth: 14,
              textAlign: "center",
              lineHeight: 1.2,
            }}
          >
            {tokens >= 1000 ? "1k" : tokens}
          </span>
        )}
      </div>

      {showModal && (
        <RechargeModal
          tokens={tokens}
          onRecharge={() => { setShowModal(false); navigate("/tokens"); }}
          onDismiss={() => setShowModal(false)}
        />
      )}
    </>
  );
}

function RechargeModal({ tokens, onRecharge, onDismiss }) {
  return (
    <div
      data-testid="recharge-modal-overlay"
      onClick={onDismiss}
      style={{
        position: "fixed", inset: 0, zIndex: 9500,
        background: "rgba(0,0,0,0.7)", backdropFilter: "blur(6px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        data-testid="recharge-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 440,
          background: "var(--panel)",
          border: "1px solid var(--danger)",
          borderRadius: 6,
          padding: 28,
          color: "var(--text)",
          textAlign: "center",
          boxShadow: "0 24px 60px -12px rgba(0,0,0,0.7), 0 0 24px -8px var(--danger)",
        }}
      >
        <Zap
          size={28}
          style={{ color: "var(--danger)", marginBottom: 12 }}
        />
        <h2 className="serif" style={{ margin: "0 0 10px", fontSize: 22 }}>
          You're almost out of tokens
        </h2>
        <p style={{ color: "var(--text-dim)", fontSize: 13, margin: "0 0 22px" }}>
          {tokens} tokens remaining. Recharge to keep building with AUREM Dev.
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
          <button
            data-testid="recharge-modal-dismiss"
            onClick={onDismiss}
            className="btn-ghost"
          >
            Continue anyway
          </button>
          <button
            data-testid="recharge-modal-cta"
            onClick={onRecharge}
            className="btn-primary"
          >
            Recharge now
          </button>
        </div>
      </div>
    </div>
  );
}
