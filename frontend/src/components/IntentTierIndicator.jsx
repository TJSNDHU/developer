/**
 * IntentTierIndicator.jsx — Iter 212m-149
 *
 * Read-only 8 px tier-dot + label that replaces the binary
 * `LoopModeToggle`.  Driven entirely by the Intent Gateway —
 * NOT a toggle.
 *
 * States rendered (founder spec):
 *   casual  → small gray dot   ("CASUAL"  · muted text)
 *   query   → small amber dot  ("QUERY"   · amber text)
 *   agentic → small orange dot ("AGENTIC" · orange text)
 *   clarify → small yellow dot ("CLARIFY" · yellow text)
 *
 * The tier is computed two ways:
 *   1. LIVE preview as the user types — hits
 *      `POST /chat/classify-intent` (heuristic-only, <5 ms).
 *      Debounced to 220 ms so we don't spam the endpoint.
 *   2. After a turn — pulled from the SSE `intent` frame.
 *      The parent passes `lastTier` so the dot stays sticky
 *      after the reply lands.
 *
 * Subtle by design — does not steal attention.
 */
import React, { useEffect, useState, useRef } from "react";
import { api } from "../lib/api";

const TIER_THEME = {
  casual:  { dot: "#94a3b8", color: "#94a3b8", label: "CASUAL" },
  query:   { dot: "#fbbf24", color: "#fde68a", label: "QUERY" },
  agentic: { dot: "#fb923c", color: "#fdba74", label: "AGENTIC" },
  clarify: { dot: "#fde047", color: "#fef08a", label: "CLARIFY" },
};

function tierTheme(tier) { return TIER_THEME[tier] || TIER_THEME.casual; }

export default function IntentTierIndicator({ liveText, lastTier }) {
  const [tier, setTier] = useState(lastTier || null);
  const [conf, setConf] = useState(null);
  const debTimer = useRef(null);
  const inFlight = useRef(false);

  // Pull last-known tier whenever the parent updates it.
  useEffect(() => {
    if (lastTier) {
      setTier(lastTier);
      setConf(null);
    }
  }, [lastTier]);

  // Live classification as the user types.
  useEffect(() => {
    const text = (liveText || "").trim();
    // Idle when composer is empty — show last-known tier instead.
    if (!text) {
      if (debTimer.current) clearTimeout(debTimer.current);
      return undefined;
    }
    if (debTimer.current) clearTimeout(debTimer.current);
    debTimer.current = setTimeout(async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const r = await api.post("/chat/classify-intent", { message: text });
        if (r?.data?.ok) {
          setTier(r.data.tier);
          setConf(r.data.confidence);
        }
      } catch { /* silent — UI is a hint, not a hard contract */ }
      finally { inFlight.current = false; }
    }, 220);
    return () => { if (debTimer.current) clearTimeout(debTimer.current); };
  }, [liveText]);

  // Iter 281 follow-up — never return null. Same graceful-degradation
  // rule that fixed LoopLiveFeed: an empty tier used to make the entire
  // dot+label disappear from the composer toolbar, which broke the
  // CSS sibling selectors in index.css:666-667 that anchor the
  // LoopModeToggle position. Default to `casual` when nothing has
  // been classified yet — tierTheme() already falls back there too.
  const activeTier = tier || "casual";
  const theme = tierTheme(activeTier);
  const confLabel = typeof conf === "number" ? ` · ${(conf * 100).toFixed(0)}%` : "";

  return (
    <div
      data-testid="intent-tier-indicator"
      data-tier={activeTier}
      data-pending={tier ? undefined : "true"}
      title={`Intent: ${theme.label}${confLabel} (read-only — Gateway picks the path)`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "0 8px", height: 28, borderRadius: 999,
        background: "rgba(255,255,255,0.03)",
        border: "1px solid rgba(255,255,255,0.06)",
        fontSize: 9, fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.08em", color: theme.color,
        cursor: "default", userSelect: "none",
      }}
    >
      <span
        data-testid="intent-tier-dot"
        style={{
          width: 8, height: 8, borderRadius: 999,
          background: theme.dot,
          boxShadow: tier === "agentic" ? `0 0 6px ${theme.dot}` : "none",
        }}
      />
      <span data-testid="intent-tier-label">{theme.label}</span>
    </div>
  );
}
