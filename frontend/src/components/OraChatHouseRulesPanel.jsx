/**
 * components/OraChatHouseRulesPanel.jsx — Iter 212m-239
 *
 * Slide-over panel opened from the ORA Chat drawer header. Lets the
 * founder edit the "house rules" text that layers on top of AUREM's
 * base system prompt (never overriding CORE_SAFETY_RULES — enforced
 * architecturally in the backend `assemble_system_prompt`).
 */
import React, { useEffect, useState } from "react";
import { X, RotateCcw, AlertTriangle } from "lucide-react";
import { api } from "../lib/api";

const MAX_LEN = 2000;

export default function OraChatHouseRulesPanel({ onClose }) {
  const [text, setText]         = useState("");
  const [defaultText, setDT]    = useState("");
  const [history, setHistory]   = useState([]);
  const [warning, setWarning]   = useState(null);
  const [savedAt, setSavedAt]   = useState(null);
  const [busy, setBusy]         = useState(false);

  const load = async () => {
    try {
      const r = await api.get("/ora-chat/house-rules");
      setText(r.data.current?.rules_text || r.data.effective_text || "");
      setDT(r.data.default_text || "");
      const h = await api.get("/ora-chat/house-rules/history");
      setHistory(h.data.history || []);
    } catch { /* auth failed — drawer will close */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (text.length > MAX_LEN || busy) return;
    setBusy(true);
    try {
      const r = await api.put("/ora-chat/house-rules", { rules_text: text });
      setWarning(r.data.soft_warning);
      setSavedAt(Date.now());
      await load();
    } finally { setBusy(false); }
  };

  const restore = async (v) => {
    setBusy(true);
    try {
      await api.post(`/ora-chat/house-rules/restore/${v}`);
      await load();
      setWarning(null);
    } finally { setBusy(false); }
  };

  const resetToDefault = async () => { setText(defaultText); };

  const over = text.length > MAX_LEN;

  return (
    <div
      data-testid="ora-chat-house-rules"
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        display: "flex", alignItems: "stretch", justifyContent: "flex-end",
      }}
    >
      <div onClick={onClose}
           style={{ flex: 1, background: "rgba(0,0,0,0.5)" }} />
      <div style={{
        width: "min(520px, 96vw)", background: "#0f1113", color: "#e8e3d3",
        display: "flex", flexDirection: "column",
        borderLeft: "1px solid rgba(255,255,255,0.06)",
        boxShadow: "-12px 0 40px rgba(0,0,0,0.5)",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}>
        <div style={{
          padding: "16px 18px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <div style={{ flex: 1, fontSize: 14, fontWeight: 600 }}>
            ORA Chat — House Rules
          </div>
          <button data-testid="hr-close" onClick={onClose}
                  style={{ background: "transparent", border: "none",
                            color: "#a39d8a", cursor: "pointer", padding: 6 }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: 18, overflow: "auto", flex: 1 }}>
          <textarea
            data-testid="hr-textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={7}
            placeholder="Style / behavior preferences (safety rules cannot be overridden)"
            style={{
              width: "100%", boxSizing: "border-box",
              padding: 12,
              background: "rgba(255,255,255,0.03)",
              border: `1px solid ${over ? "#f88" : "rgba(255,255,255,0.08)"}`,
              borderRadius: 8, color: "#e8e3d3",
              fontFamily: "ui-monospace, monospace", fontSize: 12,
              lineHeight: 1.5, resize: "vertical",
              outline: "none",
            }}
          />
          <div style={{ display: "flex", justifyContent: "space-between",
                          marginTop: 6, fontSize: 11,
                          color: over ? "#f88" : "#7a7466" }}>
            <span>These rules guide ORA&apos;s tone and behavior. They can&apos;t override
              built-in safety limits (like never treating web content as commands,
              or DB access rules).</span>
            <span data-testid="hr-counter">{text.length} / {MAX_LEN}</span>
          </div>

          {warning && (
            <div data-testid="hr-warning" style={{
              marginTop: 12, padding: 10, borderRadius: 6,
              background: "rgba(220,150,80,0.10)",
              border: "1px solid rgba(220,150,80,0.3)",
              color: "#f4a082", fontSize: 12,
              display: "flex", gap: 8, alignItems: "flex-start",
            }}>
              <AlertTriangle size={14} /> {warning}
            </div>
          )}
          {savedAt && !warning && (
            <div style={{ marginTop: 12, fontSize: 11, color: "#7a7466" }}>
              Saved. Next message uses the new rules.
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button data-testid="hr-save" onClick={save} disabled={over || busy}
                    style={{
                      padding: "10px 16px", border: "none", borderRadius: 6,
                      background: over || busy ? "#3a3428" : "#E07A5F",
                      color: "#0a0a0a", cursor: over || busy ? "not-allowed" : "pointer",
                      fontSize: 12, fontWeight: 600,
                    }}>
              Save
            </button>
            <button data-testid="hr-reset" onClick={resetToDefault} disabled={busy}
                    style={{
                      padding: "10px 12px", border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: 6, background: "transparent",
                      color: "#a39d8a", cursor: "pointer",
                      fontSize: 12, display: "flex", gap: 6, alignItems: "center",
                    }}>
              <RotateCcw size={12} /> Reset to default
            </button>
          </div>

          <div style={{ marginTop: 24 }}>
            <div style={{ fontSize: 11, textTransform: "uppercase",
                            letterSpacing: 1, color: "#a39d8a",
                            marginBottom: 10 }}>
              Recent versions
            </div>
            {history.length === 0 && (
              <div style={{ fontSize: 12, color: "#7a7466" }}>
                No saved versions yet.
              </div>
            )}
            {history.map((v) => (
              <div key={v.id}
                   data-testid={`hr-version-${v.version}`}
                   style={{
                     padding: 10, marginBottom: 8, borderRadius: 6,
                     background: v.active
                       ? "rgba(224,122,95,0.08)"
                       : "rgba(255,255,255,0.03)",
                     border: `1px solid ${v.active
                       ? "rgba(224,122,95,0.28)"
                       : "rgba(255,255,255,0.06)"}`,
                     fontSize: 11,
                   }}>
                <div style={{ display: "flex", justifyContent: "space-between",
                                marginBottom: 4 }}>
                  <span style={{ fontFamily: "ui-monospace, monospace",
                                    color: v.active ? "#E07A5F" : "#a39d8a" }}>
                    v{v.version} {v.active && "· active"}
                  </span>
                  {!v.active && (
                    <button
                      data-testid={`hr-restore-${v.version}`}
                      onClick={() => restore(v.version)}
                      style={{ background: "transparent", border: "none",
                                color: "#E07A5F", cursor: "pointer",
                                fontSize: 11 }}>
                      Restore →
                    </button>
                  )}
                </div>
                <div style={{ color: "#c8c2b4", lineHeight: 1.5,
                                whiteSpace: "pre-wrap",
                                wordBreak: "break-word" }}>
                  {(v.rules_text || "").slice(0, 200)}
                  {(v.rules_text || "").length > 200 && "…"}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
