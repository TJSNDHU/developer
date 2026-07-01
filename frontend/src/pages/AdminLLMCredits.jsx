/**
 * pages/AdminLLMCredits.jsx — Iter 212m-171
 *
 * Dedicated LLM Provider Status page + reusable card.  Reads from
 * /admin/llm-credits every 5 minutes.  Shows OpenRouter balance,
 * per-provider status, LongCat live flag, circuit breaker state, and
 * a threshold-based alert configurator.
 */
import React, { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import { RefreshCw, AlertTriangle, Loader2 } from "lucide-react";

const STATUS_COLOR = {
  ok:       { fg: "#4ade80", bg: "#15803d20", dot: "●" },
  fallback: { fg: "#fbbf24", bg: "#d9770620", dot: "⚠" },
  error:    { fg: "#f87171", bg: "#dc262620", dot: "✗" },
};

export function LLMCreditMonitor({ compact = false }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [threshold, setThreshold] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.get("/admin/llm-credits");
      setData(r.data);
      setThreshold(String(r.data.threshold_usd ?? 5.0));
    } catch {
      setData({ ok: false });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60 * 1000);  // 5 min
    return () => clearInterval(t);
  }, [load]);

  const saveThreshold = async () => {
    const v = parseFloat(threshold);
    if (isNaN(v) || v <= 0) return;
    try {
      await api.post("/admin/llm-credit-alert", { threshold: v });
      toast({ message: `Alert threshold set to $${v}`, kind: "success" });
    } catch { toast({ message: "Save failed", kind: "error" }); }
  };

  const bal = data?.openrouter?.balance_usd;
  const belowThreshold = bal !== undefined && bal !== null
    && bal < parseFloat(threshold || "5");

  return (
    <div data-testid="llm-credit-monitor" style={{
      background: "var(--panel-2)", border: "1px solid var(--border)",
      borderRadius: 4, padding: 16, marginBottom: 16,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                     marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                       textTransform: "uppercase", letterSpacing: "0.08em" }}>
          LLM Provider Status
        </div>
        <button data-testid="llm-credits-refresh" onClick={load} disabled={busy}
                style={{ background: "transparent",
                         border: "1px solid var(--border)",
                         color: "var(--text-dim)", padding: "3px 8px",
                         borderRadius: 3, cursor: "pointer", fontSize: 10,
                         display: "flex", alignItems: "center", gap: 4 }}>
          {busy ? <Loader2 size={10} className="spin" /> : <RefreshCw size={10} />}
          Refresh
        </button>
        {data?.last_checked && (
          <div style={{ marginLeft: "auto", fontSize: 10,
                        color: "var(--text-faint)" }}>
            {new Date(data.last_checked).toLocaleTimeString()}
          </div>
        )}
      </div>

      {!data ? (
        <div style={{ color: "var(--text-faint)", fontSize: 11 }}>Loading…</div>
      ) : (
        <>
          <div style={{ display: "grid",
                        gridTemplateColumns: compact ? "1fr" : "180px 100px 1fr",
                        rowGap: 6, columnGap: 12, fontSize: 12 }}>
            {(data.providers || []).map((p) => {
              const c = STATUS_COLOR[p.status] || STATUS_COLOR.error;
              return (
                <React.Fragment key={p.id}>
                  <div style={{ color: "var(--text)", fontWeight: 500 }}>
                    {p.label}
                  </div>
                  <div style={{ color: c.fg, fontSize: 11 }}>
                    {p.balance_usd !== undefined && p.balance_usd !== null
                      ? `$${p.balance_usd.toFixed(2)}`
                      : (p.status === "ok" ? "API OK"
                         : p.status === "fallback" ? "FALLBACK"
                         : "ERROR")}
                  </div>
                  <div style={{ color: c.fg, fontSize: 11 }}
                       data-testid={`llm-status-${p.id}`}>
                    <span style={{ marginRight: 4 }}>{c.dot}</span>
                    {p.detail || (p.status === "ok" ? "Connected" : p.status)}
                  </div>
                </React.Fragment>
              );
            })}
          </div>

          <div style={{ marginTop: 16, padding: 10,
                        background: belowThreshold ? "#dc262615" : "var(--bg-elev)",
                        border: `1px solid ${belowThreshold ? "#dc262640" : "var(--border)"}`,
                        borderRadius: 3,
                        display: "flex", alignItems: "center", gap: 10 }}>
            {belowThreshold && (
              <AlertTriangle size={14} color="#f87171" />
            )}
            <div style={{ fontSize: 11, color: belowThreshold ? "#f87171" : "var(--text-dim)" }}>
              {belowThreshold
                ? `Alert: OpenRouter balance $${bal.toFixed(2)} < $${threshold}`
                : `Alert threshold`}
            </div>
            <input
              data-testid="llm-threshold-input"
              type="number" step="0.5" min="0.5"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              style={{
                width: 70, padding: "3px 6px",
                background: "var(--panel-2)",
                border: "1px solid var(--border)",
                color: "var(--text)", fontSize: 11, borderRadius: 3,
              }} />
            <button data-testid="llm-threshold-save" onClick={saveThreshold}
                    style={{ padding: "3px 8px", fontSize: 10,
                             background: "var(--accent, #ff8a2a)",
                             color: "#0a0c10", border: "none",
                             borderRadius: 3, cursor: "pointer" }}>
              Save
            </button>
          </div>

          <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-faint)" }}>
            Circuit breaker: <span style={{ color: "var(--text-dim)" }}>
              {data.circuit_breaker || "unknown"}
            </span>
            {data.linters_missing?.length > 0 && (
              <>{" · "}Linters missing: <span style={{ color: "#fbbf24" }}>
                {data.linters_missing.join(", ")}
              </span></>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function AdminLLMCredits() {
  return (
    <div style={{ padding: "24px 20px", maxWidth: 900 }}>
      <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0,
                   color: "var(--text)", marginBottom: 16 }}>
        LLM Credits
      </h1>
      <LLMCreditMonitor />
    </div>
  );
}
