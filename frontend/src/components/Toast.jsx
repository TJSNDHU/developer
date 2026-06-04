/**
 * Toast.jsx — Minimal toast system. Use via window.dispatchEvent + <Toaster/>.
 */
import React, { useEffect, useState } from "react";

let _id = 0;

export function toast({ message, kind = "info", duration = 3500, onClick = null }) {
  window.dispatchEvent(
    new CustomEvent("aurem:toast", {
      detail: { id: ++_id, message, kind, duration, onClick },
    })
  );
}

export default function Toaster() {
  const [list, setList] = useState([]);

  useEffect(() => {
    function onToast(e) {
      const t = e.detail;
      setList((cur) => [...cur, t]);
      setTimeout(() => {
        setList((cur) => cur.filter((x) => x.id !== t.id));
      }, t.duration);
    }
    window.addEventListener("aurem:toast", onToast);
    return () => window.removeEventListener("aurem:toast", onToast);
  }, []);

  return (
    <div
      data-testid="toaster"
      style={{
        position: "fixed",
        bottom: 24,
        right: 24,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 9999,
        pointerEvents: "none",
      }}
    >
      {list.map((t) => {
        const palette = {
          info: { bg: "var(--panel)", border: "var(--border-strong)", color: "var(--text)" },
          success: { bg: "rgba(109,212,161,0.08)", border: "rgba(109,212,161,0.35)", color: "var(--ok)" },
          error: { bg: "rgba(255,107,107,0.08)", border: "rgba(255,107,107,0.35)", color: "var(--danger)" },
          warn: { bg: "rgba(255,197,96,0.08)", border: "rgba(255,197,96,0.35)", color: "var(--accent-2)" },
        }[t.kind] || {};
        return (
          <div
            key={t.id}
            data-testid={`toast-${t.kind}`}
            onClick={() => {
              if (t.onClick) {
                try { t.onClick(); } catch { /* ignore */ }
                setList((cur) => cur.filter((x) => x.id !== t.id));
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
            }}
          >
            {t.message}
          </div>
        );
      })}
      <style>{`
        @keyframes toastIn {
          from { transform: translateY(8px); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
