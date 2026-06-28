/**
 * VercelCard.jsx — Iter 212m-84
 *
 * Settings → Integrations → Vercel card.
 *
 * Shows the connection status (account + plan) for the shared
 * `VERCEL_API_TOKEN` mode, lists the 8 ORA-available Vercel tools,
 * and offers a "try a tool" dropdown so the founder can verify the
 * integration without going through chat.
 *
 * Architectural note: built so it transparently swaps to per-user
 * OAuth 2.1 + PKCE (mcp.vercel.com) once a Vercel OAuth integration
 * is registered. Until then, the card surfaces `mode: "shared-token"`
 * with a small banner explaining the limitation.
 */
import React, { useEffect, useMemo, useState } from "react";
import { Cloud, Check, AlertCircle, Play, ChevronDown, Lock } from "lucide-react";
import { api } from "../lib/api";

const PLAN_COLORS = {
  hobby: "var(--text-dim)",
  pro:   "var(--accent, #FF6608)",
  enterprise: "#9b59ff",
};

export default function VercelCard() {
  const [status, setStatus] = useState(null);
  const [tools, setTools]   = useState([]);
  const [audit, setAudit]   = useState([]);
  const [tool, setTool]     = useState("vercel_list_projects");
  const [args, setArgs]     = useState("{}");
  const [busy, setBusy]     = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr]       = useState("");

  const refresh = async () => {
    try {
      const [s, t, a] = await Promise.all([
        api.get("/integrations/vercel/status"),
        api.get("/integrations/vercel/tools"),
        api.get("/integrations/vercel/audit?limit=8"),
      ]);
      setStatus(s.data);
      setTools(t.data?.tools || []);
      setAudit(a.data?.entries || []);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Failed to load Vercel status");
    }
  };

  useEffect(() => { refresh(); }, []);

  const planColor = useMemo(() => {
    const p = (status?.account?.plan || "").toLowerCase();
    return PLAN_COLORS[p] || "var(--text-dim)";
  }, [status]);

  const runTool = async () => {
    setBusy(true); setResult(null); setErr("");
    let parsed = {};
    try { parsed = args.trim() ? JSON.parse(args) : {}; }
    catch { setErr("Invalid JSON in args"); setBusy(false); return; }
    try {
      const r = await api.post("/integrations/vercel/execute",
                               { tool, args: parsed });
      setResult(r.data);
      // refresh audit panel
      const a = await api.get("/integrations/vercel/audit?limit=8");
      setAudit(a.data?.entries || []);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Execute failed");
    } finally { setBusy(false); }
  };

  return (
    <section className="card" data-testid="settings-vercel"
             style={{ gridColumn: "1 / -1" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <Cloud size={16} style={{ color: "var(--accent, #FF6608)" }} />
        <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0 }}>
          Vercel · ORA platform tools
        </h3>
        {status?.connected ? (
          <span data-testid="vercel-status-connected"
            style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              fontSize: 10, padding: "2px 8px", borderRadius: 999,
              background: "rgba(34,197,94,0.12)", color: "#22c55e",
              fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.05em",
            }}>
            <Check size={10} /> CONNECTED
          </span>
        ) : (
          <span data-testid="vercel-status-disconnected"
            style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              fontSize: 10, padding: "2px 8px", borderRadius: 999,
              background: "rgba(239,68,68,0.12)", color: "#ef4444",
              fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.05em",
            }}>
            <AlertCircle size={10} /> NOT CONNECTED
          </span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{
          fontSize: 10, color: "var(--text-faint)",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          mode: {status?.mode || "—"}
        </span>
      </div>

      {/* Account block */}
      {status?.connected && status.account && (
        <div data-testid="vercel-account-block" style={{
          fontSize: 12, marginBottom: 14,
          padding: "10px 12px", borderRadius: 6,
          background: "rgba(255,102,8,0.04)",
          border: "1px solid rgba(255,102,8,0.16)",
          display: "grid", gridTemplateColumns: "auto 1fr", gap: 6,
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          <span style={{ color: "var(--text-faint)" }}>account</span>
          <span style={{ color: "var(--text)" }}>
            {status.account.email} ({status.account.username})
          </span>
          <span style={{ color: "var(--text-faint)" }}>plan</span>
          <span style={{ color: planColor, textTransform: "uppercase",
                         letterSpacing: "0.08em" }}>
            {status.account.plan || "—"}
          </span>
          <span style={{ color: "var(--text-faint)" }}>tools</span>
          <span style={{ color: "var(--text)" }}>
            {status.tool_count} available to ORA chat
          </span>
        </div>
      )}

      {!status?.connected && (
        <div style={{
          fontSize: 12, marginBottom: 12, color: "#ef4444",
          padding: "8px 12px", borderRadius: 6,
          background: "rgba(239,68,68,0.06)",
          border: "1px solid rgba(239,68,68,0.2)",
        }}>
          {status?.reason || "Vercel not connected. Set VERCEL_API_TOKEN to enable."}
        </div>
      )}

      {/* Tool catalogue */}
      {tools.length > 0 && (
        <details data-testid="vercel-tool-catalogue">
          <summary style={{
            cursor: "pointer", fontSize: 12, color: "var(--text-dim)",
            padding: "6px 0", userSelect: "none",
            display: "inline-flex", alignItems: "center", gap: 6,
          }}>
            <ChevronDown size={12} />
            <span>{tools.length} tools ORA can call</span>
          </summary>
          <ul style={{
            listStyle: "none", padding: 0, margin: "8px 0 14px",
            display: "grid", gap: 6,
            fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
          }}>
            {tools.map((t) => (
              <li key={t.name} style={{
                padding: "6px 10px", borderRadius: 4,
                background: "var(--bg-2, rgba(255,255,255,0.02))",
                border: "1px solid var(--border)",
              }}>
                <div style={{ color: "var(--accent, #FF6608)", marginBottom: 3 }}>
                  {t.name}
                </div>
                <div style={{ color: "var(--text-dim)", lineHeight: 1.55 }}>
                  {t.description}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Try-it row */}
      {status?.connected && (
        <div data-testid="vercel-try-it"
             style={{ marginTop: 12, paddingTop: 12,
                      borderTop: "1px solid var(--border)" }}>
          <div style={{
            fontSize: 10, color: "var(--text-faint)",
            textTransform: "uppercase", letterSpacing: "0.15em",
            marginBottom: 8, fontFamily: "'JetBrains Mono', monospace",
          }}>try a tool</div>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
            <select
              data-testid="vercel-tool-select"
              value={tool}
              onChange={(e) => setTool(e.target.value)}
              style={{
                flex: "0 0 240px", padding: "8px 10px", fontSize: 12,
                background: "var(--bg-2, rgba(255,255,255,0.04))",
                color: "var(--text)", border: "1px solid var(--border)",
                borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              {tools.map((t) => (
                <option key={t.name} value={t.name}>{t.name}</option>
              ))}
            </select>
            <input
              data-testid="vercel-tool-args"
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              placeholder='{"limit": 5}'
              style={{
                flex: 1, padding: "8px 10px", fontSize: 12,
                background: "var(--bg-2, rgba(255,255,255,0.04))",
                color: "var(--text)", border: "1px solid var(--border)",
                borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
              }}
            />
            <button
              data-testid="vercel-tool-run"
              onClick={runTool}
              disabled={busy}
              style={{
                padding: "8px 14px", fontSize: 12,
                background: "var(--accent, #FF6608)",
                color: "#111", border: 0, borderRadius: 4,
                cursor: busy ? "wait" : "pointer", fontWeight: 600,
                display: "inline-flex", alignItems: "center", gap: 6,
                opacity: busy ? 0.6 : 1,
              }}
            >
              <Play size={12} /> {busy ? "Running…" : "Run"}
            </button>
          </div>
          {err && (
            <div style={{
              marginTop: 8, fontSize: 11, color: "#ef4444",
              fontFamily: "'JetBrains Mono', monospace",
            }}>⚠ {err}</div>
          )}
          {result && (
            <pre data-testid="vercel-tool-result" style={{
              marginTop: 10, maxHeight: 280, overflow: "auto",
              padding: 12, borderRadius: 4, fontSize: 11,
              background: "var(--bg, #0c0c0c)", color: "var(--text-dim)",
              border: "1px solid var(--border)",
              fontFamily: "'JetBrains Mono', monospace",
              whiteSpace: "pre-wrap",
            }}>{JSON.stringify(result?.data, null, 2)}</pre>
          )}
        </div>
      )}

      {/* Audit log */}
      {audit.length > 0 && (
        <div data-testid="vercel-audit" style={{ marginTop: 14 }}>
          <div style={{
            fontSize: 10, color: "var(--text-faint)",
            textTransform: "uppercase", letterSpacing: "0.15em",
            marginBottom: 6, fontFamily: "'JetBrains Mono', monospace",
          }}>recent tool calls</div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0,
                       display: "grid", gap: 4 }}>
            {audit.map((e, i) => (
              <li key={i} style={{
                fontSize: 11, color: "var(--text-dim)",
                fontFamily: "'JetBrains Mono', monospace",
                padding: "4px 0", borderBottom: "1px solid var(--border)",
                display: "flex", gap: 10, alignItems: "baseline",
              }}>
                <span style={{
                  color: e.status === "ok" ? "#22c55e" : "#ef4444",
                  fontSize: 9, textTransform: "uppercase",
                  letterSpacing: "0.1em", width: 50,
                }}>{e.status}</span>
                <span style={{ color: "var(--accent, #FF6608)" }}>{e.tool}</span>
                <span style={{ color: "var(--text-faint)", flex: 1,
                               overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>
                  {e.summary || "—"}
                </span>
                <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
                  {String(e.created_at || "").slice(11, 19)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Footer — architectural hint */}
      <div style={{
        marginTop: 14, paddingTop: 10, borderTop: "1px solid var(--border)",
        fontSize: 10, color: "var(--text-faint)",
        display: "flex", alignItems: "center", gap: 6,
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        <Lock size={10} />
        shared-token mode · per-user OAuth 2.1 via mcp.vercel.com coming next
      </div>
    </section>
  );
}
