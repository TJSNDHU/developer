/**
 * components/VanguardConfigPanel.jsx — admin selector for per-mode
 * Vanguard verify thresholds. Mounted at the top of /admin/vanguard
 * alongside the existing audit dashboard.
 *
 *   🔴 OFF       — verify-agent skipped (regex floor still gates)
 *   🟢 CRITICAL  — recommended default; blocks only on critical
 *   🟡 HIGH      — strict; blocks on critical + high
 *
 * A master "Enabled" toggle wraps the whole agent — when OFF the LLM
 * + E2B passes are skipped entirely (regex floor remains).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const MODES = ["swift", "pro", "maxx"];
const LEVELS = [
  { value: "OFF",      dot: "#ef4444",
    blurb: "Disabled — only regex floor blocks." },
  { value: "CRITICAL", dot: "#22c55e",
    blurb: "Recommended. Blocks only on critical findings." },
  { value: "HIGH",     dot: "#f59e0b",
    blurb: "Strict. Blocks on critical + high." },
];

export default function VanguardConfigPanel() {
  const [cfg,   setCfg]   = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error,  setError]  = useState(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/admin/vanguard/config");
      setCfg(r.data.config);
      setDraft(JSON.parse(JSON.stringify(r.data.config)));
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to load config.");
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const dirty = useMemo(() => {
    if (!cfg || !draft) return false;
    if (cfg.enabled !== draft.enabled) return true;
    for (const m of MODES) {
      if ((cfg.levels?.[m] ?? "") !== (draft.levels?.[m] ?? "")) return true;
    }
    return false;
  }, [cfg, draft]);

  const setLevel = (mode, lvl) => {
    setDraft((d) => ({ ...d, levels: { ...(d.levels || {}), [mode]: lvl } }));
  };

  const save = async () => {
    setSaving(true); setError(null);
    try {
      const r = await api.post("/admin/vanguard/config", {
        enabled: !!draft.enabled,
        levels:  draft.levels || {},
      });
      setCfg(r.data.config);
      setDraft(JSON.parse(JSON.stringify(r.data.config)));
      toast("Vanguard config saved.", "success");
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to save.");
    } finally {
      setSaving(false);
    }
  };

  if (!draft) return null;

  return (
    <section data-testid="vanguard-config-panel"
             style={{
               maxWidth: 1240, margin: "0 auto 28px",
               padding: 20,
               background: "rgba(255,138,42,0.04)",
               border: "1px solid rgba(255,138,42,0.20)",
               borderRadius: 12,
               display: "flex", flexDirection: "column", gap: 16,
             }}>
      {/* Master switch row */}
      <div style={rowStyle}>
        <div>
          <div style={titleStyle}>⚙ Vanguard verify config</div>
          <div style={subStyle}>
            Per-mode severity threshold for the LLM verify-agent. Changes
            propagate within ~10 s.
          </div>
        </div>
        <button
          type="button"
          data-testid="vanguard-master-toggle"
          onClick={() => setDraft((d) => ({ ...d, enabled: !d.enabled }))}
          style={{
            padding: "8px 16px", fontSize: 12, fontWeight: 700,
            color: draft.enabled ? "#0b0b0b" : "#fff",
            background: draft.enabled ? "#22c55e" : "#ef4444",
            border: "none", borderRadius: 6, cursor: "pointer",
            minWidth: 130,
          }}>
          {draft.enabled ? "● Enabled" : "○ Disabled"}
        </button>
      </div>

      {/* Per-mode selectors */}
      <div style={{ display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                    gap: 12 }}>
        {MODES.map((mode) => (
          <ModeSelector key={mode} mode={mode}
                        current={draft.levels?.[mode] || "CRITICAL"}
                        disabled={!draft.enabled}
                        onChange={(lvl) => setLevel(mode, lvl)} />
        ))}
      </div>

      {/* Save bar */}
      <div style={{ ...rowStyle, paddingTop: 8,
                    borderTop: "1px dashed rgba(255,138,42,0.20)" }}>
        <div style={{ fontSize: 11, color: "var(--text-dim, #888)",
                       fontFamily: "'JetBrains Mono', monospace" }}>
          {cfg?.updated_at
            ? `Last changed by ${cfg.updated_by || "—"} at ${cfg.updated_at}`
            : "No previous saves — defaults active."}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="vanguard-discard"
            disabled={!dirty || saving}
            onClick={() => setDraft(JSON.parse(JSON.stringify(cfg)))}
            style={btnGhost(dirty && !saving)}>
            Discard
          </button>
          <button
            type="button"
            data-testid="vanguard-save"
            disabled={!dirty || saving}
            onClick={save}
            style={btnPrimary(dirty && !saving)}>
            {saving ? "Saving…" : dirty ? "Save" : "Saved"}
          </button>
        </div>
      </div>

      {error && (
        <div data-testid="vanguard-config-error"
             style={{ color: "#fca5a5", fontSize: 12 }}>{error}</div>
      )}
    </section>
  );
}


