/**
 * pages/AdminIntegrations.jsx — Live integration health dashboard.
 *
 * Hits `/api/aurem-dev/admin/integrations/health` (cached snapshot)
 * on mount, and `/admin/integrations/refresh` (real-time re-probe of
 * every external provider) when the founder clicks "Refresh now".
 *
 * Cards show one provider each: live status, last-check latency,
 * what's wrong (if anything), and a one-click "fix this" deeplink.
 *
 * Auto-refreshes once every 60s via polling so a long-open tab stays
 * current without manual refresh.
 */
import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, getToken } from "../lib/api";

const STATUS_META = {
  ok:      { color: "#6dd4a1", bg: "rgba(109,212,161,0.10)", label: "Live"    },
  warn:    { color: "#ffb454", bg: "rgba(255,180,84,0.10)",  label: "Degraded"},
  broken:  { color: "#ff6b6b", bg: "rgba(255,107,107,0.10)", label: "Broken"  },
  missing: { color: "#888d99", bg: "rgba(136,141,153,0.10)", label: "Missing" },
};

function timeSince(epoch) {
  if (!epoch) return "never";
  const s = Math.max(1, Math.round(Date.now() / 1000 - epoch));
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function AdminIntegrations() {
  const nav = useNavigate();
  const [snap, setSnap]       = useState(null);
  const [busy, setBusy]       = useState(false);
  const [err, setErr]         = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      const r = await api.get("/admin/integrations/health", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setSnap(r.data);
    } catch (e) {
      const detail = (e && e.response && e.response.data && e.response.data.detail) || "";
      setErr(detail || (e && e.message) || "Failed to load integrations.");
    }
  }, []);

  const refresh = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/admin/integrations/refresh", {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setSnap(r.data);
    } catch (e) {
      const detail = (e && e.response && e.response.data && e.response.data.detail) || "";
      setErr(detail || (e && e.message) || "Refresh failed.");
    } finally {
      setBusy(false);
    }
  }, [busy]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 60_000);  // poll cached snapshot every 60s
    return () => clearInterval(iv);
  }, [load]);

  const results = (snap && snap.results) || [];
  const summary = (snap && snap.summary) || {ok: 0, warn: 0, broken: 0, missing: 0, total: 0};
  const generated = snap && snap.generated_at;
  const trigger = (snap && snap.trigger) || "—";

  return (
    <div data-testid="admin-integrations-page" style={{
      maxWidth: 1200, margin: "0 auto", padding: "32px 24px",
      color: "var(--text, #e8e3d3)",
    }}>
      <button
        data-testid="admin-back"
        onClick={() => nav("/admin/overview")}
        style={{
          fontSize: 11, color: "var(--text-dim)",
          background: "transparent", border: "none",
          cursor: "pointer", marginBottom: 16,
        }}
      >← Back to admin</button>

      <header style={{ marginBottom: 24 }}>
        <h1 style={{
          fontSize: 28, fontWeight: 500, letterSpacing: "-0.02em",
          margin: 0, color: "var(--text)",
        }}>Integration Health Center</h1>
        <p style={{
          fontSize: 13, color: "var(--text-dim)", marginTop: 6,
        }}>
          Real-time probes of every external dependency. Auto-refreshes
          daily at <code>DIGEST_HOUR_UTC</code>; manual refresh below.
        </p>
      </header>

      {/* Summary band */}
      <div data-testid="health-summary" style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
        gap: 12,
        marginBottom: 20,
      }}>
        {[
          { k: "total",   label: "Total",    color: "var(--text-dim)" },
          { k: "ok",      label: "Live",     color: STATUS_META.ok.color },
          { k: "warn",    label: "Degraded", color: STATUS_META.warn.color },
          { k: "broken",  label: "Broken",   color: STATUS_META.broken.color },
          { k: "missing", label: "Missing",  color: STATUS_META.missing.color },
        ].map(({ k, label, color }) => (
          <div key={k} data-testid={`summary-${k}`} style={{
            padding: "16px 14px",
            background: "var(--panel, #0f1219)",
            border: "1px solid var(--border, rgba(255,200,120,0.16))",
            borderRadius: 8,
          }}>
            <div style={{ fontSize: 11, color: "var(--text-faint)",
                          textTransform: "uppercase", letterSpacing: ".08em" }}>
              {label}
            </div>
            <div style={{ fontSize: 24, fontWeight: 500, color, marginTop: 4 }}>
              {summary[k] || 0}
            </div>
          </div>
        ))}
      </div>

      {/* Controls */}
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 16, fontSize: 12,
        color: "var(--text-dim)",
      }}>
        <div>
          Last refresh: <b style={{ color: "var(--text)" }}>{timeSince(generated)}</b>
          {" "}·{" "}
          Trigger: <code style={{ color: "var(--text)" }}>{trigger}</code>
        </div>
        <button
          data-testid="refresh-all-btn"
          onClick={refresh}
          disabled={busy}
          style={{
            padding: "8px 16px", fontSize: 12, fontWeight: 600,
            background: busy ? "var(--bg-elev, #0a0c10)" : "var(--accent, #ff8a2a)",
            color: busy ? "var(--text-faint)" : "var(--bg, #0a0c10)",
            border: "none", borderRadius: 5,
            cursor: busy ? "wait" : "pointer",
            letterSpacing: ".04em",
          }}
        >
          {busy ? "Probing all 11 APIs…" : "Refresh now"}
        </button>
      </div>

      {err && (
        <div data-testid="integrations-error" style={{
          fontSize: 12, padding: "10px 14px", marginBottom: 16,
          background: "rgba(255,107,107,0.06)",
          border: "1px solid rgba(255,107,107,0.2)",
          color: "var(--danger, #ff6b6b)", borderRadius: 5,
        }}>{err}</div>
      )}

      {/* Grid of integration cards */}
      {!snap && !err && (
        <div data-testid="loading" style={{ fontSize: 13, color: "var(--text-dim)" }}>
          Loading…
        </div>
      )}

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: 14,
      }}>
        {results.map((r) => {
          const meta = STATUS_META[r.status] || STATUS_META.broken;
          return (
            <div
              key={r.id}
              data-testid={`card-${r.id}`}
              data-status={r.status}
              style={{
                padding: "16px 18px",
                background: "var(--panel, #0f1219)",
                border: `1px solid ${meta.bg.replace("0.10","0.32")}`,
                borderRadius: 8,
                display: "flex", flexDirection: "column", gap: 10,
              }}
            >
              <div style={{
                display: "flex", justifyContent: "space-between",
                alignItems: "center",
              }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
                  {r.name}
                </div>
                <div style={{
                  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
                  textTransform: "uppercase",
                  color: meta.color,
                  background: meta.bg,
                  padding: "3px 8px", borderRadius: 4,
                }}>{meta.label}</div>
              </div>

              <div style={{
                fontSize: 12, color: "var(--text-dim)",
                lineHeight: 1.5, minHeight: 32,
              }}>
                {r.summary}
              </div>

              {r.detail && r.status !== "ok" && (
                <div data-testid={`detail-${r.id}`} style={{
                  fontSize: 11, color: "var(--text-faint)",
                  fontFamily: "monospace",
                  background: "var(--bg-elev, #0a0c10)",
                  padding: "6px 8px", borderRadius: 4,
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                }}>{r.detail}</div>
              )}

              {r.fix_hint && r.status !== "ok" && (
                <div data-testid={`fix-${r.id}`} style={{
                  fontSize: 11, color: meta.color,
                  borderTop: "1px solid var(--border, rgba(255,200,120,0.10))",
                  paddingTop: 8,
                }}>
                  <b>Fix:</b> {r.fix_hint}
                </div>
              )}

              <div style={{
                fontSize: 10, color: "var(--text-faint)",
                marginTop: "auto",
                display: "flex", justifyContent: "space-between",
              }}>
                <span>id: <code>{r.id}</code></span>
                <span>{r.latency_ms}ms · {timeSince(new Date(r.checked_at).getTime()/1000)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
