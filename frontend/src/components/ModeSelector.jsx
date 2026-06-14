/**
 * ModeSelector — Iter 153.
 *
 * Renders three pills (Swift / Pro / Maxx) inside the composer toolbar.
 * Locked pills open an upgrade popup that calls /payments/checkout
 * with the plan key that unlocks the chosen mode. The locked icon
 * (lucide `Lock`) replaces the mode glyph until the user upgrades.
 *
 * Backed by GET /chat/modes/available which returns
 * { tier, modes: { swift|pro|maxx: { label, desc, price, min_tier, unlocked } } }.
 */
import React, { useState, useEffect } from "react";
import { Zap, Search, Rocket, Lock } from "lucide-react";
import { api } from "../lib/api";

const ICONS = { swift: Zap, pro: Search, maxx: Rocket };

export default function ModeSelector({ value, onChange }) {
  const [modes, setModes] = useState(null);
  const [popup, setPopup] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.get("/chat/modes/available")
      .then((r) => { if (!cancelled) setModes(r.data?.modes || null); })
      .catch(() => { /* silent — selector simply hides */ });
    return () => { cancelled = true; };
  }, []);

  if (!modes) return null;

  return (
    <>
      <div
        data-testid="mode-selector"
        style={{ display: "flex", gap: 4, alignItems: "center" }}
      >
        {["swift", "pro", "maxx"].map((key) => {
          const m = modes[key];
          const Icon = ICONS[key];
          const active = value === key;
          const locked = !m?.unlocked;
          return (
            <button
              key={key}
              type="button"
              data-testid={`mode-pill-${key}`}
              onClick={() => locked ? setPopup(key) : onChange(key)}
              title={m?.desc}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "4px 9px", borderRadius: 6,
                border: active
                  ? "1px solid var(--accent, #f59e0b)"
                  : "1px solid var(--border)",
                background: active
                  ? "var(--accent-soft, rgba(245,158,11,0.12))"
                  : "transparent",
                color: locked
                  ? "var(--text-faint)"
                  : active
                    ? "var(--accent-2, #ffb347)"
                    : "var(--text-dim)",
                cursor: "pointer",
                fontSize: 11,
                fontWeight: active ? 600 : 400,
              }}
            >
              {locked ? <Lock size={11} /> : <Icon size={12} />}
              {m?.label}
            </button>
          );
        })}
      </div>
      {popup && (
        <UpgradePopup
          mode={popup}
          data={modes[popup]}
          onClose={() => setPopup(null)}
        />
      )}
    </>
  );
}

function UpgradePopup({ mode, data, onClose }) {
  const [paying, setPaying] = useState(false);
  const pay = async () => {
    setPaying(true);
    try {
      const r = await api.post("/payments/checkout", { plan: data.min_tier });
      if (r.data?.checkout_url) {
        window.location.href = r.data.checkout_url;
        return;
      }
    } catch (_) { /* fallthrough */ }
    setPaying(false);
  };
  return (
    <div
      data-testid="upgrade-popup-overlay"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
        zIndex: 9000, display: "flex",
        alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        data-testid="upgrade-popup"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--panel)",
          border: "1px solid var(--border-strong)",
          borderRadius: 12, padding: 28, maxWidth: 380, width: "90%",
          color: "var(--text)",
        }}
      >
        <div style={{
          fontSize: 18, fontWeight: 600, marginBottom: 8,
          color: "var(--accent-2, #f59e0b)",
        }}>
          Unlock {data.label} mode
        </div>
        <p style={{
          fontSize: 13, color: "var(--text-dim)",
          lineHeight: 1.6, marginBottom: 16,
        }}>
          {data.desc}
        </p>
        <div style={{
          background: "var(--bg-elev, rgba(255,255,255,0.04))",
          borderRadius: 8, padding: 14, marginBottom: 16,
        }}>
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
            Requires {data.min_tier} plan
          </div>
          <div style={{
            fontSize: 24, fontWeight: 700,
            color: "var(--accent-2, #f59e0b)",
          }}>
            {data.price}
            <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
              /month
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button
            data-testid="upgrade-popup-cancel"
            onClick={onClose}
            style={{
              flex: 1, padding: 10, borderRadius: 8,
              border: "1px solid var(--border)",
              background: "transparent",
              color: "var(--text-dim)", cursor: "pointer",
            }}
          >Maybe later</button>
          <button
            data-testid="upgrade-popup-pay"
            onClick={pay}
            disabled={paying}
            style={{
              flex: 1, padding: 10, borderRadius: 8, border: "none",
              background: "var(--accent, #f59e0b)",
              color: "#000", cursor: "pointer", fontWeight: 600,
            }}
          >{paying ? "Loading…" : "Upgrade now"}</button>
        </div>
      </div>
    </div>
  );
}