function ModeSelector({ mode, current, onChange, disabled }) {
  return (
    <div data-testid={`vanguard-mode-${mode}`}
         style={{
           padding: 12,
           background: "rgba(0,0,0,0.20)",
           border: "1px solid rgba(255,255,255,0.06)",
           borderRadius: 8,
           opacity: disabled ? 0.45 : 1,
           transition: "opacity 140ms ease",
         }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8,
                    marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 700,
                       letterSpacing: "0.08em",
                       color: "var(--text, #fff)" }}>
          {mode.toUpperCase()}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {LEVELS.map((lvl) => {
          const active = current === lvl.value;
          return (
            <button
              key={lvl.value}
              type="button"
              data-testid={`vanguard-${mode}-${lvl.value.toLowerCase()}`}
              disabled={disabled}
              onClick={() => onChange(lvl.value)}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "8px 12px",
                background: active ? "rgba(234,179,8,0.10)" : "transparent",
                border: active
                  ? "1px solid rgba(234,179,8,0.55)"
                  : "1px solid rgba(255,255,255,0.10)",
                borderRadius: 6,
                color: "var(--text, #ddd)",
                fontSize: 12, fontWeight: active ? 700 : 500,
                cursor: disabled ? "not-allowed" : "pointer",
                textAlign: "left",
                transition: "background 120ms ease, border-color 120ms ease",
              }}>
              <span style={{
                width: 9, height: 9, borderRadius: "50%",
                background: lvl.dot, flexShrink: 0,
                boxShadow: active ? `0 0 0 3px ${lvl.dot}33` : "none",
              }} />
              <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                <span>{lvl.value}</span>
                <span style={{ fontSize: 10, fontWeight: 400,
                               color: "var(--text-dim, #aaa)" }}>
                  {lvl.blurb}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}


// ── Shared styles ────────────────────────────────────────────────────
const rowStyle = {
  display: "flex", justifyContent: "space-between",
  alignItems: "center", gap: 16, flexWrap: "wrap",
};
const titleStyle = {
  fontSize: 14, fontWeight: 700, color: "var(--text, #fff)",
  marginBottom: 4,
};
const subStyle  = {
  fontSize: 12, color: "var(--text-dim, #aaa)", lineHeight: 1.5,
};
const btnGhost  = (enabled) => ({
  padding: "6px 12px", fontSize: 12, fontWeight: 600,
  background: "transparent", color: "var(--text, #ddd)",
  border: "1px solid var(--border, rgba(255,255,255,0.16))",
  borderRadius: 6,
  cursor: enabled ? "pointer" : "not-allowed",
  opacity: enabled ? 1 : 0.5,
});
const btnPrimary = (enabled) => ({
  padding: "6px 16px", fontSize: 12, fontWeight: 700,
  background: "#facc15", color: "#0b0b0b",
  border: "none", borderRadius: 6,
  cursor: enabled ? "pointer" : "not-allowed",
  opacity: enabled ? 1 : 0.55,
});
