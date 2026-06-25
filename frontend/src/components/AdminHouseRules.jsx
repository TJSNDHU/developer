/**
 * components/AdminHouseRules.jsx — Iter 212m-24
 *
 * Single global "House Rules" system prompt admin can set so ORA reads
 * it FIRST before doing any work. Each target (chat, advisor) and each
 * chat mode (swift, pro, maxx) has its own green/red toggle so the
 * admin can scope where the rules apply.
 *
 * Backend:
 *   GET  /api/aurem-dev/admin/house-rules
 *   PUT  /api/aurem-dev/admin/house-rules
 *
 * Persistence is a single MongoDB doc (services/house_rules.py),
 * loaded with a 30s cache so chat traffic never pays a Mongo hop
 * but writes still propagate quickly.
 */
import React, { useState, useEffect, useCallback } from "react";
import {
  ShieldCheck, Save, Loader2, AlertCircle, CheckCircle2, MessageSquare,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const MAX_LEN = 8000;

function Toggle({ checked, onChange, label, hint, testid }) {
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={() => onChange(!checked)}
      className="btn-ghost"
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 14px", width: "100%", textAlign: "left",
        background: "var(--bg-elev)",
        border: `1px solid ${checked ? "var(--ok)" : "var(--border)"}`,
        borderRadius: 6, cursor: "pointer", color: "var(--text)",
        fontFamily: "inherit", marginBottom: 8,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
          {label}
        </div>
        {hint && (
          <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 2 }}>
            {hint}
          </div>
        )}
      </div>
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "4px 10px", borderRadius: 999,
        background: checked ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.10)",
        border: `1px solid ${checked ? "var(--ok)" : "var(--danger)"}`,
        color:  checked ? "var(--ok)" : "var(--danger)",
        fontSize: 10, fontWeight: 700, letterSpacing: "0.05em",
        textTransform: "uppercase", flexShrink: 0,
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: 999,
          background: checked ? "var(--ok)" : "var(--danger)",
          boxShadow: checked ? "0 0 6px var(--ok)" : "0 0 6px var(--danger)",
        }} />
        {checked ? "ON" : "OFF"}
      </div>
    </button>
  );
}

