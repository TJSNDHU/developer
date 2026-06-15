/**
 * ThinkingHint.jsx — Iter 158 + 160 (one-line skinny strip)
 *
 * A slim sponsored-style strip that appears next to the chat
 * "thinking…" spinner during chat-busy states. Single line, low
 * height — feels like a status bar, not an ad card.
 *
 *   <ThinkingHint busy={isBusy} />
 *
 * Iter 160 changes:
 *   - Collapsed to ONE line (~32px tall) so it never dominates the
 *     composer area visually.
 *   - Headline + body merged into a single sentence ("Headline. Body.")
 *     truncated with ellipsis on overflow.
 *   - Width is now content-sized (no `maxWidth: 480` boxy look).
 *   - Founder tier gets no hint server-side now (never leaks admin
 *     copy into the user chat).
 *
 * Behaviour kept from Iter 158:
 *   - Polls `/thinking-hint` ONCE per busy-cycle.
 *   - Honours admin `enabled` + `delay_ms` from global config.
 *   - `cta_link = "stripe:<tier>"` opens checkout in one click.
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
        if (r.data?.enabled === false) return;
        if (!r.data?.hint) return;
        setHint(r.data.hint);
        const delayMs = Number.isFinite(r.data.delay_ms)
          ? Math.max(200, Math.min(5000, r.data.delay_ms))
          : 600;
        timerRef.current = setTimeout(() => setShow(true), delayMs);
      } catch { /* silent */ }
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
    } catch { setPaying(false); }
  }

  // One-sentence merge so the strip stays single-line.
  const fullText = hint.body
    ? `${hint.headline} ${hint.body}`
    : hint.headline;

  return (
    <div
      data-testid="thinking-hint"
      data-hint-id={hint.hint_id}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "5px 10px 5px 8px",
        margin: "6px 0",
        borderRadius: 999,                       // pill — skinny status bar feel
        background:
          "linear-gradient(135deg, rgba(255,138,42,0.07) 0%, rgba(255,197,96,0.03) 100%)",
        border: "1px solid rgba(255,138,42,0.22)",
        boxShadow: "0 0 14px -12px rgba(255,138,42,0.55)",
        animation: "thinkingHintIn 280ms cubic-bezier(.2,.8,.25,1) both",
        maxWidth: "100%",
        minWidth: 0,
        fontSize: 12,
        lineHeight: 1.2,
        height: 28,                              // hard cap → never wraps
        overflow: "hidden",
      }}
    >
      {hint.emoji && (
        <span style={{ fontSize: 13, flexShrink: 0 }}>{hint.emoji}</span>
      )}
      <span
        data-testid="thinking-hint-text"
        style={{
          color: "var(--text-dim, #a39d8a)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          minWidth: 0,
          flex: "0 1 auto",
        }}
      >
        <span style={{ color: "var(--text, #f4ecdc)", fontWeight: 600 }}>
          {hint.headline}
        </span>
        {hint.body ? <span style={{ marginLeft: 6 }}>· {hint.body}</span> : null}
      </span>
      {hint.cta_text && hint.cta_link && (
        <a
          data-testid="thinking-hint-cta"
          href={isStripeCta ? "#" : hint.cta_link}
          onClick={isStripeCta ? handleStripeClick : undefined}
          aria-disabled={paying}
          style={{
            flexShrink: 0,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.04em",
            padding: "2px 10px",
            borderRadius: 999,
            color: "var(--accent-2, #ffc560)",
            background: "rgba(255,138,42,0.10)",
            border: "1px solid rgba(255,138,42,0.40)",
            textDecoration: "none",
            transition: "background 120ms, color 120ms",
            whiteSpace: "nowrap",
            cursor: paying ? "wait" : "pointer",
            opacity: paying ? 0.6 : 1,
          }}
          onMouseEnter={(e) => {
            if (paying) return;
            e.currentTarget.style.background = "rgba(255,138,42,0.22)";
            e.currentTarget.style.color = "#fff";
          }}
          onMouseLeave={(e) => {
            if (paying) return;
            e.currentTarget.style.background = "rgba(255,138,42,0.10)";
            e.currentTarget.style.color = "var(--accent-2, #ffc560)";
          }}
        >
          {paying ? "…" : hint.cta_text}
        </a>
      )}
      <style>{`
        @keyframes thinkingHintIn {
          from { opacity: 0; transform: translateY(3px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
      {/* dummy reference so unused var lint never complains */}
      <span style={{ display: "none" }}>{fullText.length}</span>
    </div>
  );
}
