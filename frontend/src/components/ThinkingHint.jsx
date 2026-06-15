/**
 * ThinkingHint.jsx — Iter 158
 *
 * A small, attractive pill that appears next to the chat "thinking…"
 * spinner during chat-busy states. Converts wait time into a tier-
 * aware upsell / feature highlight moment.
 *
 *   <ThinkingHint busy={isBusy} />
 *
 * Behaviour
 * ─────────
 * - Polls `/thinking-hint` ONCE per busy-cycle (when busy flips
 *   false→true). No streaming, no re-renders during the wait.
 * - Founder tier returns null hint → component renders nothing.
 * - `enabled=false` from admin config → component renders nothing.
 * - `delay_ms` from admin config controls slide-in lead time
 *   (default 600ms; sub-second replies never flash the pill).
 * - If `cta_link` starts with `stripe:<tier>` (e.g. `stripe:starter`),
 *   the CTA opens Stripe checkout for that tier directly — one click
 *   instead of three (settings → billing → checkout).
 * - Hover lifts the card and reveals the CTA accent.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

export default function ThinkingHint({ busy }) {
  const [hint, setHint] = useState(null);
  const [show, setShow] = useState(false);
  const [paying, setPaying] = useState(false);
  const fetchedRef = useRef(false);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!busy) {
      clearTimeout(timerRef.current);
      setShow(false);
      fetchedRef.current = false;
      return;
    }
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get("/thinking-hint");
        if (cancelled) return;
        if (r.data?.enabled === false) return;     // admin kill-switch
        if (!r.data?.hint) return;                  // founder tier / no row
        setHint(r.data.hint);
        const delayMs = Number.isFinite(r.data.delay_ms)
          ? Math.max(200, Math.min(5000, r.data.delay_ms))
          : 600;
        timerRef.current = setTimeout(() => setShow(true), delayMs);
      } catch {
        // Silent — upsell hint is not a critical surface.
      }
    })();
    return () => { cancelled = true; clearTimeout(timerRef.current); };
  }, [busy]);

  if (!busy || !hint || !show) return null;

  const isStripeCta = (hint.cta_link || "").startsWith("stripe:");
  const stripeTier = isStripeCta
    ? hint.cta_link.slice("stripe:".length).trim()
    : null;

  async function handleStripeClick(e) {
    e.preventDefault();
    if (!stripeTier || paying) return;
    setPaying(true);
    try {
      const r = await api.post("/payments/checkout", {
        tier: stripeTier,
        origin_url: window.location.origin,
      });
      const url = r.data?.url || r.data?.checkout_url;
      if (url) window.location.href = url;
      else setPaying(false);
    } catch {
      setPaying(false);
    }
  }

  return (
    <div
      data-testid="thinking-hint"
      data-hint-id={hint.hint_id}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 14px 8px 12px",
        margin: "10px 0",
        borderRadius: 12,
        background:
          "linear-gradient(135deg, rgba(255,138,42,0.08) 0%, rgba(255,197,96,0.04) 100%)",
        border: "1px solid rgba(255,138,42,0.20)",
        boxShadow: "0 0 18px -10px rgba(255,138,42,0.55)",
        animation: "thinkingHintIn 360ms cubic-bezier(.2,.8,.25,1) both",
        maxWidth: 480,
      }}
    >
      <span
        aria-hidden
        style={{
          position: "absolute",
          left: 0, top: 0, bottom: 0,
          width: 3,
          borderRadius: "12px 0 0 12px",
          background:
            "linear-gradient(180deg, var(--accent, #ff8a2a), var(--accent-2, #ffc560))",
          boxShadow: "0 0 12px -2px var(--accent, #ff8a2a)",
        }}
      />
      {hint.emoji && (
        <span style={{ fontSize: 18, flexShrink: 0, marginLeft: 4 }}>
          {hint.emoji}
        </span>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          data-testid="thinking-hint-headline"
          style={{
            fontSize: 13, fontWeight: 600,
            color: "var(--text, #f4ecdc)",
            letterSpacing: "0.01em", lineHeight: 1.25,
          }}
        >
          {hint.headline}
        </div>
        <div
          data-testid="thinking-hint-body"
          style={{
            fontSize: 11, color: "var(--text-dim, #a39d8a)",
            lineHeight: 1.4, marginTop: 2,
          }}
        >
          {hint.body}
        </div>
      </div>
      {hint.cta_text && hint.cta_link && (
        <a
          data-testid="thinking-hint-cta"
          href={isStripeCta ? "#" : hint.cta_link}
          onClick={isStripeCta ? handleStripeClick : undefined}
          aria-disabled={paying}
          style={{
            flexShrink: 0,
            fontSize: 11, fontWeight: 600,
            letterSpacing: "0.05em",
            padding: "5px 10px", borderRadius: 8,
            color: "var(--accent-2, #ffc560)",
            background: "rgba(255,138,42,0.08)",
            border: "1px solid rgba(255,138,42,0.35)",
            textDecoration: "none",
            transition: "background 160ms, color 160ms, transform 160ms",
            whiteSpace: "nowrap",
            cursor: paying ? "wait" : "pointer",
            opacity: paying ? 0.6 : 1,
          }}
          onMouseEnter={(e) => {
            if (paying) return;
            e.currentTarget.style.background = "rgba(255,138,42,0.18)";
            e.currentTarget.style.color = "#fff";
            e.currentTarget.style.transform = "translateY(-1px)";
          }}
          onMouseLeave={(e) => {
            if (paying) return;
            e.currentTarget.style.background = "rgba(255,138,42,0.08)";
            e.currentTarget.style.color = "var(--accent-2, #ffc560)";
            e.currentTarget.style.transform = "translateY(0)";
          }}
        >
          {paying ? "Opening checkout…" : `${hint.cta_text} →`}
        </a>
      )}
      <style>{`
        @keyframes thinkingHintIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
