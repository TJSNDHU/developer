/**
 * Toast.jsx — Minimal toast system. Use via window.dispatchEvent + <Toaster/>.
 *
 * Iter 212m-217 — Added persistent + countdown + actions support so
 * `/scan` slash-command rate-limit handling can render a live "retry
 * in Ns" toast with a Cancel button and auto-retry when the timer
 * hits zero.
 *
 *   toast({ message, kind, duration, onClick,
 *           id, persistent, countdown, onExpire, actions })
 *     → returns the toast id (auto-generated when omitted) so callers
 *       can dismiss or update it later.
 *
 *   dismissToast(id)   — imperative dismiss.
 *
 * `persistent: true` disables the auto-dismiss timer.
 * `countdown: N`      renders "N s" that ticks down live; when it
 *                     reaches 0 the toast dismisses and (if provided)
 *                     `onExpire` fires.  `actions` is a `[{label,
 *                     onClick}]` array rendered inline (used for
 *                     "Cancel" / "Retry now" buttons).
 */
import React, { useEffect, useState } from "react";

let _id = 0;

export function toast({
  message,
  kind = "info",
  duration = 3500,
  onClick = null,
  id = null,
  persistent = false,
  countdown = null,
  onExpire = null,
  actions = null,
}) {
  const toastId = id ?? ++_id;
  window.dispatchEvent(
    new CustomEvent("aurem:toast", {
      detail: {
        id: toastId, message, kind, duration, onClick,
        persistent, countdown, onExpire, actions,
      },
    })
  );
  return toastId;
}

export function dismissToast(id) {
  window.dispatchEvent(
    new CustomEvent("aurem:toast-dismiss", { detail: { id } })
  );
}

export default function Toaster() {
  const [list, setList] = useState([]);

  useEffect(() => {
    function onToast(e) {
      const t = e.detail;
      setList((cur) => {
        // If a toast with the same id exists, replace it (update).
        const filtered = cur.filter((x) => x.id !== t.id);
        return [...filtered, t];
      });
      if (!t.persistent && !t.countdown) {
        setTimeout(() => {
          setList((cur) => cur.filter((x) => x.id !== t.id));
        }, t.duration);
      }
    }
    function onDismiss(e) {
      const { id } = e.detail || {};
      setList((cur) => cur.filter((x) => x.id !== id));
    }
    window.addEventListener("aurem:toast", onToast);
    window.addEventListener("aurem:toast-dismiss", onDismiss);
    return () => {
      window.removeEventListener("aurem:toast", onToast);
      window.removeEventListener("aurem:toast-dismiss", onDismiss);
    };
  }, []);

  return (
    <div
      data-testid="toaster"
      className="aurem-toaster"
      style={{
        position: "fixed",
        top: 72,
        right: "calc(24px + var(--advisor-w, 0px))",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 9999,
        pointerEvents: "none",
      }}
    >
      <style>{`
        @media (max-width: 480px) {
          .aurem-toaster {
            top: 88px !important;
            right: 12px !important;
            left: 12px !important;
            max-width: calc(100vw - 24px) !important;
          }
        }
      `}</style>
      {list.map((t) => (
        <ToastItem
          key={t.id}
          t={t}
          onDismiss={() =>
            setList((cur) => cur.filter((x) => x.id !== t.id))
          }
        />
      ))}
      <style>{`
        @keyframes toastIn {
          from { transform: translateY(8px); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}

function ToastItem({ t, onDismiss }) {
  const palette = {
    info:    { bg: "var(--panel)", border: "var(--border-strong)", color: "var(--text)" },
    success: { bg: "rgba(109,212,161,0.08)", border: "rgba(109,212,161,0.35)", color: "var(--ok)" },
    error:   { bg: "rgba(255,107,107,0.08)", border: "rgba(255,107,107,0.35)", color: "var(--danger)" },
    warn:    { bg: "rgba(255,197,96,0.08)",  border: "rgba(255,197,96,0.35)",  color: "var(--accent-2)" },
  }[t.kind] || {};

  const [secondsLeft, setSecondsLeft] = useState(
    typeof t.countdown === "number" ? t.countdown : null
  );

  // Reset countdown when the toast is updated with a new value.
  useEffect(() => {
    if (typeof t.countdown === "number") {
      setSecondsLeft(t.countdown);
    }
  }, [t.countdown]);

  useEffect(() => {
    if (secondsLeft == null) return;
    if (secondsLeft <= 0) {
      // Fire expire callback then dismiss.
      try { if (t.onExpire) t.onExpire(); } catch { /* swallow */ }
      onDismiss();
      return;
    }
    const timer = setTimeout(() => setSecondsLeft((s) => (s == null ? null : s - 1)), 1000);
    return () => clearTimeout(timer);
  }, [secondsLeft]);

  return (
    <div
      data-testid={`toast-${t.kind}`}
      onClick={() => {
        if (t.onClick) {
          try { t.onClick(); } catch { /* ignore */ }
          onDismiss();
        }
      }}
      style={{
        padding: "10px 14px",
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderRadius: 4,
        color: palette.color,
        fontSize: 13,
        fontFamily: "'Jost', system-ui, sans-serif",
        maxWidth: 380,
        boxShadow: "0 8px 24px -8px rgba(0,0,0,0.5)",
        animation: "toastIn 220ms cubic-bezier(0.4,0,0.2,1)",
        cursor: t.onClick ? "pointer" : "default",
        pointerEvents: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ flex: 1 }}>{t.message}</span>
        {secondsLeft != null && (
          <span
            data-testid={`toast-countdown-${t.id}`}
            style={{
              fontVariantNumeric: "tabular-nums",
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: 999,
              background: "rgba(255,255,255,0.06)",
              border: "1px solid rgba(255,255,255,0.12)",
              color: "var(--text)",
              fontSize: 12,
            }}
          >
            {secondsLeft}s
          </span>
        )}
      </div>
      {Array.isArray(t.actions) && t.actions.length > 0 && (
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          {t.actions.map((a, idx) => (
            <button
              key={idx}
              data-testid={`toast-action-${t.id}-${(a.label || "")
                .toLowerCase()
                .replace(/\s+/g, "-")}`}
              onClick={(ev) => {
                ev.stopPropagation();
                try { if (a.onClick) a.onClick(); } catch { /* ignore */ }
                if (a.dismiss !== false) onDismiss();
              }}
              style={{
                background: a.primary ? "var(--accent)" : "transparent",
                color: a.primary ? "#000" : "var(--text)",
                border: a.primary
                  ? "1px solid var(--accent)"
                  : "1px solid var(--border-strong)",
                padding: "4px 10px",
                borderRadius: 4,
                fontSize: 12,
                fontFamily: "inherit",
                cursor: "pointer",
                fontWeight: 500,
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