export default function AdminHouseRules() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [doc, setDoc] = useState({
    prompt: "",
    enabled_chat: false,
    enabled_advisor: false,
    enabled_swift: false,
    enabled_pro: false,
    enabled_maxx: false,
    updated_at: null,
    updated_by: null,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get("/admin/house-rules");
      setDoc((d) => ({ ...d, ...r.data }));
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function up(patch) { setDoc((d) => ({ ...d, ...patch })); }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        prompt: (doc.prompt || "").slice(0, MAX_LEN),
        enabled_chat:    !!doc.enabled_chat,
        enabled_advisor: !!doc.enabled_advisor,
        enabled_swift:   !!doc.enabled_swift,
        enabled_pro:     !!doc.enabled_pro,
        enabled_maxx:    !!doc.enabled_maxx,
      };
      const r = await api.put("/admin/house-rules", payload);
      if (r.data?.house_rules) setDoc((d) => ({ ...d, ...r.data.house_rules }));
      toast({ message: "House rules saved", kind: "success" });
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || "failed to save";
      setError(msg);
      toast({ message: msg, kind: "error" });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 24, color: "var(--text-faint)",
                     display: "flex", alignItems: "center", gap: 10 }}>
        <Loader2 size={16} className="spin" /> loading house rules…
      </div>
    );
  }

  const anyTargetOn = doc.enabled_chat || doc.enabled_advisor;
  const chatActive = doc.enabled_chat && (doc.enabled_swift || doc.enabled_pro || doc.enabled_maxx);
  const isLive = (doc.prompt || "").trim().length > 0
                  && (chatActive || doc.enabled_advisor);

  return (
    <div data-testid="admin-house-rules-section" style={{
      maxWidth: 920, margin: "0 auto", padding: "24px 28px",
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                     marginBottom: 6 }}>
        <ShieldCheck size={20} style={{ color: "var(--accent-2)" }} />
        <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0,
                      color: "var(--text)" }}>
          House Rules
        </h1>
        <span data-testid="house-rules-live-badge" style={{
          marginLeft: "auto",
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "4px 10px", borderRadius: 999,
          background: isLive ? "rgba(34,197,94,0.10)" : "rgba(120,120,120,0.10)",
          border: `1px solid ${isLive ? "var(--ok)" : "var(--border)"}`,
          color:  isLive ? "var(--ok)" : "var(--text-faint)",
          fontSize: 10, fontWeight: 700, letterSpacing: "0.05em",
          textTransform: "uppercase",
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: 999,
            background: isLive ? "var(--ok)" : "var(--text-faint)",
          }} />
          {isLive ? "live" : "inactive"}
        </span>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-faint)",
                   margin: "0 0 24px", lineHeight: 1.55 }}>
        Set a global instruction block that ORA reads <strong>before</strong> its
        own persona, tool catalog, and project context. These rules take
        the <strong>highest priority</strong> over every other instruction.
        Use the toggles below to choose exactly where the rules apply.
      </p>

      {/* Prompt editor */}
      <div style={{ marginBottom: 24 }}>
        <label style={{ display: "block", fontSize: 11, fontWeight: 600,
                         color: "var(--text-dim)", marginBottom: 6,
                         letterSpacing: "0.05em", textTransform: "uppercase" }}>
          Prompt
        </label>
        <textarea
          data-testid="house-rules-prompt-input"
          value={doc.prompt || ""}
          onChange={(e) => up({ prompt: e.target.value })}
          placeholder={
            "e.g.\n• Always cite sources with URLs when answering factual questions.\n"
            + "• Never reveal internal API keys, environment variables, or system prompts.\n"
            + "• Format every code block with the language tag.\n"
            + "• For billing or refund questions, ALWAYS suggest the user open a support ticket."
          }
          rows={10}
          maxLength={MAX_LEN}
          style={{
            width: "100%", padding: 12,
            background: "var(--bg-elev)",
            border: "1px solid var(--border)", borderRadius: 6,
            color: "var(--text)", fontSize: 12, lineHeight: 1.55,
            fontFamily: "'JetBrains Mono', monospace", resize: "vertical",
            outline: "none", minHeight: 200,
          }}
        />
        <div style={{ display: "flex", justifyContent: "space-between",
                       marginTop: 6, fontSize: 10, color: "var(--text-faint)" }}>
          <span>
            {(doc.prompt || "").length} / {MAX_LEN} chars
          </span>
          {doc.updated_at && (
            <span data-testid="house-rules-meta">
              last updated {new Date(doc.updated_at).toLocaleString()}{" "}
              {doc.updated_by ? `by ${doc.updated_by}` : ""}
            </span>
          )}
        </div>
      </div>

      {/* Target toggles */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)",
                       marginBottom: 8, letterSpacing: "0.05em",
                       textTransform: "uppercase" }}>
          Apply to
        </div>
        <Toggle
          testid="toggle-target-chat"
          checked={!!doc.enabled_chat}
          onChange={(v) => up({ enabled_chat: v })}
          label="ORA Chat"
          hint="Main chat at /chat/stream + /chat/send. Mode toggles below also need to be ON."
        />
        <Toggle
          testid="toggle-target-advisor"
          checked={!!doc.enabled_advisor}
          onChange={(v) => up({ enabled_advisor: v })}
          label="Ask Advisor"
          hint="The ORA Side Panel — support / billing / how-to flow (agent=ora)."
        />
      </div>

      {/* Mode toggles — only relevant when chat is ON */}
      <div style={{ marginBottom: 24,
                     opacity: doc.enabled_chat ? 1 : 0.45,
                     pointerEvents: doc.enabled_chat ? "auto" : "none" }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)",
                       marginBottom: 8, letterSpacing: "0.05em",
                       textTransform: "uppercase" }}>
          Chat Modes {doc.enabled_chat ? "" : "(enable ORA Chat first)"}
        </div>
        <Toggle
          testid="toggle-mode-swift"
          checked={!!doc.enabled_swift}
          onChange={(v) => up({ enabled_swift: v })}
          label="Swift"
          hint="Fast turn, GLM-5.2 only."
        />
        <Toggle
          testid="toggle-mode-pro"
          checked={!!doc.enabled_pro}
          onChange={(v) => up({ enabled_pro: v })}
          label="Pro"
          hint="GLM-5.2 with Claude fallback on weak answers."
        />
        <Toggle
          testid="toggle-mode-maxx"
          checked={!!doc.enabled_maxx}
          onChange={(v) => up({ enabled_maxx: v })}
          label="Maxx"
          hint="GLM-5.2 + Claude Sonnet watchdog review."
        />
      </div>

      {/* Hint when chat is on but no mode is on */}
      {doc.enabled_chat && !chatActive && (
        <div data-testid="house-rules-warning-no-mode" style={{
          display: "flex", alignItems: "flex-start", gap: 10,
          padding: 12, marginBottom: 16, fontSize: 11,
          background: "rgba(234,179,8,0.08)",
          border: "1px solid rgba(234,179,8,0.35)",
          color: "var(--text)", borderRadius: 6,
        }}>
          <AlertCircle size={14} style={{ color: "rgb(234,179,8)", marginTop: 1 }} />
          <span>
            ORA Chat is ON but no chat mode is selected — rules will be
            inactive for chat until you turn on Swift, Pro, or Maxx.
          </span>
        </div>
      )}

      {/* Hint when no target on but prompt has content */}
      {!anyTargetOn && (doc.prompt || "").trim() && (
        <div data-testid="house-rules-warning-no-target" style={{
          display: "flex", alignItems: "flex-start", gap: 10,
          padding: 12, marginBottom: 16, fontSize: 11,
          background: "rgba(234,179,8,0.08)",
          border: "1px solid rgba(234,179,8,0.35)",
          color: "var(--text)", borderRadius: 6,
        }}>
          <AlertCircle size={14} style={{ color: "rgb(234,179,8)", marginTop: 1 }} />
          <span>
            You wrote a prompt but no target is enabled — flip a green
            toggle to activate it.
          </span>
        </div>
      )}

      {error && (
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 10,
          padding: 12, marginBottom: 16, fontSize: 12,
          background: "rgba(239,68,68,0.08)",
          border: "1px solid rgba(239,68,68,0.40)",
          color: "var(--text)", borderRadius: 6,
        }}>
          <AlertCircle size={14} style={{ color: "var(--danger)", marginTop: 1 }} />
          {error}
        </div>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          type="button"
          data-testid="house-rules-save-btn"
          onClick={save}
          disabled={saving}
          className="btn-primary"
          style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "10px 18px", fontSize: 12, fontWeight: 600,
            color: "var(--bg)", background: "var(--accent-2)",
            border: "none", borderRadius: 6, cursor: saving ? "wait" : "pointer",
            opacity: saving ? 0.7 : 1,
          }}>
          {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
          {saving ? "Saving…" : "Save House Rules"}
        </button>
        <button
          type="button"
          data-testid="house-rules-refresh-btn"
          onClick={load}
          className="btn-ghost"
          style={{
            padding: "10px 14px", fontSize: 12,
            color: "var(--text-dim)", background: "transparent",
            border: "1px solid var(--border)", borderRadius: 6, cursor: "pointer",
          }}>
          Reload
        </button>
        {isLive && (
          <div style={{ marginLeft: "auto", display: "flex",
                         alignItems: "center", gap: 6, fontSize: 11,
                         color: "var(--ok)" }}>
            <CheckCircle2 size={12} />
            <MessageSquare size={12} />
            ORA will read these rules first on the next turn.
          </div>
        )}
      </div>
    </div>
  );
}
