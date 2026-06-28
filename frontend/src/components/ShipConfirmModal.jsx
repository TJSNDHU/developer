/**
 * ShipConfirmModal.jsx — Iter 212m-86 BUG 5 fix
 *
 * Dark-overlay confirmation modal shown when the user clicks "Ship via
 * CTO" on a code reply. Matches the v0 design at
 * https://sidebar-changes.vercel.app — Files changed list + Vanguard
 * clean badge + Cancel / Ship it.
 *
 * Wire-up (event-driven so any component can trigger it):
 *
 *   // open
 *   window.dispatchEvent(new CustomEvent("aurem:open-ship-modal", {
 *     detail: {
 *       files: [{ path: "backend/auth_middleware.py", added: 47, removed: 12 }],
 *       vanguard: { critical: 0 },
 *       onShip: () => { … real ship handler … },
 *     },
 *   }));
 *
 * Auto-closes on Cancel / Esc / Ship-it click. Mounted once at Dashboard
 * level so any code path that wants confirmation just dispatches.
 */
import React, { useEffect, useState } from "react";
import { CheckCircle2, FileText, X } from "lucide-react";

export default function ShipConfirmModal() {
  const [open, setOpen]       = useState(false);
  const [files, setFiles]     = useState([]);
  const [vanguard, setVan]    = useState({ critical: 0 });
  const [onShip, setOnShip]   = useState(() => () => {});
  const [shipping, setShipping] = useState(false);

  useEffect(() => {
    const handler = (e) => {
      const d = e?.detail || {};
      setFiles(Array.isArray(d.files) ? d.files : []);
      setVan(d.vanguard || { critical: 0 });
      setOnShip(() => (typeof d.onShip === "function" ? d.onShip : () => {}));
      setOpen(true);
    };
    window.addEventListener("aurem:open-ship-modal", handler);
    return () => window.removeEventListener("aurem:open-ship-modal", handler);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;

  const handleShip = async () => {
    if (shipping) return;
    setShipping(true);
    try { await onShip(); } catch { /* caller handles */ }
    setShipping(false);
    setOpen(false);
  };

  const clean = (vanguard?.critical || 0) === 0;

  return (
    <div
      data-testid="ship-modal-overlay"
      onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
      style={{
        position: "fixed", inset: 0, zIndex: 100,
        background: "rgba(0,0,0,0.78)", backdropFilter: "blur(6px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        data-testid="ship-modal"
        style={{
          width: "min(480px, 100%)",
          background: "#161616",
          border: "1px solid #222",
          borderRadius: 12,
          padding: 24,
          boxShadow: "0 24px 60px rgba(0,0,0,0.6)",
          fontFamily: "'Jost', system-ui, sans-serif",
          color: "#F5F5F5",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 18,
        }}>
          <h2 style={{
            fontSize: 18, fontWeight: 700, margin: 0,
            letterSpacing: "-0.01em",
          }}>Ship via CTO</h2>
          <button
            data-testid="ship-modal-close"
            onClick={() => setOpen(false)}
            aria-label="Close"
            style={{
              background: "transparent", border: 0, color: "#8A8A8A",
              cursor: "pointer", padding: 4, borderRadius: 4,
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}
          ><X size={16} /></button>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{
            fontSize: 10, color: "#8A8A8A", textTransform: "uppercase",
            letterSpacing: "0.12em", marginBottom: 8,
            fontFamily: "'JetBrains Mono', monospace",
          }}>Files changed ({files.length})</div>
          {files.length === 0 ? (
            <div style={{ fontSize: 12, color: "#666", fontStyle: "italic" }}>
              No changes detected
            </div>
          ) : (
            <ul style={{
              listStyle: "none", padding: 0, margin: 0,
              display: "grid", gap: 6,
            }}>
              {files.slice(0, 8).map((f, i) => (
                <li key={i} style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "8px 10px", borderRadius: 6,
                  background: "#0A0A0A", border: "1px solid #222",
                  fontSize: 12,
                }}>
                  <FileText size={12} style={{ color: "#FF6608", flex: "0 0 auto" }} />
                  <span style={{
                    flex: 1, fontFamily: "'JetBrains Mono', monospace",
                    color: "#F5F5F5", overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>{f.path || f.file || "(unnamed)"}</span>
                  {(f.added != null || f.removed != null) && (
                    <span style={{
                      fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
                    }}>
                      <span style={{ color: "#22C55E" }}>+{f.added ?? 0}</span>
                      <span style={{ color: "#8A8A8A" }}>{" / "}</span>
                      <span style={{ color: "#EF4444" }}>−{f.removed ?? 0}</span>
                    </span>
                  )}
                </li>
              ))}
              {files.length > 8 && (
                <li style={{ fontSize: 11, color: "#8A8A8A", paddingLeft: 10 }}>
                  +{files.length - 8} more
                </li>
              )}
            </ul>
          )}
        </div>

        <div data-testid="ship-vanguard-badge" style={{
          display: "inline-flex", alignItems: "center", gap: 8,
          padding: "8px 12px", borderRadius: 999,
          background: clean
            ? "rgba(34,197,94,0.12)"
            : "rgba(239,68,68,0.12)",
          color: clean ? "#22C55E" : "#EF4444",
          fontSize: 11, fontWeight: 600,
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: "0.05em",
          marginBottom: 22,
        }}>
          <CheckCircle2 size={12} />
          Vanguard {clean ? "clean" : `flagged ${vanguard.critical} critical`} ·{" "}
          {vanguard?.critical ?? 0} critical
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button
            data-testid="ship-modal-cancel"
            onClick={() => setOpen(false)}
            disabled={shipping}
            style={{
              padding: "9px 16px", fontSize: 13, fontWeight: 600,
              background: "transparent", color: "#F5F5F5",
              border: "1px solid #333", borderRadius: 6,
              cursor: "pointer",
            }}
          >Cancel</button>
          <button
            data-testid="ship-modal-confirm"
            onClick={handleShip}
            disabled={shipping}
            style={{
              padding: "9px 18px", fontSize: 13, fontWeight: 700,
              background: "#FF6608", color: "#0A0A0A",
              border: 0, borderRadius: 6,
              cursor: shipping ? "wait" : "pointer",
              opacity: shipping ? 0.6 : 1,
            }}
          >{shipping ? "Shipping…" : "Ship it"}</button>
        </div>
      </div>
    </div>
  );
}
